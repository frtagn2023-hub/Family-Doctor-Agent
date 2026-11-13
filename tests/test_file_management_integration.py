"""Independent end-to-end household file organization using synthetic data."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class FileManagementIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="household-files-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "家庭资料"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("FAMILY_DOCTOR_", "HERMES_SESSION_"))}
        self.env.update(PYTHONDONTWRITEBYTECODE="1", FAMILY_DOCTOR_TIMEZONE="Asia/Shanghai")
        self.family("init", "--name", "合成文件管理家庭")
        self.father = json.loads(self.family("add-member", "--name", "合成父亲", "--profile", "father").stdout)
        self.child = json.loads(self.family("add-member", "--name", "合成女儿", "--profile", "daughter").stdout)
        self.health = self.home / "members" / self.father["id"] / "health-records"
        self.other_health = self.home / "members" / self.child["id"] / "health-records"
        self.source = self.base / "合成检查.txt"
        self.source.write_text("DO_NOT_LIST_FULL_PAGE\nLab value 1.2 mg/L\nEnd synthetic page", encoding="utf-8")
        self.extraction = self.write("extraction.json", {"source": {"sha256": hashlib.sha256(self.source.read_bytes()).hexdigest()},
            "status": "ok", "page_count": 1, "pages": [{"page_number": 1, "status": "ok", "text": self.source.read_text()}]})
        self.fields = self.write("fields.json", [{"name": "合成检查指标", "value": 1.2, "unit": "mg/L",
            "observed_at": "2026-11-01", "source_page": 1, "original_text": "Lab value 1.2 mg/L"}])

    def write(self, name, data):
        path = self.base / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def family(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(ROOT / "family.py"), "--home", str(self.home), *map(str, args)],
                                env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        return result

    def tool(self, name, *args, actor="father", ok=True):
        return self.family("--profile", actor, "--member", self.father["id"], "run", name, "--", *args, ok=ok)

    def profile(self):
        return json.loads((self.health / "user-profile.json").read_text())

    def stage(self, actor="father", key="upload-first"):
        return json.loads(self.tool("manage_records", "stage", "--file", self.source,
            "--extraction-json", self.extraction, "--fields-json", self.fields,
            "--title", "合成关联检查", "--document-date", "2026-11-01", "--request-id", key, actor=actor).stdout)

    def confirm(self, receipt):
        return json.loads(self.tool("manage_records", "confirm", receipt["id"], "--reviewed").stdout)

    def document(self):
        return self.confirm(self.stage())["id"]

    def episode(self, key, actor="father"):
        self.tool("add_consultation", "add", "--chief-complaint", "独立合成症状 " + key,
                  "--request-id", key, actor=actor)
        return self.profile()["consultation_episodes"][-1]

    def files(self, *args):
        return json.loads(self.tool("file_manager", "list", *args, "--json").stdout)

    def test_family_overview_names_counts_and_member_catalogs(self):
        self.document()
        self.episode("event-a")
        self.assertTrue((self.health / "catalog.md").is_file())
        summary = json.loads(self.family("--profile", "father", "files", "--rebuild", "--json").stdout)
        by_name = {row["name"]: row for row in summary["members"]}
        self.assertEqual(by_name["合成父亲"]["counts"]["document"], 1)
        self.assertEqual(by_name["合成父亲"]["counts"]["episode"], 1)
        self.assertEqual(by_name["合成女儿"]["counts"].get("document", 0), 0)
        self.assertTrue((self.other_health / "catalog.md").is_file())
        catalog = (self.home / "catalog.md").read_text()
        self.assertIn("合成父亲", catalog)
        self.assertIn("合成女儿", catalog)

    def test_member_filtered_rebuild_does_not_replace_whole_family_catalog(self):
        self.family("--profile", "father", "files", "--rebuild")
        target = self.home / "catalog.md"
        before = target.read_bytes()
        filtered = json.loads(self.family("--profile", "daughter", "--member", self.father["id"],
                                          "files", "--rebuild", "--json").stdout)
        self.assertEqual(len(filtered["members"]), 1)
        self.assertEqual(Path(filtered["catalog"]).resolve(), (self.health / "catalog.md").resolve())
        self.assertEqual(target.read_bytes(), before)

    def test_pending_and_confirmed_duplicate_uploads_use_one_catalog_entry(self):
        first, second = self.stage(), self.stage("daughter", "upload-second")
        pending = self.files("--kind", "document")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["status"], "pending")
        self.assertNotIn("DO_NOT_LIST_FULL_PAGE", json.dumps(pending))
        self.confirm(first)
        self.confirm(second)
        entries = self.files("--kind", "document")
        self.assertEqual(len(entries), 1)
        self.assertEqual(set(entries[0]["source_names"]), {"合成父亲", "合成女儿"})
        self.assertEqual(len(entries[0]["source_ids"]), 2)
        self.assertNotIn("DO_NOT_LIST_FULL_PAGE", json.dumps(entries))
        self.assertFalse((self.other_health / "imports").exists())

    def test_linked_documents_follow_merge_and_split_in_derived_files(self):
        document_id = self.document()
        first, second = self.episode("episode-one"), self.episode("episode-two", "daughter")
        self.tool("file_manager", "link", "document:" + document_id, "episode:" + first["id"], "--basis", "与第一份来源相关")
        self.tool("add_consultation", "merge", "--episodes", first["id"], second["id"], "--basis", "核对同一事件")
        self.tool("generate_health_report", "--brief")
        merged_path = self.health / "consultations" / "episodes" / (first["id"] + ".md")
        self.assertIn("合成关联检查", merged_path.read_text())
        self.assertIn("合成关联检查", (self.health / "reports" / "latest.md").read_text())
        self.tool("add_consultation", "split", "--report", first["report_ids"][0], "--basis", "核对这份描述属于另一次")
        episodes = self.profile()["consultation_episodes"]
        split = next(e for e in episodes if first["report_ids"][0] in e["report_ids"])
        self.assertIn("合成关联检查", (self.health / split["file"]).read_text())
        self.assertNotIn("合成关联检查", merged_path.read_text())

    def test_alias_archive_restore_preserves_clinical_report_facts(self):
        first, second = self.stage(), self.stage("daughter", "upload-second")
        self.confirm(first)
        self.confirm(second)
        episode = self.episode("archive-event")
        self.tool("file_manager", "link", "document:" + first["id"], "episode:" + episode["id"], "--basis", "本次相关检查")
        before = self.profile()["records"]
        alias = "document:" + second["id"]
        self.tool("file_manager", "archive", alias, "--reason", "已处理")
        self.assertEqual(self.files("--kind", "document"), [])
        self.assertEqual(len(self.files("--kind", "document", "--status", "archived")), 1)
        self.tool("generate_health_report", "--brief")
        report = (self.health / "reports" / "latest.md").read_text()
        self.assertIn("合成检查指标", report)
        self.assertIn("1.2", report)
        self.assertIn("已归档", report)
        self.assertEqual(self.profile()["records"], before)
        self.tool("file_manager", "restore", alias, "--reason", "重新查看")
        self.assertEqual(len(self.files("--kind", "document")), 1)

    def test_cli_export_portable_files_and_manifest(self):
        document_id = self.document()
        episode = self.episode("export-event")
        self.tool("file_manager", "link", document_id, episode["id"], "--basis", "此次检查")
        output = self.base / "event.zip"
        result = json.loads(self.tool("file_manager", "export", episode["id"], "--output", output, "--json").stdout)
        self.assertEqual(result["document_count"], 1)
        with zipfile.ZipFile(output) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            for entry in manifest["files"]:
                self.assertEqual(hashlib.sha256(archive.read(entry["path"])).hexdigest(), entry["sha256"])
            event = archive.read("event.md").decode()
            for target in re.findall(r"\]\(([^)]+)\)", event):
                self.assertIn(target, archive.namelist())
            self.assertEqual(manifest["member"]["name"], "合成父亲")
            self.assertFalse(any(self.child["id"] in path for path in archive.namelist()))

    def test_doctor_check_detects_corrupt_original_bytes(self):
        self.document()
        self.episode("check-event")
        self.tool("doctor", "check")
        document = self.profile()["records"]["检验结果"][0]
        (self.health / document["raw_file"]).write_bytes(b"damaged synthetic original")
        result = self.tool("doctor", "check", ok=False)
        self.assertIn("校验失败", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
