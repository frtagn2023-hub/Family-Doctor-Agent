"""Synthetic-only observation import, provenance, idempotency and statistics."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cognition_store import (CognitionStore, ImportValidationError, MAX_CONTENT_BYTES,
                             RevisionConflict, template_csv, template_json)


class CognitionImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-cognition-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "household"
        self.home.mkdir()
        self.father, self.child = "m_" + "a" * 32, "m_" + "b" * 32
        self.registry = {"schema_version": 1, "name": "合成观察家庭", "members": {
            self.father: {"id": self.father, "name": "合成父亲"}, self.child: {"id": self.child, "name": "合成女儿"}},
            "profiles": {"father": self.father, "daughter": self.child}}
        self.save_registry()
        self.clock = patch("cognition_store._now", return_value=datetime(2026, 11, 11, 4, tzinfo=timezone.utc))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.store = CognitionStore(self.home, "father")
        self.path = self.home / "cognition" / "observations.json"

    def save_registry(self):
        (self.home / "family.json").write_text(json.dumps(self.registry, ensure_ascii=False))

    def record(self, **overrides):
        row = {"source_record_id": "synthetic-1", "member": "合成父亲", "reporter_profile": "father",
               "observed_at": "2026-11-11T08:00:00+08:00", "source_type": "self_expression", "quality": "usable",
               "signals": ["repeated_question"], "confounders": [], "event_key": ""}
        row.update(overrides)
        return row

    def content(self, *records):
        return json.dumps({"schema_version": 1, "records": list(records)}, ensure_ascii=False)

    def commit(self, *records):
        content = self.content(*records)
        preview = self.store.preview(content)
        self.assertTrue(preview["ok"], preview)
        return self.store.commit(content, expected_revision=preview["revision"], expected_digest=preview["digest"])

    def test_preview_never_creates_directories_and_template_cannot_be_imported(self):
        preview = self.store.preview(self.content(self.record()))
        self.assertTrue(preview["ok"])
        self.assertFalse(self.path.parent.exists())
        self.assertFalse(self.store.preview(json.dumps(template_json()))["ok"])
        self.assertFalse(self.store.preview(template_csv(), "csv")["ok"])
        self.assertFalse(self.path.parent.exists())

    def test_idempotent_replay_and_changed_content_conflict_preserve_bytes(self):
        first = self.commit(self.record())
        original = self.path.read_bytes()
        second = self.commit(self.record())
        self.assertEqual(second["new"], 0)
        self.assertEqual(second["duplicates"], 1)
        self.assertIsNone(second["batchId"])
        self.assertEqual(first["revision"], second["revision"])
        self.assertEqual(self.path.read_bytes(), original)
        rejected = self.store.preview(self.content(self.record(signals=[])))
        self.assertFalse(rejected["ok"])
        self.assertEqual(rejected["errors"][0]["field"], "source_record_id")
        with self.assertRaises(ImportValidationError):
            self.store.commit(self.content(self.record(signals=[])), expected_revision=first["revision"])
        self.assertEqual(self.path.read_bytes(), original)

    def test_batch_is_atomic_and_errors_do_not_echo_raw_values(self):
        self.commit(self.record())
        original = self.path.read_bytes()
        content = self.content(self.record(source_record_id="valid-new"), self.record(source_record_id="bad", raw_text="SECRET_RAW_DIALOGUE"))
        preview = self.store.preview(content)
        self.assertFalse(preview["ok"])
        self.assertEqual(preview["errors"][0]["row"], 2)
        self.assertNotIn("SECRET_RAW_DIALOGUE", json.dumps(preview))
        with self.assertRaises(ImportValidationError):
            self.store.commit(content, expected_revision=1)
        self.assertEqual(original, self.path.read_bytes())

    def test_source_roles_and_strict_current_profile_are_checked(self):
        for row in (self.record(reporter_profile="daughter"), self.record(source_type="caregiver_observation"),
                    self.record(reporter_profile="missing"), self.record(member="不存在的成员")):
            self.assertFalse(self.store.preview(self.content(row))["ok"])
        proxy = self.record(reporter_profile="daughter", source_type="caregiver_observation")
        self.assertTrue(self.store.preview(self.content(proxy))["ok"])
        self.assertFalse(self.store.preview(self.content(proxy), strict_reporter=True)["ok"])
        self.commit(proxy)
        member = self.store.snapshot()["members"][0]
        self.assertEqual(member["languageSamples"], 0)
        self.assertEqual(member["sourceCounts"]["caregiver_observation"], 1)

    def test_duplicate_name_requires_id_and_no_implicit_enrollment(self):
        self.registry["members"][self.child]["name"] = "合成父亲"
        self.save_registry()
        self.assertFalse(self.store.preview(self.content(self.record()))["ok"])
        self.assertTrue(self.store.preview(self.content(self.record(member=self.father)))["ok"])

    def test_same_event_sources_preserved_but_statistics_count_one(self):
        own = self.record(event_key="shared-event")
        proxy = self.record(source_record_id="proxy-1", event_key="shared-event", observed_at="2026-11-11T00:00:00Z",
            reporter_profile="daughter", source_type="caregiver_observation", signals=["repeated_question", "disorientation"])
        self.commit(own, proxy)
        member = self.store.snapshot()["members"][0]
        self.assertEqual(member["validSamples"], 2)
        self.assertEqual(member["eventCount"], 1)
        self.assertEqual(member["signalCount"], 1)
        self.assertEqual(member["languageSamples"], 1)
        self.assertEqual(next(d["count"] for d in member["domains"] if d["key"] == "repeated_question"), 1)
        self.assertEqual(len(json.loads(self.path.read_text())["records"]), 2)
        conflict = self.record(source_record_id="proxy-2", event_key="shared-event", observed_at="2026-11-11T01:00:00Z")
        self.assertFalse(self.store.preview(self.content(conflict))["ok"])

    def test_distinct_real_observations_are_not_collapsed_by_similar_content(self):
        self.commit(self.record(), self.record(source_record_id="synthetic-2", observed_at="2026-11-11T09:00:00+08:00"))
        member = self.store.snapshot()["members"][0]
        self.assertEqual(member["eventCount"], 2)
        self.assertEqual(member["signalCount"], 2)

    def test_limited_and_excluded_never_create_risk_or_language_sample(self):
        self.commit(self.record(quality="limited"), self.record(source_record_id="excluded", quality="excluded"))
        result = self.store.snapshot()["members"][0]
        self.assertEqual(result["status"], "not_assessed")
        self.assertEqual(result["qualityCounts"], {"usable": 0, "limited": 1, "excluded": 1})
        self.assertEqual(result["eventCount"], 0)
        self.assertEqual(result["signalCount"], 0)
        self.assertEqual(result["languageSamples"], 0)
        self.commit(self.record(source_record_id="no-signals", signals=[]))
        self.assertEqual(self.store.snapshot()["members"][0]["status"], "recorded")
        self.assertNotIn("正常", json.dumps(self.store.snapshot(), ensure_ascii=False))

    def test_shanghai_calendar_window_and_unknown_members(self):
        self.commit(self.record(source_record_id="before", observed_at="2026-10-12T23:59:59+08:00"),
                    self.record(source_record_id="edge", observed_at="2026-10-12T16:00:00Z"), self.record())
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["meta"]["windowStart"], "2026-10-13")
        self.assertEqual(snapshot["members"][0]["validSamples"], 2)
        self.assertEqual(snapshot["members"][0]["trend"][0]["signals"], 1)
        self.assertEqual(snapshot["members"][1]["status"], "not_assessed")

    def test_csv_json_equivalence_and_in_batch_duplicates(self):
        import csv, io
        row = self.record()
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(row))
        writer.writeheader()
        csvrow = dict(row, signals="|".join(row["signals"]), confounders="")
        writer.writerow(csvrow)
        preview = self.store.preview(output.getvalue(), "csv")
        self.assertTrue(preview["ok"])
        self.assertEqual(preview["digest"], self.store.preview(self.content(row))["digest"])
        self.store.commit(output.getvalue(), "csv", expected_revision=0, expected_digest=preview["digest"])
        preview = self.store.preview(self.content(row, row))
        self.assertTrue(preview["ok"])
        self.assertEqual(preview["duplicates"], 2)
        self.assertFalse(self.store.preview("member,member\na,b", "csv")["ok"])

    def test_schema_limits_and_time_quality_validation(self):
        for text in ("", "[]", '{"schema_version":true,"records":[]}', '{"schema_version":1,"schema_version":1,"records":[]}',
                     '{"schema_version":1,"records":[NaN]}', "x" * (MAX_CONTENT_BYTES + 1), self.content(*([self.record()] * 1001))):
            self.assertFalse(self.store.preview(text)["ok"])
        for changes in ({"observed_at":"2026-11-11"}, {"observed_at":"2026-11-11T08:00:00"},
                        {"observed_at":"2026-11-12T00:00:00+08:00"}, {"quality":"good"},
                        {"signals":["repeated_question","repeated_question"]}, {"signals":[True]},
                        {"confounders":["SECRET_FREE_TEXT"]}, {"source_record_id":True}):
            self.assertFalse(self.store.preview(self.content(self.record(**changes)))["ok"])

    def test_stale_preview_and_registration_change_rejected(self):
        content = self.content(self.record())
        old = self.store.preview(content)
        self.commit(self.record(source_record_id="another"))
        with self.assertRaises(RevisionConflict):
            self.store.commit(content, expected_revision=old["revision"], expected_digest=old["digest"])
        fresh = self.store.preview(content)
        self.registry["name"] = "合成家庭更名"
        self.save_registry()
        with self.assertRaises(RevisionConflict):
            self.store.commit(content, expected_revision=fresh["revision"], expected_digest=fresh["digest"])
        fresh = self.store.preview(content)
        self.registry["members"][self.father]["name"] = "合成成员更名"
        self.save_registry()
        with self.assertRaises(RevisionConflict):
            self.store.commit(content, expected_revision=fresh["revision"], expected_digest=fresh["digest"])

    def test_concurrent_commit_serializes_and_preserves_one_atomic_batch(self):
        content = self.content(self.record())
        preview = self.store.preview(content)
        def work():
            try:
                return self.store.commit(content, expected_revision=preview["revision"], expected_digest=preview["digest"])["new"]
            except RevisionConflict:
                return "stale"
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: work(), range(2)))
        self.assertCountEqual(outcomes, [1, "stale"])
        self.assertEqual(len(json.loads(self.path.read_text())["records"]), 1)

    def test_corrupt_store_symlinks_and_private_permissions(self):
        self.commit(self.record())
        self.commit(self.record(source_record_id="second"))
        if os.name != "nt":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(len(list((self.path.parent / ".backups").glob("*.bak"))), 1)
        self.path.write_text('{broken')
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.preview(self.content(self.record()))
        self.assertEqual(self.path.read_bytes(), before)
        self.path.unlink()
        external = Path(self.temp.name) / "external.json"
        external.write_text("{}")
        self.path.symlink_to(external)
        with self.assertRaises(ValueError):
            self.store.snapshot()
        self.assertEqual(external.read_text(), "{}")

    def test_snapshot_and_import_audit_expose_only_allowed_summary(self):
        self.commit(self.record(source_record_id="SECRET_SOURCE_ID", event_key="SECRET_EVENT_ID", confounders=["sleep"]))
        summary = json.dumps(self.store.snapshot(), ensure_ascii=False)
        for forbidden in ("SECRET_SOURCE_ID", "SECRET_EVENT_ID", "reporter_profile", "ingested_by_profile", str(self.home)):
            self.assertNotIn(forbidden, summary)
        stored = json.loads(self.path.read_text())
        self.assertNotIn("raw_text", stored["records"][0])
        self.assertNotIn("filename", stored["imports"][0])

    def test_store_duplicate_fields_and_incomplete_audit_stop_reads(self):
        self.commit(self.record())
        original = self.path.read_text()
        self.path.write_text(original.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'))
        with self.assertRaises(ValueError):
            self.store.snapshot()
        broken = json.loads(original)
        broken["records"] = []
        self.path.write_text(json.dumps(broken))
        with self.assertRaises(ValueError):
            self.store.snapshot()

    def test_demo_preview_uses_memory_only_and_never_saves(self):
        demo = CognitionStore.demo(self.registry, "father")
        self.assertTrue(demo.preview(self.content(self.record()))["ok"])
        with self.assertRaises(ValueError):
            demo.commit(self.content(self.record()), expected_revision=0)
        self.assertFalse(self.path.parent.exists())

    def test_cli_current_source_guard_and_explicit_batch(self):
        self.clock.stop()
        file = Path(self.temp.name) / "source.json"
        file.write_text(self.content(self.record(reporter_profile="daughter", source_type="caregiver_observation")))
        args = [sys.executable, str(ROOT / "cognition.py"), "--home", str(self.home), "--profile", "father", "import", "--file", str(file)]
        result = subprocess.run(args, text=True, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.path.exists())
        result = subprocess.run(args + ["--batch"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(json.loads(result.stdout)["new"], 1)


if __name__ == "__main__":
    unittest.main()
