"""Synthetic owner profile update tests; subprocesses use an isolated workspace."""
from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/family-doctor/scripts/update_profile.py"


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="profile-test-")
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.health = self.workspace / "health-records"
        self.profile = self.health / "user-profile.json"
        self.environment = dict(os.environ, FAMILY_DOCTOR_HOME=str(self.workspace), PYTHONDONTWRITEBYTECODE="1")
        self.initial = {"version": "3.0", "onboarding": {"status": "complete"},
                        "owner": {"name": "Synthetic", "age": 20, "allergies": [],
                                  "chronicConditions": [], "familyHistory": [],
                                  "factStatus": {"allergies": "not_asked"},
                                  "emergencyContact": {"name": "Test", "phone": "123456"}},
                        "records": {"sentinel": [{"retained": True}]}, "consultations": ["retained"]}
        self.write(self.initial)

    def write(self, data):
        self.health.mkdir(parents=True, exist_ok=True)
        self.profile.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def run_update(self, *args, success=True):
        process = subprocess.run([sys.executable, str(SCRIPT), *args], env=self.environment,
                                 capture_output=True, text=True, timeout=10)
        if success:
            self.assertEqual(process.returncode, 0, process.stderr)
        else:
            self.assertNotEqual(process.returncode, 0, process.stdout)
        return process

    def read(self):
        return json.loads(self.profile.read_text(encoding="utf-8"))

    def add(self, *extra):
        return self.run_update("add", "--field", "allergies", "--value", "synthetic allergy",
                               "--source", "synthetic user statement", *extra)

    def test_add_preserves_source_confirmation_and_other_records(self):
        self.add("--json")
        profile = self.read()
        fact = profile["owner"]["allergies"][0]
        self.assertEqual(fact["source"], {"type": "user_report", "reference": "synthetic user statement"})
        self.assertEqual(fact["confirmation_status"], "unconfirmed")
        self.assertTrue(fact["recorded_at"])
        self.assertIsNone(fact["confirmed_at"])
        self.assertEqual(profile["owner"]["factStatus"]["allergies"], "present")
        for key in ("records", "consultations", "onboarding"):
            self.assertEqual(profile[key], self.initial[key])
        self.add("--confirmed")
        facts = self.read()["owner"]["allergies"]
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["confirmation_status"], "confirmed")
        self.assertTrue(facts[0]["confirmed_at"])
        self.assertEqual(facts[0]["recorded_at"], fact["recorded_at"])

    def test_document_fact_and_other_fields(self):
        self.run_update("add", "--field", "chronicConditions", "--value", "synthetic condition",
                        "--source", "synthetic report page 2", "--source-type", "document", "--confirmed")
        fact = self.read()["owner"]["chronicConditions"][0]
        self.assertEqual(fact["source"]["type"], "document")
        self.assertEqual(fact["confirmation_status"], "confirmed")
        self.assertEqual(self.read()["owner"]["allergies"], [])

    def test_empty_fact_or_source_rejected_without_write(self):
        before = self.profile.read_bytes()
        for value, source in ((" ", "test"), ("test", " ")):
            self.run_update("add", "--field", "familyHistory", "--value", value, "--source", source, success=False)
        self.assertEqual(self.profile.read_bytes(), before)

    def test_status_requires_explicit_replace_and_keeps_backup(self):
        self.add()
        before = self.profile.read_bytes()
        self.run_update("status", "--field", "allergies", "--status", "denied", success=False)
        self.assertEqual(self.profile.read_bytes(), before)
        self.run_update("status", "--field", "allergies", "--status", "denied", "--replace", "--source", "synthetic correction")
        owner = self.read()["owner"]
        self.assertEqual(owner["allergies"], [])
        self.assertEqual(owner["factStatus"]["allergies"], "denied")
        self.assertEqual(owner["factStatusEvidence"]["allergies"]["source"]["reference"], "synthetic correction")
        self.assertTrue(any(file.read_bytes() == before for file in (self.health / ".backups").glob("*.bak")))
        for status in ("unknown", "not_asked"):
            self.run_update("status", "--field", "allergies", "--status", status)
            self.assertEqual(self.read()["owner"]["factStatus"]["allergies"], status)

    def test_identity_updates_only_requested_fields_and_validates_age(self):
        self.run_update("identity", "--nickname", "Synthetic nickname", "--gender", "unknown", "--age", "30")
        owner = self.read()["owner"]
        self.assertEqual(owner["name"], "Synthetic")
        self.assertEqual(owner["age"], 30)
        self.assertEqual(owner["nickname"], "Synthetic nickname")
        before = self.profile.read_bytes()
        for args in (("--age", "-1"), ("--age", "150"), ("--age", "1.5"), ("--gender", "invalid"),
                     ("--birth-date", "2999-01-01"), ("--name", " "), ()):
            self.run_update("identity", *args, success=False)
            self.assertEqual(self.profile.read_bytes(), before)
        self.run_update("identity", "--birth-date", date.today().isoformat(), "--age", "0")
        self.assertEqual(self.read()["owner"]["age"], 0)

    def test_contact_preserves_other_contact_fields(self):
        self.run_update("contact", "--relationship", "synthetic relation", "--phone", "+86 123456789")
        contact = self.read()["owner"]["emergencyContact"]
        self.assertEqual(contact["name"], "Test")
        self.assertEqual(contact["relationship"], "synthetic relation")
        before = self.profile.read_bytes()
        self.run_update("contact", "--phone", "invalid", success=False)
        self.assertEqual(self.profile.read_bytes(), before)

    def test_proxy_rejected_before_read_or_lock(self):
        self.profile.write_text("{broken", encoding="utf-8")
        before = sorted(path.name for path in self.health.iterdir())
        process = self.run_update("identity", "--name", "test", "--role", "proxy", success=False)
        self.assertIn("代问诊禁止", process.stderr)
        self.assertNotIn("损坏", process.stderr)
        self.assertEqual(sorted(path.name for path in self.health.iterdir()), before)
        self.assertEqual(self.profile.read_text(), "{broken")

    def test_malformed_profile_never_resets(self):
        malformed = [[], {"owner": []}, {"owner": {"allergies": {}}}, {"owner": {"allergies": [None]}},
                     {"owner": {"factStatus": []}}, {"owner": {"emergencyContact": "invalid"}}]
        for data in malformed:
            self.write(data)
            before = self.profile.read_bytes()
            self.run_update("identity", "--name", "test", success=False)
            self.assertEqual(self.profile.read_bytes(), before)
        self.profile.write_text("{broken", encoding="utf-8")
        self.run_update("identity", "--name", "test", success=False)
        self.assertEqual(self.profile.read_text(), "{broken")

    def test_missing_profile_does_not_initialize_or_reset(self):
        self.profile.unlink()
        self.run_update("identity", "--name", "test", success=False)
        self.assertFalse(self.profile.exists())

    def test_legacy_facts_are_preserved_without_invented_provenance(self):
        self.initial["owner"]["allergies"] = ["legacy synthetic fact"]
        self.write(self.initial)
        self.add()
        facts = self.read()["owner"]["allergies"]
        self.assertEqual(facts[0], "legacy synthetic fact")
        self.assertEqual(facts[1]["source"]["type"], "user_report")


if __name__ == "__main__":
    unittest.main()
