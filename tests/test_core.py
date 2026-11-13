"""Isolated regression tests: never touch a user's actual health workspace."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "family-doctor" / "scripts"


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="family-doctor-test-")
        self.workspace = pathlib.Path(self.temporary.name)
        self.health = self.workspace / "health-records"
        self.environment = dict(os.environ, FAMILY_DOCTOR_HOME=str(self.workspace), PYTHONDONTWRITEBYTECODE="1")

    def tearDown(self):
        self.temporary.cleanup()

    def run_script(self, script, *args, success=True):
        result = subprocess.run([sys.executable, str(SCRIPTS / (script + ".py")), *args],
                                env=self.environment, text=True, capture_output=True)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def write(self, name, data):
        self.health.mkdir(parents=True, exist_ok=True)
        path = self.health / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def add_med(self, name="阿司匹林"):
        return json.loads(self.run_script("add_medication", "add", "--name", name, "--dose", "100mg",
                                          "--freq", "qd", "--json").stdout)

    def test_medication_round_trip_and_stopped_state(self):
        record = self.add_med()
        listed = json.loads(self.run_script("list_medications", "--json").stdout)
        self.assertEqual(listed[0]["dose"], "100mg")
        result = json.loads(self.run_script("check_interactions", "--new", "华法林", "--json").stdout)
        self.assertEqual(result["pairs"][0]["assessment"], "potential_interaction")
        self.run_script("add_medication", "stop", "--id", record["id"])
        self.assertEqual(json.loads(self.run_script("list_medications", "--current-only", "--json").stdout), [])
        result = json.loads(self.run_script("check_interactions", "--new", "华法林", "--json").stdout)
        self.assertEqual(result["pairs"], [])
        self.run_script("add_medication", "status", "--id", record["id"], "--status", "active")
        self.assertEqual(len(json.loads(self.run_script("list_medications", "--current-only", "--json").stdout)), 1)

    def test_aliases_share_rules_and_unknown_is_not_safe(self):
        results = []
        for pair in (("华法林", "阿司匹林"), ("warfarin", "aspirin"), (" ASPIRIN ", "Warfarin")):
            result = json.loads(self.run_script("check_interactions", "--drug1", pair[0], "--drug2", pair[1], "--json").stdout)
            results.append(result["pairs"][0]["assessment"])
        self.assertEqual(results, ["potential_interaction"] * 3)
        for pair in (("阿司匹林加强版", "华法林"), ("metformin", "aspirin")):
            result = json.loads(self.run_script("check_interactions", "--drug1", pair[0], "--drug2", pair[1], "--json").stdout)
            self.assertEqual(result["pairs"][0]["assessment"], "unknown")
            self.assertEqual(result["pairs"][0]["coverage"], "limited_demo")

    def test_legacy_medication_migration_and_backup(self):
        path = self.write("medications.json", {"medications": [{"id": "old", "name": "华法林", "dosage": "1mg", "frequency": "qd", "ongoing": False}]})
        old = path.read_bytes()
        record = json.loads(self.run_script("list_medications", "--json").stdout)[0]
        self.assertEqual((record["dose"], record["status"]), ("1mg", "stopped"))
        self.assertEqual(path.read_bytes(), old, "reads must not rewrite legacy data")
        self.add_med()
        data = json.loads(path.read_text())
        self.assertEqual(data["version"], "3.0")
        self.assertNotIn("dosage", data["medications"][0])
        backups = list((self.health / ".backups").glob("medications.json.*.bak"))
        self.assertTrue(any(backup.read_bytes() == old for backup in backups))
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)

    def test_vitals_round_trip_uses_measurement_time(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        for time in (recent, old):
            self.run_script("add_vital", "add", "--type", "blood_pressure", "--systolic", "120", "--diastolic", "80", "--measured-at", time)
        result = json.loads(self.run_script("get_vital_trends", "--type", "bp", "--days", "30", "--json").stdout)
        group = result["groups"][0]
        self.assertEqual(group["count"], 1)
        self.assertEqual(group["unit"], "mmHg")
        self.assertEqual(group["statistics"]["systolic"]["average"], 120)
        result2 = json.loads(self.run_script("add_vital", "trends", "--type", "blood_pressure", "--days", "30", "--json").stdout)
        self.assertEqual(result2["groups"], result["groups"])

    def test_units_and_glucose_context_not_mixed(self):
        for unit, value in (("mmol/L", "5"), ("mg/dL", "90")):
            self.run_script("add_vital", "add", "--type", "blood_sugar", "--value", value, "--unit", unit, "--fasting")
        self.run_script("add_vital", "add", "--type", "blood_sugar", "--value", "6", "--context", "after_meal")
        groups = json.loads(self.run_script("get_vital_trends", "--type", "glucose", "--json").stdout)["groups"]
        self.assertEqual(len(groups), 3)
        self.assertTrue(all(group["count"] == 1 for group in groups))

    def test_legacy_vitals_keep_unknown_unit_and_no_average(self):
        timestamp = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        path = self.write("vitals.json", {"vitals": {"glucose": [{"timestamp": timestamp, "value": 90}]}})
        old = path.read_bytes()
        groups = json.loads(self.run_script("get_vital_trends", "--json").stdout)["groups"]
        self.assertIsNone(groups[0]["unit"])
        self.assertNotIn("statistics", groups[0])
        self.run_script("add_vital", "add", "--type", "weight", "--value", "60")
        data = json.loads(path.read_text())
        self.assertIsNone(data["vitals"][0]["unit"])
        self.assertEqual(data["vitals"][0]["type"], "blood_sugar")
        self.assertEqual(data["vitals"][0]["measured_at"], timestamp)
        self.assertTrue(any(p.read_bytes() == old for p in (self.health / ".backups").glob("*.bak")))

    def test_invalid_values_do_not_create_records(self):
        for args in (("--type", "blood_pressure"), ("--type", "blood_pressure", "--systolic", "80", "--diastolic", "120"),
                     ("--type", "weight", "--value", "-1"), ("--type", "weight", "--value", "nan"),
                     ("--type", "spo2", "--value", "101"), ("--type", "blood_sugar", "--value", "5", "--unit", "kg")):
            self.run_script("add_vital", "add", *args, success=False)
        self.assertFalse((self.health / "vitals.json").exists())

    def test_symptom_round_trip_preserves_unknown_fields(self):
        self.run_script("add_symptom", "add", "--symptom", "头痛", "--severity", "20", success=False)
        self.run_script("add_symptom", "add", "--symptom", "头痛", "--severity", "3")
        symptoms = json.loads(self.run_script("add_symptom", "list", "--json").stdout)
        self.assertEqual(symptoms[0]["severity"], 3)
        self.assertIsNone(symptoms[0]["associated"])
        self.assertEqual(symptoms[0]["source"], {"type": "user_report", "reference": "用户自述/手动录入"})
        self.assertEqual(symptoms[0]["confirmation_status"], "unconfirmed")

    def test_new_records_keep_user_source_and_explicit_confirmation(self):
        cases = [("add_medication", ["--name", "测试药", "--dose", "1mg", "--freq", "qd"]),
                 ("add_vital", ["--type", "weight", "--value", "60"]),
                 ("add_symptom", ["--symptom", "头痛", "--severity", "3"])]
        for script, args in cases:
            record = json.loads(self.run_script(script, "add", *args, "--source", "用户核对原始记录", "--confirmed", "--json").stdout)
            self.assertEqual(record["source"], {"type": "user_report", "reference": "用户核对原始记录"})
            self.assertEqual(record["confirmation_status"], "confirmed")
            self.assertEqual(record["confirmed_by"], "user")
            self.assertTrue(record["confirmed_at"])

    def test_proxy_refused_before_any_read_or_write(self):
        # If a script reads first, these files would fail as corrupt JSON instead.
        self.health.mkdir()
        for name in ("medications.json", "vitals.json", "symptoms.json"):
            (self.health / name).write_text("{broken", encoding="utf-8")
        before = {p.name: p.read_bytes() for p in self.health.iterdir()}
        cases = [("add_medication", ["add", "--name", "test", "--dose", "1", "--freq", "qd"]),
                 ("list_medications", []), ("check_interactions", ["--new", "test"]),
                 ("get_vital_trends", []), ("add_vital", ["add", "--type", "weight", "--value", "60"]),
                 ("add_symptom", ["add", "--symptom", "头痛", "--severity", "3"])]
        for script, args in cases:
            result = self.run_script(script, *args, "--role", "proxy", success=False)
            self.assertIn("代问诊禁止", result.stderr)
            self.assertNotIn("损坏", result.stderr)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.health.iterdir()})

    def test_corrupt_json_is_preserved(self):
        self.health.mkdir()
        path = self.health / "medications.json"
        path.write_text("{broken", encoding="utf-8")
        result = self.run_script("add_medication", "add", "--name", "test", "--dose", "1mg", "--freq", "qd", success=False)
        self.assertIn("损坏", result.stderr)
        self.assertEqual(path.read_text(), "{broken")
        self.assertFalse((self.health / ".backups").exists())

    def test_invalid_schema_and_nonfinite_legacy_json_are_not_reset(self):
        self.health.mkdir()
        path = self.health / "medications.json"
        for content in ("null", '{"wrong_key": []}', '{"medications": [], "number": NaN}'):
            path.write_text(content, encoding="utf-8")
            self.run_script("add_medication", "add", "--name", "test", "--dose", "1mg", "--freq", "qd", success=False)
            self.assertEqual(path.read_text(), content)

    def test_concurrent_updates_keep_every_record(self):
        processes = [subprocess.Popen([sys.executable, str(SCRIPTS / "add_medication.py"), "add", "--name", "测试药{}".format(i),
                                       "--dose", "1mg", "--freq", "qd"], env=self.environment, text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(12)]
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, stderr or stdout)
        meds = json.loads((self.health / "medications.json").read_text())["medications"]
        self.assertEqual(len(meds), 12)
        self.assertEqual(len({med["id"] for med in meds}), 12)
        self.assertEqual(len({med["name"] for med in meds}), 12)


if __name__ == "__main__":
    unittest.main()
