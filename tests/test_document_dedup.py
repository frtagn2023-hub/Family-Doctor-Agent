"""Synthetic household document receipt and canonical-index regressions."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "family-doctor" / "scripts"


class DocumentDedupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="family-document-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.home = self.directory / "family"
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("FAMILY_DOCTOR_", "HERMES_SESSION_"))}
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.family("init")
        self.parent = json.loads(self.family("add-member", "--name", "测试父亲", "--profile", "parent").stdout)
        self.child = json.loads(self.family("add-member", "--name", "测试家属", "--profile", "child").stdout)
        self.health = self.home / "members" / self.parent["id"] / "health-records"
        self.source = self.directory / "report.txt"
        self.source.write_text("Synthetic lab value 1.2 mg/L", encoding="utf-8")
        self.extraction = self.directory / "extraction.json"
        self.fields = self.directory / "fields.json"
        self.extracted = {"source": {"sha256": hashlib.sha256(self.source.read_bytes()).hexdigest()},
                          "status": "ok", "page_count": 1,
                          "pages": [{"page_number": 1, "status": "ok", "text": self.source.read_text()}]}
        self.facts = [{"name": "合成指标", "value": 1.2, "unit": "mg/L", "observed_at": "2020-01-02",
                       "source_page": 1, "original_text": "Synthetic lab value 1.2 mg/L"}]
        self.write(self.extraction, self.extracted)
        self.write(self.fields, self.facts)

    def write(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def family(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(ROOT / "family.py"), "--home", str(self.home), *map(str, args)],
                                env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        return result

    def command(self, profile, *args, member=None):
        return [sys.executable, str(ROOT / "family.py"), "--home", str(self.home),
                "--profile", profile, "--member", member or self.parent["id"],
                "run", "manage_records", "--", *map(str, args)]

    def tool(self, profile, *args, ok=True, member=None):
        result = subprocess.run(self.command(profile, *args, member=member), env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        return json.loads(result.stdout) if ok else result

    def stage(self, profile="parent", *extra, source=None, member=None):
        return self.tool(profile, "stage", "--file", source or self.source, "--extraction-json", self.extraction,
                         "--fields-json", self.fields, *extra, member=member)

    def confirm(self, receipt, profile="parent", member=None):
        return self.tool(profile, "confirm", receipt["id"], "--reviewed", member=member)

    def profile(self):
        return json.loads((self.health / "user-profile.json").read_text())

    def docs(self):
        return [entry for entries in self.profile()["records"].values() for entry in entries]

    def test_renamed_duplicate_reuses_blob_keeps_both_people_and_one_document(self):
        first = self.stage("parent", "--title", "合成检查", "--document-date", "2020-01-02")
        self.confirm(first)
        second_file = self.directory / "family-copy.pdf"
        second_file.write_bytes(self.source.read_bytes())
        second = self.stage("child", "--document-type", "其他", source=second_file)
        linked = self.confirm(second, "child")
        self.assertEqual(linked["status"], "confirmed")
        self.assertEqual(linked["canonical_document_id"], first["id"])
        self.assertEqual(linked["duplicate_of"], first["id"])
        self.assertEqual(len(self.docs()), 1)
        document = self.docs()[0]
        self.assertEqual(document["document_date"], "2020-01-02")
        self.assertEqual(document["title"], "合成检查")
        self.assertEqual({s["reporter_name"] for s in document["upload_sources"]}, {"测试父亲", "测试家属"})
        self.assertEqual({s["original_name"] for s in document["upload_sources"]}, {"report.txt", "family-copy.pdf"})
        self.assertEqual(len(list((self.health / "raw").glob("*"))), 1)
        self.assertEqual(self.profile()["summary"]["documentCount"], 1)
        self.assertNotIn("其他", self.profile()["records"])

    def test_conflicting_transcription_and_metadata_never_replace_confirmed_values(self):
        first = self.stage("parent", "--title", "第一次标题", "--document-date", "2020-01-02")
        self.confirm(first)
        canonical = self.docs()[0]["fields"]
        self.facts[0]["value"] = 9.9
        self.write(self.fields, self.facts)
        second = self.stage("child", "--title", "第二次标题", "--document-date", "2020-01-03")
        linked = self.confirm(second, "child")
        self.assertEqual(self.docs()[0]["fields"], canonical)
        self.assertEqual(linked["fields"][0]["value"], 9.9)
        self.assertEqual(linked["canonical_fields"][0]["value"], 1.2)
        self.assertEqual({c["field"] for c in linked["duplicate_conflicts"]}, {"fields", "title", "document_date"})
        self.assertEqual(self.docs()[0]["upload_sources"][1]["submitted_fields"][0]["value"], 9.9)

    def test_request_retry_reuses_receipt_and_changed_payload_rejected(self):
        first = self.stage("parent", "--request-id", "one-upload")
        self.assertEqual(self.stage("parent", "--request-id", "one-upload")["id"], first["id"])
        self.confirm(first)
        repeated = self.stage("parent", "--request-id", "one-upload")
        self.assertEqual(repeated["id"], first["id"])
        self.assertEqual(repeated["status"], "confirmed")
        before = (self.health / "user-profile.json").read_bytes()
        self.tool("parent", "stage", "--file", self.source, "--extraction-json", self.extraction,
                  "--fields-json", self.fields, "--request-id", "one-upload", "--title", "不同标题", ok=False)
        self.assertEqual((self.health / "user-profile.json").read_bytes(), before)
        self.assertEqual(len(list((self.health / "imports").glob("*.json"))), 1)
        child = self.stage("child", "--request-id", "one-upload")
        self.assertNotEqual(child["id"], first["id"])

    def test_concurrent_confirmations_publish_one_document_with_two_sources(self):
        first, second = self.stage("parent"), self.stage("child")
        jobs = [subprocess.Popen(self.command(actor, "confirm", record["id"], "--reviewed"),
                                 env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                for actor, record in (("parent", first), ("child", second))]
        for job in jobs:
            stdout, stderr = job.communicate(timeout=20)
            self.assertEqual(job.returncode, 0, stdout + stderr)
        self.assertEqual(len(self.docs()), 1)
        self.assertEqual(len(self.docs()[0]["upload_sources"]), 2)
        for actor, record in (("parent", first), ("child", second)):
            self.confirm(record, actor)
        self.assertEqual(len(self.docs()[0]["upload_sources"]), 2)

    def test_same_document_in_different_members_is_not_cross_linked(self):
        first = self.stage("parent")
        self.confirm(first)
        second = self.stage("child", member=self.child["id"])
        linked = self.confirm(second, "child", member=self.child["id"])
        self.assertEqual(linked["canonical_document_id"], second["id"])
        self.assertNotIn("duplicate_of", linked)
        self.assertEqual(len(self.docs()[0]["upload_sources"]), 1)

    def test_different_bytes_with_same_name_date_and_fields_are_separate(self):
        first = self.stage("parent", "--document-date", "2020-01-02")
        self.confirm(first)
        self.source.write_text(self.source.read_text() + "\nsecond original", encoding="utf-8")
        self.extracted["source"]["sha256"] = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.extracted["pages"][0]["text"] = self.source.read_text()
        self.write(self.extraction, self.extracted)
        second = self.stage("child", "--document-date", "2020-01-02")
        self.confirm(second, "child")
        self.assertEqual(len(self.docs()), 2)

    def test_unknown_document_date_stays_unknown_and_invalid_dates_fail(self):
        first = self.stage()
        self.assertIsNone(first["document_date"])
        self.confirm(first)
        self.assertIsNone(self.docs()[0]["document_date"])
        for value in ("2026-02-30", "20261111", "yesterday"):
            self.tool("parent", "stage", "--file", self.source, "--extraction-json", self.extraction,
                      "--fields-json", self.fields, "--document-date", value, ok=False)
        self.assertEqual(len(list((self.health / "imports").glob("*.json"))), 1)

    def test_duplicate_still_requires_complete_checked_extraction_and_valid_original(self):
        self.confirm(self.stage())
        self.extracted["pages"][0]["status"] = "error"
        self.write(self.extraction, self.extracted)
        duplicate = self.stage("child")
        self.tool("child", "confirm", duplicate["id"], "--reviewed", ok=False)
        self.assertEqual(len(self.docs()[0]["upload_sources"]), 1)
        self.extracted["pages"][0]["status"] = "ok"
        self.write(self.extraction, self.extracted)
        duplicate = self.stage("child")
        (self.health / duplicate["raw_file"]).write_bytes(b"damaged")
        self.tool("child", "confirm", duplicate["id"], "--reviewed", ok=False)
        self.assertEqual(len(self.docs()[0]["upload_sources"]), 1)

    def test_duplicate_receipt_publication_failure_recovers_committed_source(self):
        first = self.stage("parent")
        self.confirm(first)
        second = self.stage("child")
        code = '''
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
import health_store as storage
import manage_records as imports
target = imports.record_path(sys.argv[2])
original = storage._replace_text
def fail(path, content):
    if Path(path) == target:
        raise OSError('injected receipt failure')
    return original(path, content)
storage._replace_text = fail
try:
    imports.confirm(SimpleNamespace(record_id=sys.argv[2], reviewed=True))
except OSError:
    pass
else:
    raise AssertionError('expected publication failure')
'''
        env = dict(self.env, FAMILY_DOCTOR_HOME=str(self.health.parent),
                   FAMILY_DOCTOR_FAMILY_HOME=str(self.home), FAMILY_DOCTOR_PROFILE="child",
                   FAMILY_DOCTOR_MEMBER_ID=self.parent["id"])
        result = subprocess.run([sys.executable, "-c", code, str(SCRIPTS), second["id"]], env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.docs()[0]["upload_sources"]), 2)
        receipt_path = self.health / "imports" / (second["id"] + ".json")
        self.assertEqual(json.loads(receipt_path.read_text())["status"], "pending")
        before = (self.health / "user-profile.json").read_bytes()
        self.tool("child", "review", second["id"], "--fields-json", self.fields, ok=False)
        recovered = self.confirm(second, "child")
        self.assertEqual(recovered["duplicate_of"], first["id"])
        self.assertEqual((self.health / "user-profile.json").read_bytes(), before)
        self.assertEqual(len(self.docs()[0]["upload_sources"]), 2)

    def test_failed_publication_restores_committed_metadata_for_primary_and_duplicate(self):
        code = '''
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
import health_store as storage
import manage_records as imports
target = imports.record_path(sys.argv[2])
original = storage._replace_text
def fail(path, content):
    if Path(path) == target:
        raise OSError('injected receipt failure')
    return original(path, content)
storage._replace_text = fail
try:
    imports.confirm(SimpleNamespace(record_id=sys.argv[2], reviewed=True))
except OSError:
    pass
else:
    raise AssertionError('expected publication failure')
'''
        for actor in ("parent", "child"):
            receipt = self.stage(actor, "--title", "原始标题", "--document-date", "2020-01-02",
                                 "--request-id", "stable-" + actor)
            env = dict(self.env, FAMILY_DOCTOR_HOME=str(self.health.parent),
                       FAMILY_DOCTOR_FAMILY_HOME=str(self.home), FAMILY_DOCTOR_PROFILE=actor,
                       FAMILY_DOCTOR_MEMBER_ID=self.parent["id"])
            failed = subprocess.run([sys.executable, "-c", code, str(SCRIPTS), receipt["id"]],
                                    env=env, text=True, capture_output=True)
            self.assertEqual(failed.returncode, 0, failed.stdout + failed.stderr)
            committed = next(s for s in self.docs()[0]["upload_sources"] if s["import_id"] == receipt["id"])
            path = self.health / "imports" / (receipt["id"] + ".json")
            stale = json.loads(path.read_text())
            stale.update(title="过期标题", document_date="1999-01-01", original_name="过期名字.txt",
                         imported_at="1999-01-01T00:00:00+00:00", reporter_name="错误来源姓名",
                         observed_actor_id="invented-after-index", request_key="wrong-stale-key")
            self.write(path, stale)
            before = (self.health / "user-profile.json").read_bytes()
            recovered = self.confirm(receipt, actor)
            for key in ("title", "document_date", "original_name", "imported_at", "reporter_name", "request_key"):
                self.assertEqual(recovered[key], committed[key])
            self.assertNotIn("observed_actor_id", recovered)
            self.assertEqual((self.health / "user-profile.json").read_bytes(), before)
            repeated = self.stage(actor, "--title", "原始标题", "--document-date", "2020-01-02",
                                  "--request-id", "stable-" + actor)
            self.assertEqual(repeated["id"], receipt["id"])
        listed = json.loads(self.family("--profile", "parent", "run", "file_manager", "--",
                                        "list", "--kind", "document", "--json").stdout)
        self.assertEqual(set(listed[0]["source_names"]), {"测试父亲", "测试家属"})

    def test_archived_pending_group_keeps_visibility_when_another_receipt_confirms_first(self):
        first, second = self.stage("parent"), self.stage("child")
        self.family("--profile", "parent", "run", "file_manager", "--", "archive",
                    "document:" + first["id"], "--reason", "合成归档测试")
        self.confirm(second, "child")
        self.family("--profile", "parent", "run", "add_consultation", "--", "add",
                    "--chief-complaint", "合成咨询")
        episode = self.profile()["consultation_episodes"][0]
        self.family("--profile", "parent", "run", "file_manager", "--", "link",
                    "document:" + second["id"], episode["id"], "--basis", "同次合成就诊")
        def views():
            entries = json.loads(self.family("--profile", "parent", "run", "file_manager", "--",
                                             "list", "--status", "all", "--json").stdout)
            document = next(e for e in entries if e["kind"] == "document")
            event = next(e for e in entries if e["kind"] == "episode")
            return document, event["linked_documents"][0]
        document, linked = views()
        self.assertEqual(document["status"], "archived")
        self.assertTrue(linked["archived"])
        self.assertTrue(any(h["action"] == "canonicalize_document_visibility"
                            for h in self.profile()["file_management"]["history"]))
        self.family("--profile", "parent", "run", "file_manager", "--", "restore",
                    "document:" + second["id"], "--reason", "合成恢复测试")
        self.confirm(first)
        document, linked = views()
        self.assertEqual(document["status"], "active")
        self.assertFalse(linked["archived"])



if __name__ == "__main__":
    unittest.main()
