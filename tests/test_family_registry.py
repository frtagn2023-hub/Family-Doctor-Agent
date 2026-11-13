"""Synthetic same-machine household routing and concurrent registry tests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "family-doctor" / "scripts"


class FamilyRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="family-registry-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.home = self.base / "household"
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("FAMILY_DOCTOR_", "HERMES_SESSION_"))}
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.command("init", "--name", "合成测试家庭")
        self.father = json.loads(self.command("add-member", "--name", "张父", "--profile", "father").stdout)
        self.child = json.loads(self.command("add-member", "--name", "张女", "--profile", "daughter").stdout)

    def command(self, *args, ok=True, env=None):
        result = subprocess.run([sys.executable, str(ROOT / "family.py"), "--home", str(self.home)] + list(args),
                                env=env or self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        return result

    def member_home(self, member):
        return self.home / "members" / member["id"]

    def inspect_context(self, member=None, profile="father", env_extra=None, ok=True):
        member = member or self.father
        env = dict(self.env, FAMILY_DOCTOR_FAMILY_HOME=str(self.home), FAMILY_DOCTOR_PROFILE=profile,
                   FAMILY_DOCTOR_MEMBER_ID=member["id"], FAMILY_DOCTOR_HOME=str(self.member_home(member)))
        env.update(env_extra or {})
        script = "import sys,json; sys.path.insert(0,sys.argv[1]); from family_context import current_context,provenance; print(json.dumps({'context':current_context(),'provenance':provenance()}))"
        result = subprocess.run([sys.executable, "-c", script, str(SCRIPTS)], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        return json.loads(result.stdout) if ok else result

    def test_names_displayed_but_storage_uses_stable_member_id(self):
        listing = json.loads(self.command("members").stdout)
        self.assertEqual(listing["name"], "合成测试家庭")
        self.assertEqual({item["name"] for item in listing["members"]}, {"张父", "张女"})
        for member in (self.father, self.child):
            self.assertRegex(member["id"], r"^m_[0-9a-f]{32}$")
            data = json.loads((self.member_home(member) / "health-records" / "user-profile.json").read_text())
            self.assertEqual(data["owner"]["name"], member["name"])
            self.assertEqual(data["owner"]["member_id"], member["id"])

    def test_profiles_share_household_not_hermes_memory_or_member_directory(self):
        personal = self.command("--profile", "father", "run", "add_symptom", "--", "add", "--symptom", "合成症状", "--severity", "2")
        self.assertEqual(personal.returncode, 0)
        self.assertTrue((self.member_home(self.father) / "health-records" / "symptoms.json").exists())
        self.assertFalse((self.member_home(self.child) / "health-records" / "symptoms.json").exists())
        self.assertFalse((self.home / "health-records").exists())
        self.assertFalse((self.home / "MEMORY.md").exists())
        context = self.inspect_context(self.father, "daughter")
        self.assertEqual(context["context"]["report_mode"], "proxy")
        self.assertEqual(context["provenance"]["reporter_name"], "张女")
        self.assertEqual(context["provenance"]["subject_name"], "张父")

    def test_registered_proxy_targets_subject_without_touching_actor(self):
        before = (self.member_home(self.child) / "health-records" / "user-profile.json").read_bytes()
        self.command("--profile", "daughter", "--member", "张父", "run", "add_symptom", "--", "add", "--symptom", "家属观察的合成症状", "--severity", "2")
        symptoms = json.loads((self.member_home(self.father) / "health-records" / "symptoms.json").read_text())
        self.assertEqual(symptoms["symptoms"][0]["symptom"], "家属观察的合成症状")
        self.assertEqual((self.member_home(self.child) / "health-records" / "user-profile.json").read_bytes(), before)

    def test_unknown_profiles_and_outsiders_are_not_automatically_enrolled(self):
        before = (self.home / "family.json").read_bytes()
        self.command("--profile", "stranger", "run", "welcome", "--skip", ok=False)
        self.command("--profile", "father", "--member", "未登记的朋友", "run", "welcome", "--skip", ok=False)
        self.assertEqual((self.home / "family.json").read_bytes(), before)
        self.assertEqual(len(list((self.home / "members").iterdir())), 2)

    def test_binding_cannot_silently_move_profile_between_people(self):
        before = (self.home / "family.json").read_bytes()
        self.command("bind-profile", "--member", "张女", "--profile", "father", ok=False)
        self.command("add-member", "--name", "另一人", "--profile", "father", ok=False)
        self.assertEqual((self.home / "family.json").read_bytes(), before)
        self.command("bind-profile", "--member", "张父", "--profile", "father")
        self.command("bind-profile", "--member", "张父", "--profile", "father-tablet")
        self.assertEqual(self.inspect_context(profile="father-tablet")["context"]["actor_member_id"], self.father["id"])

    def test_duplicate_names_require_member_id(self):
        another = json.loads(self.command("add-member", "--name", "张父", "--profile", "another").stdout)
        self.command("--profile", "father", "--member", "张父", "run", "welcome", "--skip", ok=False)
        self.command("--profile", "another", "--member", another["id"], "run", "welcome", "--skip")
        self.assertNotEqual(another["id"], self.father["id"])

    def test_explicit_role_cannot_override_person_routing_even_abbreviated(self):
        for flag in ("--role", "--rol", "--ro"):
            self.command("--profile", "daughter", "--member", "张父", "run", "add_symptom", "add", "--symptom", "合成", flag, "self", ok=False)
        self.command("--profile", "father", "run", "welcome", "--skip", "--role=proxy", ok=False)
        self.command("--profile", "father", "run", "welcome", "--skip", "--role=本人")

    def test_doctor_cannot_override_member_workspace(self):
        other = self.base / "unrelated"
        for flag in ("--data-dir", "--data-d"):
            self.command("--profile", "father", "run", "doctor", flag, str(other), "init", ok=False)
        self.command("--profile", "father", "run", "doctor", "--data-dir=" + str(other), "init", ok=False)
        self.assertFalse(other.exists())

    def test_mismatched_direct_workspace_rejected(self):
        self.inspect_context(env_extra={"FAMILY_DOCTOR_HOME": str(self.member_home(self.child))}, ok=False)
        self.inspect_context(env_extra={"FAMILY_DOCTOR_MEMBER_ID": "../../elsewhere"}, ok=False)

    def test_symlink_member_directory_rejected(self):
        original = self.member_home(self.father)
        saved = self.base / "saved"
        original.rename(saved)
        original.symlink_to(saved, target_is_directory=True)
        self.command("--profile", "father", "run", "welcome", "--skip", ok=False)
        self.inspect_context(ok=False)

    def test_corrupt_registry_not_reset(self):
        path = self.home / "family.json"
        path.write_bytes(b"broken")
        for command in (("init",), ("members",), ("add-member", "--name", "测试")):
            self.command(*command, ok=False)
        self.assertEqual(path.read_bytes(), b"broken")

    def test_missing_profile_stops_instead_of_guessing_from_name_or_cwd(self):
        self.command("run", "welcome", "--skip", ok=False)
        env = dict(self.env, HERMES_SESSION_PROFILE="father")
        self.command("run", "welcome", "--skip", env=env)
        env = dict(env, FAMILY_DOCTOR_PROFILE="daughter")
        self.command("run", "welcome", "--skip", env=env)

    def test_gateway_metadata_preserved_as_observed_not_identity(self):
        result = self.inspect_context(env_extra={"HERMES_SESSION_ACTOR_ID": "synthetic-sender",
                                                "HERMES_SESSION_MESSAGE_ID": "synthetic-message"})
        self.assertEqual(result["provenance"]["observed_actor_id"], "synthetic-sender")
        self.assertEqual(result["provenance"]["identity_basis"], "configured_profile_binding")
        self.assertEqual(result["provenance"]["reporter_member_id"], self.father["id"])

    def test_concurrent_member_registration_keeps_all_bindings(self):
        processes = [subprocess.Popen([sys.executable, str(ROOT / "family.py"), "--home", str(self.home),
                                      "add-member", "--name", "测试成员" + str(i), "--profile", "test-" + str(i)],
                                     env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for i in range(5)]
        for process in processes:
            stdout, stderr = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, stdout + stderr)
        data = json.loads(self.command("members").stdout)
        self.assertEqual(len(data["members"]), 7)
        self.assertEqual(sum(len(member["profiles"]) for member in data["members"]), 7)


if __name__ == "__main__":
    unittest.main()
