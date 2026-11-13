"""Synthetic file lifecycle: reversible filing, stable evidence links and export."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "family-doctor" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import file_lifecycle as lifecycle
import file_catalog as catalog
from consultation_episodes import merge_episodes, split_report


class FileLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="file-lifecycle-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.health = self.base / "member" / "health-records"
        self.health.mkdir(parents=True)
        self.profile_path = self.health / "user-profile.json"
        for module in (lifecycle, catalog):
            for key, value in (("HEALTH_DIR", self.health), ("USER_PROFILE", self.profile_path)):
                if hasattr(module, key):
                    patcher = mock.patch.object(module, key, value)
                    patcher.start()
                    self.addCleanup(patcher.stop)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("FAMILY_DOCTOR_", "HERMES_SESSION_"))}
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.member = "m_" + "a" * 32
        self.doc_id = str(uuid.uuid4())
        self.alias_id = str(uuid.uuid4())
        self.raw = b"Synthetic laboratory document only: 1.2 mg/L"
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.raw_path = self.health / "raw" / (self.digest + ".txt")
        self.raw_path.parent.mkdir()
        self.raw_path.write_bytes(self.raw)
        self.report1 = self.source("cons_" + "1" * 32, "张父", "self")
        self.report2 = self.source("cons_" + "2" * 32, "张女", "proxy")
        self.episode1 = self.episode("episode_" + "1" * 32, [self.report1["id"]])
        self.episode2 = self.episode("episode_" + "2" * 32, [self.report2["id"]])
        self.document = {"id": self.doc_id, "status": "confirmed", "role": "self",
            "subject_member_id": self.member, "reporter_name": "张女", "subject_name": "张父",
            "original_name": "合成检查.txt", "document_type": "检验结果", "source_type": "document",
            "raw_file": "raw/" + self.raw_path.name, "sha256": self.digest, "confirmed_at": "2026-11-11T09:00:00+08:00",
            "fields": [{"name": "合成指标", "value": 1.2, "unit": "mg/L", "observed_at": "2026-11-01"}],
            "upload_sources": [{"import_id": self.doc_id, "original_name": "合成检查.txt", "reporter_name": "张女"},
                               {"import_id": self.alias_id, "original_name": "再次上传.txt", "reporter_name": "张父"}]}
        self.data = {"owner": {"member_id": self.member, "name": "张父"},
                     "records": {"检验结果": [self.document]}, "consultations": [self.report1, self.report2],
                     "consultation_episodes": [self.episode1, self.episode2], "summary": {}}
        self.save(self.data)

    def source(self, identifier, name, role):
        path = self.health / "consultations" / (identifier + ".md")
        path.parent.mkdir(exist_ok=True)
        path.write_text("# 合成来源\n提供者：" + name + "\n", encoding="utf-8")
        return {"id": identifier, "subject_member_id": self.member, "patient": "张父",
                "reporter_name": name, "role": role, "report_mode": role,
                "recordedAt": "2026-11-11T09:00:00+08:00", "date": "2026-11-11",
                "chiefComplaint": "合成症状", "symptoms": {}, "riskLevel": "UNKNOWN",
                "source": {"type": "user_report"}, "file": "consultations/" + path.name}

    def episode(self, identifier, refs):
        return {"id": identifier, "subject_member_id": self.member, "report_ids": refs,
                "status": "separate", "revision": 1, "candidate_episode_ids": [], "merge_basis": []}

    def save(self, data):
        self.profile_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def read(self):
        return json.loads(self.profile_path.read_text(encoding="utf-8"))

    def test_archive_restore_only_changes_visibility_and_uses_canonical_alias(self):
        before_sources = {r["file"]: (self.health / r["file"]).read_bytes() for r in self.data["consultations"]}
        archived = lifecycle.archive_item("document:" + self.alias_id, "整理已处理资料")
        self.assertEqual(archived["item_id"], "document:" + self.doc_id)
        self.assertTrue(archived["archived"])
        self.assertEqual(self.read()["records"], self.data["records"])
        lifecycle.archive_item("document:" + self.doc_id, "重复操作")
        self.assertEqual(len(self.read()["file_management"]["history"]), 1)
        restored = lifecycle.restore_item("document:" + self.alias_id, "重新查看")
        self.assertFalse(restored["archived"])
        self.assertEqual(len(self.read()["file_management"]["history"]), 2)
        self.assertEqual(self.raw_path.read_bytes(), self.raw)
        for path, expected in before_sources.items():
            self.assertEqual((self.health / path).read_bytes(), expected)

    def test_link_idempotence_and_archive_do_not_hide_clinical_evidence(self):
        first = lifecycle.link_document(self.doc_id, self.episode1["id"], "本次就诊的检查")
        self.assertFalse(first["already_linked"])
        second = lifecycle.link_document("document:" + self.alias_id, "episode:" + self.episode1["id"], "相同关联")
        self.assertTrue(second["already_linked"])
        lifecycle.archive_item("document:" + self.doc_id, "已处理")
        related = lifecycle.documents_for_episode(self.read(), self.episode1)
        self.assertEqual(len(related), 1)
        self.assertTrue(related[0]["archived"])
        self.assertEqual(related[0]["link_basis"], "本次就诊的检查")
        self.assertEqual(related[0]["linked_report_ids"], [self.report1["id"]])

    def test_links_follow_sources_through_merge_and_split(self):
        lifecycle.link_document(self.doc_id, self.episode1["id"], "仅与本人最初这份描述有关")
        data = self.read()
        combined = merge_episodes(data, [self.episode1["id"], self.episode2["id"]], "核实同一次症状")
        self.assertEqual(len(lifecycle.documents_for_episode(data, combined)), 1)
        split = split_report(data, self.report1["id"], "原始本人描述属于另一次症状")
        self.assertEqual(len(lifecycle.documents_for_episode(data, split)), 1)
        remaining = next(e for e in data["consultation_episodes"] if e["id"] == self.episode1["id"])
        self.assertEqual(lifecycle.documents_for_episode(data, remaining), [])

    def test_unlink_one_split_part_keeps_other_source_links(self):
        data = self.read()
        combined = merge_episodes(data, [self.episode1["id"], self.episode2["id"]], "核实同一事件")
        self.save(data)
        lifecycle.link_document(self.doc_id, combined["id"], "涉及两份来源")
        data = self.read()
        split = split_report(data, self.report1["id"], "区分另一次")
        self.save(data)
        lifecycle.unlink_document(self.doc_id, split["id"], "这份检查不涉及该次")
        current = self.read()
        self.assertEqual(lifecycle.documents_for_episode(current, split), [])
        self.assertEqual(len(lifecycle.documents_for_episode(current, combined["id"])), 1)
        self.assertEqual(lifecycle.unlink_document(self.doc_id, split["id"], "再次核对")["unlinked"], 0)

    def test_wrong_subject_and_unknown_ids_do_not_modify_profile(self):
        for location in ("document", "episode", "source"):
            data = copy.deepcopy(self.data)
            target = data["records"]["检验结果"][0] if location == "document" else data["consultation_episodes"][0] if location == "episode" else data["consultations"][0]
            target["subject_member_id"] = "m_" + "b" * 32
            self.save(data)
            before = self.profile_path.read_bytes()
            with self.assertRaises(ValueError):
                lifecycle.link_document(self.doc_id, self.episode1["id"], "不能串档")
            self.assertEqual(self.profile_path.read_bytes(), before)
        self.save(self.data)
        with self.assertRaises(ValueError):
            lifecycle.archive_item("document:" + str(uuid.uuid4()), "无此资料")
        with self.assertRaises(ValueError):
            lifecycle.link_document(self.doc_id, self.episode1["id"], "")

    def test_export_is_self_contained_and_hashes_every_payload(self):
        lifecycle.link_document(self.doc_id, self.episode1["id"], "此次检查")
        lifecycle.archive_item("document:" + self.doc_id, "已整理")
        data = self.read()
        data["consultation_episodes"][0]["candidate_episode_ids"] = [self.episode2["id"]]
        self.save(data)
        output = self.base / "exports" / "selected.zip"
        result = lifecycle.export_episode("episode:" + self.episode1["id"], output)
        self.assertEqual(result["source_count"], 1)
        self.assertEqual(result["document_count"], 1)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        with zipfile.ZipFile(output) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["member"]["name"], "张父")
            self.assertEqual(set(archive.namelist()), {entry["path"] for entry in manifest["files"]} | {"manifest.json"})
            for entry in manifest["files"]:
                value = archive.read(entry["path"])
                self.assertEqual(hashlib.sha256(value).hexdigest(), entry["sha256"])
                self.assertEqual(len(value), entry["size"])
            event = archive.read("event.md").decode()
            self.assertIn("](consultations/" + self.report1["id"] + ".md)", event)
            self.assertNotIn("](episode_", event)
            self.assertIn("已归档，仍保留为依据", event)
            self.assertNotIn("consultations/" + self.report2["id"] + ".md", archive.namelist())
            metadata = json.loads(archive.read("documents/" + self.doc_id + "/metadata.json"))
            self.assertEqual(len(metadata["upload_sources"]), 2)
            self.assertEqual(archive.read("documents/" + self.doc_id + "/original.txt"), self.raw)

    def test_export_rejects_corruption_missing_source_and_unsafe_destinations(self):
        lifecycle.link_document(self.doc_id, self.episode1["id"], "本次检查")
        output = self.base / "test.zip"
        self.raw_path.write_bytes(b"altered")
        with self.assertRaises(ValueError):
            lifecycle.export_episode(self.episode1["id"], output)
        self.assertFalse(output.exists())
        self.raw_path.write_bytes(self.raw)
        source = self.health / self.report1["file"]
        original = source.read_bytes()
        source.unlink()
        with self.assertRaises(ValueError):
            lifecycle.export_episode(self.episode1["id"], output)
        source.write_bytes(original)
        with self.assertRaises(ValueError):
            lifecycle.export_episode(self.episode1["id"], self.health / "archive.zip")
        output.write_bytes(b"existing")
        with self.assertRaises(ValueError):
            lifecycle.export_episode(self.episode1["id"], output)
        self.assertEqual(output.read_bytes(), b"existing")

    def test_export_rejects_source_symlinks_and_traversal(self):
        lifecycle.link_document(self.doc_id, self.episode1["id"], "本次检查")
        source = self.health / self.report1["file"]
        outside = self.base / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        source.unlink()
        source.symlink_to(outside)
        with self.assertRaises(ValueError):
            lifecycle.export_episode(self.episode1["id"], self.base / "bad.zip")
        source.unlink()
        source.write_text("restored synthetic", encoding="utf-8")
        data = self.read()
        data["records"]["检验结果"][0]["raw_file"] = "raw/../../outside.md"
        self.save(data)
        with self.assertRaises(ValueError):
            lifecycle.export_episode(self.episode1["id"], self.base / "bad2.zip")

    def test_integrity_checks_dangling_links_and_raw_bytes_without_changing_data(self):
        lifecycle.link_document(self.doc_id, self.episode1["id"], "本次检查")
        self.assertEqual(lifecycle.check_integrity(self.read()), [])
        data = self.read()
        data["file_management"]["links"][0]["report_ids"] = ["cons_" + "9" * 32]
        self.save(data)
        self.raw_path.write_bytes(b"damaged")
        (self.health / self.report1["file"]).unlink()
        before = self.profile_path.read_bytes()
        problems = lifecycle.check_integrity(self.read())
        self.assertTrue(any("活动文档关联" in p for p in problems))
        self.assertTrue(any("校验失败" in p for p in problems))
        self.assertTrue(any("原文件缺失" in p for p in problems))
        self.assertEqual(self.profile_path.read_bytes(), before)

    def test_merge_visibility_requires_all_contributing_events_archived(self):
        for archived_second, expected in ((False, False), (True, True)):
            data = copy.deepcopy(self.data)
            data["file_management"] = {"items": {
                "episode:" + self.episode1["id"]: {"archived": True},
                "episode:" + self.episode2["id"]: {"archived": archived_second}}, "links": [], "history": []}
            before = copy.deepcopy(data["consultation_episodes"])
            merged = merge_episodes(data, [self.episode1["id"], self.episode2["id"]], "核实同次事件")
            lifecycle.sync_episode_file_states(data, before, "merge")
            self.assertEqual(lifecycle.is_archived(data, "episode:" + merged["id"]), expected)
            self.assertIn("episode:" + self.episode2["id"], data["file_management"]["items"])

    def test_split_visibility_inherits_origin_and_new_followup_reactivates(self):
        data = copy.deepcopy(self.data)
        combined = merge_episodes(data, [self.episode1["id"], self.episode2["id"]], "同一事件")
        data["file_management"] = {"items": {"episode:" + combined["id"]: {"archived": True}}, "links": [], "history": []}
        before = copy.deepcopy(data["consultation_episodes"])
        split = split_report(data, self.report1["id"], "分开整理")
        lifecycle.sync_episode_file_states(data, before, "split", {"reporter_name": "张女"})
        self.assertTrue(lifecycle.is_archived(data, "episode:" + split["id"]))
        self.assertTrue(lifecycle.is_archived(data, "episode:" + combined["id"]))
        before = {e["id"]: copy.deepcopy(e) for e in data["consultation_episodes"]}
        extra = copy.deepcopy(self.report1)
        extra["id"] = "cons_" + "3" * 32
        data["consultations"].append(extra)
        split["report_ids"].append(extra["id"])
        lifecycle.sync_episode_file_states(data, before, "add", {"reporter_name": "张父"})
        self.assertFalse(lifecycle.is_archived(data, "episode:" + split["id"]))
        self.assertTrue(lifecycle.is_archived(data, "episode:" + combined["id"]))
        last = data["file_management"]["history"][-1]
        self.assertEqual(last["new_report_ids"], [extra["id"]])
        self.assertEqual(last["reporter_name"], "张父")
        history_len = len(data["file_management"]["history"])
        lifecycle.sync_episode_file_states(data, copy.deepcopy(data["consultation_episodes"]), "add")
        self.assertEqual(len(data["file_management"]["history"]), history_len)


if __name__ == "__main__":
    unittest.main()
