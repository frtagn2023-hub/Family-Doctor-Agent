"""Synthetic integration tests for imports, consultation states and workspace tools."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "family-doctor" / "scripts"


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="doctor-workflow-")
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.workspace = self.home / "workspace"
        self.health = self.workspace / "health-records"
        self.env = dict(os.environ, FAMILY_DOCTOR_HOME=str(self.workspace), PYTHONDONTWRITEBYTECODE="1")
        self.source = self.home / "synthetic.txt"
        self.source.write_text("Synthetic report\nTest value 1.2 mg/L\nSecond page", encoding="utf-8")
        self.digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.extraction = self.home / "extraction.json"
        self.fields = self.home / "fields.json"
        self.payload = {"source": {"sha256": self.digest}, "status": "ok", "page_count": 2,
                        "pages": [{"page_number": 1, "status": "ok", "text": "Test value 1.2 mg/L"},
                                  {"page_number": 2, "status": "ok", "text": "Second page"}]}
        self.field_list = [{"name": "测试指标", "value": 1.2, "unit": "mg/L", "observed_at": "2026-01-02",
                            "source_page": 1, "original_text": "Test value 1.2 mg/L", "confidence": 95}]
        self.write(self.extraction, self.payload)
        self.write(self.fields, self.field_list)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def run_tool(self, tool, *args, ok=True):
        path = ROOT / "doctor.py" if tool == "doctor" else SCRIPTS / (tool + ".py")
        result = subprocess.run([sys.executable, str(path), *map(str, args)], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        return result

    def stage(self, document_type="检验结果", with_fields=False, ok=True):
        args = ["stage", "--role", "self", "--file", self.source, "--extraction-json", self.extraction,
                "--document-type", document_type]
        if with_fields:
            args += ["--fields-json", self.fields]
        result = self.run_tool("manage_records", *args, ok=ok)
        return json.loads(result.stdout) if ok else result

    def review_confirm(self, record):
        self.run_tool("manage_records", "review", record["id"], "--role", "self", "--fields-json", self.fields)
        return json.loads(self.run_tool("manage_records", "confirm", record["id"], "--role", "self", "--reviewed").stdout)

    def test_import_review_confirm_round_trip_and_idempotence(self):
        self.run_tool("doctor", "init")
        pending = self.stage()
        profile_path = self.health / "user-profile.json"
        self.assertEqual(json.loads(profile_path.read_text())["records"], {})
        self.run_tool("manage_records", "confirm", pending["id"], "--role", "self", "--reviewed", ok=False)
        confirmed = self.review_confirm(pending)
        self.assertEqual(confirmed["status"], "confirmed")
        profile = json.loads(profile_path.read_text())
        entry = profile["records"]["检验结果"][0]
        self.assertEqual(entry["sha256"], self.digest)
        self.assertEqual(entry["fields"][0]["source_page"], 1)
        self.assertEqual(entry["fields"][0]["confirmation_status"], "confirmed")
        self.assertEqual(entry["fields"][0]["confirmed_by"], "user")
        self.assertEqual(profile["summary"]["totalRecords"], 1)
        before = profile_path.read_bytes()
        repeated = json.loads(self.run_tool("manage_records", "confirm", pending["id"], "--role", "self", "--reviewed").stdout)
        self.assertEqual(repeated, confirmed)
        self.assertEqual(profile_path.read_bytes(), before)
        self.run_tool("doctor", "check")

    def test_source_sha_and_page_references_are_enforced(self):
        self.payload["source"]["sha256"] = "0" * 64
        self.write(self.extraction, self.payload)
        self.stage(ok=False)
        self.assertFalse(self.health.exists())
        self.payload["source"]["sha256"] = self.digest
        self.write(self.extraction, self.payload)
        for change in ({"source_page": True}, {"source_page": 3}, {"original_text": "invented"}, {"unit": []},
                       {"value": "12.5", "unit": None}, {"value": "1e3", "unit": None}):
            self.write(self.fields, [dict(self.field_list[0], **change)])
            self.stage(with_fields=True, ok=False)
        self.assertFalse(self.health.exists())
        self.write(self.fields, [dict(self.field_list[0], value="定性结果", unit=None)])
        self.assertEqual(self.stage(with_fields=True)["fields"][0]["value"], "定性结果")

    def test_invalid_page_numbers_rejected_before_storage(self):
        for number in (0, -1, True):
            self.payload["pages"][0]["page_number"] = number
            self.write(self.extraction, self.payload)
            self.stage(ok=False)
        self.assertFalse(self.health.exists())

    def test_incomplete_or_failed_pages_never_confirm(self):
        for failed in (False, True):
            payload = dict(self.payload)
            payload["pages"] = [dict(page) for page in self.payload["pages"]]
            if failed:
                payload["pages"][1]["status"] = "error"
            else:
                payload["pages"].pop()
            self.write(self.extraction, payload)
            record = self.stage(with_fields=True)
            self.run_tool("manage_records", "confirm", record["id"], "--role", "self", "--reviewed", ok=False)
            self.assertFalse((self.health / "user-profile.json").exists())

    def test_duplicate_file_cannot_bypass_index_by_changing_category(self):
        record = self.stage()
        self.review_confirm(record)
        profile_path = self.health / "user-profile.json"
        before = json.loads(profile_path.read_text())
        duplicate = self.stage(document_type="其他", with_fields=True)
        linked = json.loads(self.run_tool("manage_records", "confirm", duplicate["id"], "--role", "self", "--reviewed").stdout)
        saved = json.loads(profile_path.read_text())
        self.assertEqual(list(saved["records"]), ["检验结果"])
        self.assertEqual(len(saved["records"]["检验结果"]), 1)
        canonical = saved["records"]["检验结果"][0]
        self.assertEqual(canonical["fields"], before["records"]["检验结果"][0]["fields"])
        self.assertEqual(len(canonical["upload_sources"]), 2)
        self.assertEqual(linked["duplicate_of"], canonical["id"])
        self.assertEqual(linked["status"], "confirmed")
        self.assertTrue(any(item["field"] == "document_type" for item in linked["duplicate_conflicts"]))

    def test_reconfirmation_rechecks_original_integrity(self):
        record = self.stage()
        self.review_confirm(record)
        profile_path = self.health / "user-profile.json"
        before = profile_path.read_bytes()
        (self.health / record["raw_file"]).write_bytes(b"tampered original")
        result = self.run_tool("manage_records", "confirm", record["id"], "--role", "self", "--reviewed", ok=False)
        self.assertIn("校验失败", result.stderr)
        self.assertEqual(profile_path.read_bytes(), before)

    def test_failed_import_publication_recovers_exact_committed_index(self):
        record = self.stage(with_fields=True)
        code = '''
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
import health_store as storage
import manage_records as imports
target = imports.record_path(sys.argv[2])
original = storage._replace_text
def fail_import_publication(path, text):
    if Path(path) == target:
        raise OSError('injected import publication failure')
    return original(path, text)
storage._replace_text = fail_import_publication
try:
    imports.confirm(SimpleNamespace(record_id=sys.argv[2], reviewed=True))
except OSError as exc:
    assert str(exc) == 'injected import publication failure'
else:
    raise AssertionError('expected injected failure')
'''
        failed = subprocess.run([sys.executable, "-c", code, str(SCRIPTS), record["id"]],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(failed.returncode, 0, failed.stdout + failed.stderr)
        profile_path = self.health / "user-profile.json"
        profile_bytes = profile_path.read_bytes()
        published = json.loads(profile_bytes)["records"]["检验结果"][0]
        record_path = self.health / "imports" / (record["id"] + ".json")
        pending_bytes = record_path.read_bytes()
        self.assertEqual(json.loads(pending_bytes)["status"], "pending")
        self.write(self.fields, [dict(self.field_list[0], value=9.9)])
        rejected = self.run_tool("manage_records", "review", record["id"], "--role", "self", "--fields-json", self.fields, ok=False)
        self.assertIn("已确认入档", rejected.stderr)
        self.assertEqual(record_path.read_bytes(), pending_bytes)
        self.assertEqual(profile_path.read_bytes(), profile_bytes)
        # Recovery must still verify the original bytes before trusting the index.
        raw = self.health / record["raw_file"]
        raw.write_bytes(b"changed original")
        self.run_tool("manage_records", "confirm", record["id"], "--role", "self", "--reviewed", ok=False)
        self.assertEqual(record_path.read_bytes(), pending_bytes)
        raw.write_bytes(self.source.read_bytes())
        # Simulate a stale pending edit made by the earlier implementation.
        pending = json.loads(pending_bytes)
        pending["fields"][0]["value"] = 9.9
        self.write(record_path, pending)
        recovered = json.loads(self.run_tool("manage_records", "confirm", record["id"], "--role", "self", "--reviewed").stdout)
        self.assertEqual(recovered["status"], "confirmed")
        self.assertEqual(recovered["fields"], published["fields"])
        self.assertEqual(recovered["confirmed_at"], published["confirmed_at"])
        self.assertEqual(profile_path.read_bytes(), profile_bytes)
        self.assertEqual(len(json.loads(profile_bytes)["records"]["检验结果"]), 1)
        repeated = json.loads(self.run_tool("manage_records", "confirm", record["id"], "--role", "self", "--reviewed").stdout)
        self.assertEqual(repeated, recovered)

    @unittest.skipIf(os.name == "nt", "symlink permissions differ on Windows")
    def test_import_refuses_symlink_storage_and_traversal(self):
        outside = self.home / "outside"
        outside.mkdir()
        self.health.mkdir(parents=True)
        for dirname in ("raw", "imports"):
            link = self.health / dirname
            link.symlink_to(outside, target_is_directory=True)
            self.stage(ok=False)
            self.assertEqual(list(outside.iterdir()), [])
            link.unlink()
        record = self.stage(with_fields=True)
        record_path = self.health / "imports" / (record["id"] + ".json")
        record["raw_file"] = "raw/../../synthetic.txt"
        self.write(record_path, record)
        self.run_tool("manage_records", "confirm", record["id"], "--role", "self", "--reviewed", ok=False)
        self.assertFalse((self.health / "user-profile.json").exists())

    def test_malformed_import_and_profile_fail_closed(self):
        for extraction in (None, [], {"source": None}):
            self.write(self.extraction, extraction)
            self.stage(ok=False)
        self.write(self.extraction, self.payload)
        record = self.stage(with_fields=True)
        profile_path = self.health / "user-profile.json"
        self.write(profile_path, None)
        self.run_tool("manage_records", "confirm", record["id"], "--role", "self", "--reviewed", ok=False)
        self.assertEqual(profile_path.read_text(), "null")
        record_path = self.health / "imports" / (record["id"] + ".json")
        for invalid in (None, []):
            self.write(record_path, invalid)
            original = record_path.read_bytes()
            self.run_tool("manage_records", "review", record["id"], "--role", "self", "--fields-json", self.fields, ok=False)
            self.assertEqual(record_path.read_bytes(), original)

    def start_session(self):
        return json.loads(self.run_tool("consultation_session", "start", "--role", "self", "--chief", "合成症状").stdout)

    def test_unknown_or_urgent_sessions_cannot_finish_routinely(self):
        session = self.start_session()
        for disposition in ("observe", "soon"):
            self.run_tool("consultation_session", "finish", session["id"], "--role", "self", "--disposition", disposition, "--basis", "测试依据", ok=False)
        self.run_tool("consultation_session", "update", session["id"], "--role", "self", "--triage", "present", "--basis", "合成危险信号")
        for status in ("absent", "unknown"):
            self.run_tool("consultation_session", "update", session["id"], "--role", "self", "--triage", status, "--basis", "尝试降级", ok=False)
        for disposition in ("observe", "soon", "undetermined"):
            self.run_tool("consultation_session", "finish", session["id"], "--role", "self", "--disposition", disposition, "--basis", "测试依据", ok=False)
        final = json.loads(self.run_tool("consultation_session", "finish", session["id"], "--role", "self", "--disposition", "immediate", "--basis", "已提供紧急就医协助").stdout)
        self.assertEqual(final["stage"], "complete")
        self.assertEqual(final["triage"]["status"], "present")

    def test_observation_requires_confirmed_facts_and_triage_basis(self):
        session = self.start_session()
        self.run_tool("consultation_session", "update", session["id"], "--role", "self", "--triage", "absent", ok=False)
        self.run_tool("consultation_session", "update", session["id"], "--role", "self", "--triage", "absent", "--basis", "逐项核对，用户否认")
        self.run_tool("consultation_session", "finish", session["id"], "--role", "self", "--disposition", "observe", "--basis", "测试依据", ok=False)
        facts = self.home / "facts.json"
        self.write(facts, {"duration": {"status": "present", "value": "1 天", "source": "用户自述"},
                           "severity": {"status": "present", "value": "2/10", "source": "用户自述"},
                           "allergies": {"status": "denied", "source": "用户明确否认"},
                           "medications": {"status": "denied", "source": "用户明确否认"}})
        self.run_tool("consultation_session", "update", session["id"], "--role", "self", "--facts-json", facts)
        result = json.loads(self.run_tool("consultation_session", "finish", session["id"], "--role", "self", "--disposition", "observe", "--basis", "合成数据场景").stdout)
        self.assertEqual(result["missing_information"], [])
        self.run_tool("doctor", "init")
        self.run_tool("doctor", "check")

    def test_session_malformed_data_unchanged(self):
        session = self.start_session()
        path = self.health / "sessions" / (session["id"] + ".json")
        for invalid in (None, [], dict(session, triage={"status": "absent", "basis": ""})):
            self.write(path, invalid)
            original = path.read_bytes()
            self.run_tool("consultation_session", "update", session["id"], "--role", "self", "--triage", "unknown", ok=False)
            self.assertEqual(path.read_bytes(), original)

    def test_proxy_never_reads_or_writes_personal_files(self):
        self.health.mkdir(parents=True)
        (self.health / "user-profile.json").write_text("corrupt", encoding="utf-8")
        snapshot = {str(p.relative_to(self.health)): p.read_bytes() for p in self.health.rglob("*") if p.is_file()}
        proxy = json.loads(self.run_tool("consultation_session", "start", "--role", "proxy", "--chief", "代问合成症状").stdout)
        self.assertFalse(proxy["persistent"])
        self.run_tool("consultation_session", "show", str(uuid.uuid4()), "--role", "proxy", ok=False)
        self.run_tool("manage_records", "stage", "--role", "proxy", "--file", "missing", "--extraction-json", "missing", ok=False)
        for command in ("init", "check"):
            result = self.run_tool("doctor", command, "--role", "proxy", ok=False)
            self.assertIn("代问诊禁止", result.stderr)
            self.assertNotIn("JSON", result.stderr)
        self.run_tool("doctor", "export", "--role", "proxy", "--output", self.home / "proxy.zip", ok=False)
        self.assertFalse((self.home / "proxy.zip").exists())
        self.assertEqual(snapshot, {str(p.relative_to(self.health)): p.read_bytes() for p in self.health.rglob("*") if p.is_file()})

    def test_doctor_init_preserves_data_and_exports_only_health_records(self):
        self.run_tool("doctor", "init")
        profile_path = self.health / "user-profile.json"
        profile = json.loads(profile_path.read_text())
        profile["owner"]["name"] = "合成本人"
        self.write(profile_path, profile)
        self.run_tool("doctor", "init")
        self.assertEqual(json.loads(profile_path.read_text()), profile)
        record = self.stage(with_fields=True)
        archive = self.home / "export.zip"
        self.run_tool("doctor", "export", "--output", archive)
        with zipfile.ZipFile(archive) as exported:
            self.assertEqual(exported.read("health-records/user-profile.json"), profile_path.read_bytes())
            self.assertEqual(exported.read("health-records/" + record["raw_file"]), self.source.read_bytes())
            self.assertTrue(all(name.startswith("health-records/") and not name.endswith(".lock") for name in exported.namelist()))
        original = archive.read_bytes()
        self.run_tool("doctor", "export", "--output", archive, ok=False)
        self.assertEqual(archive.read_bytes(), original)
        self.run_tool("doctor", "export", "--output", self.health / "recursive.zip", ok=False)

    def test_doctor_null_profile_not_initialized_or_overwritten(self):
        self.write(self.health / "user-profile.json", None)
        self.run_tool("doctor", "init", ok=False)
        self.assertEqual((self.health / "user-profile.json").read_text(), "null")
        self.assertFalse((self.health / "types.json").exists())
        check = json.loads(self.run_tool("doctor", "check", ok=False).stdout)
        self.assertFalse(check["initialized"])
        self.assertTrue(check["json_errors"])

    def test_optional_renderer_import_failure_does_not_fail_core_check(self):
        self.run_tool("doctor", "init")
        stub = self.home / "renderer"
        stub.mkdir()
        (stub / "weasyprint.py").write_text("raise OSError('synthetic missing renderer library')\n", encoding="utf-8")
        self.env["PYTHONPATH"] = str(stub)
        check = json.loads(self.run_tool("doctor", "check").stdout)
        renderer = check["optional_dependencies"]["weasyprint"]
        self.assertTrue(renderer["installed"])
        self.assertFalse(renderer["usable"])
        self.assertIn("synthetic missing renderer library", renderer["error"])
        self.assertNotIn("Traceback", renderer["error"])

    @unittest.skipIf(os.name == "nt", "symlink permissions differ on Windows")
    def test_doctor_exports_and_checks_refuse_symlinks(self):
        self.run_tool("doctor", "init")
        (self.health / "external.txt").symlink_to(self.source)
        self.run_tool("doctor", "check", ok=False)
        output = self.home / "export.zip"
        self.run_tool("doctor", "export", "--output", output, ok=False)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
