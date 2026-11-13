"""Synthetic household integration: attribution, episode filing and retries."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "family-doctor" / "scripts"


class FamilyWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="family-workflows-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.home = self.base / "household"
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("FAMILY_DOCTOR_", "HERMES_SESSION_"))}
        self.env.update(PYTHONDONTWRITEBYTECODE="1", FAMILY_DOCTOR_TIMEZONE="Asia/Shanghai")
        self.family("init", "--name", "合成测试家庭")
        self.father = json.loads(self.family("add-member", "--name", "张父", "--profile", "father").stdout)
        self.daughter = json.loads(self.family("add-member", "--name", "张女", "--profile", "daughter").stdout)
        self.health = self.home / "members" / self.father["id"] / "health-records"
        self.actor_health = self.home / "members" / self.daughter["id"] / "health-records"
        self.event = self.write("event.json", {
            "symptoms": [{"key": "头痛", "site": "额部", "polarity": "present"}],
            "symptoms_complete": True, "start": "2026-11-01T09:00:00+08:00",
            "end": "2026-11-01T11:00:00+08:00", "precision": "minute",
            "course": "new", "time_text": "11 月 1 日上午九点至十一点额部头痛"})

    def write(self, name, data):
        path = self.base / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def family(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(ROOT / "family.py"), "--home", str(self.home)] + list(map(str, args)),
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        return result

    def tool(self, profile, tool, *args, ok=True):
        return self.family("--profile", profile, "--member", self.father["id"], "run", tool, "--", *args, ok=ok)

    def profile(self):
        return json.loads((self.health / "user-profile.json").read_text())

    def add(self, profile="father", event=True, *extra, chief="合成额部头痛", ok=True):
        args = ["add", "--chief-complaint", chief]
        if event:
            args += ["--event-json", self.event if event is True else event]
        return self.tool(profile, "add_consultation", *args, *extra, ok=ok)

    def assert_attributed(self, record, member=None, mode="proxy"):
        member = member or self.daughter
        self.assertEqual(record["subject_member_id"], self.father["id"])
        self.assertEqual(record["reporter_member_id"], member["id"])
        self.assertEqual(record["report_mode"], mode)

    def source_files(self):
        return {record["id"]: (self.health / record["file"]).read_bytes()
                for record in self.profile()["consultations"]}

    def test_proxy_observations_medicines_vitals_and_facts_keep_source_person(self):
        actor_before = (self.actor_health / "user-profile.json").read_bytes()
        self.tool("daughter", "add_symptom", "add", "--symptom", "合成症状", "--severity", "2", "--confirmed")
        self.tool("daughter", "add_medication", "add", "--name", "合成药品", "--dose", "处方所述剂量", "--freq", "处方所述频率", "--confirmed")
        self.tool("daughter", "add_vital", "add", "--type", "temperature", "--value", "36.7", "--unit", "°C", "--confirmed")
        self.tool("daughter", "update_profile", "add", "--field", "allergies", "--value", "合成过敏记录", "--source", "女儿代述", "--confirmed")
        for filename, key in (("symptoms.json", "symptoms"), ("medications.json", "medications"), ("vitals.json", "vitals")):
            record = json.loads((self.health / filename).read_text())[key][0]
            self.assert_attributed(record)
            self.assertEqual(record["source"]["type"], "caregiver_report")
            self.assertEqual(record["confirmed_by_member_id"], self.daughter["id"])
            self.assertFalse((self.actor_health / filename).exists())
        fact = self.profile()["owner"]["allergies"][0]
        self.assert_attributed(fact)
        self.assertEqual(fact["source"]["type"], "caregiver_report")
        self.assertEqual((self.actor_health / "user-profile.json").read_bytes(), actor_before)

    def test_family_session_preserves_creator_and_later_fact_provider(self):
        session = json.loads(self.tool("daughter", "consultation_session", "start", "--chief", "代述合成症状").stdout)
        self.assert_attributed(session)
        self.assertTrue((self.health / "sessions" / (session["id"] + ".json")).exists())
        self.assertFalse((self.actor_health / "sessions").exists())
        facts = self.write("facts.json", {"duration": {"status": "present", "value": "两小时", "source": "本人补充"}})
        updated = json.loads(self.tool("father", "consultation_session", "update", session["id"], "--facts-json", facts).stdout)
        self.assert_attributed(updated)
        self.assert_attributed(updated["facts"]["duration"], self.father, "self")
        self.assertEqual(updated["last_updated_by"]["reporter_member_id"], self.father["id"])

    def test_document_uploaded_by_family_confirmed_by_self_retains_both_people(self):
        raw = self.base / "synthetic.txt"
        raw.write_text("Synthetic result 1.2 mg/L", encoding="utf-8")
        extraction = self.write("extraction.json", {
            "source": {"sha256": hashlib.sha256(raw.read_bytes()).hexdigest()}, "status": "ok", "page_count": 1,
            "pages": [{"page_number": 1, "status": "ok", "text": raw.read_text()}]})
        fields = self.write("fields.json", [{"name": "合成指标", "value": 1.2, "unit": "mg/L", "observed_at": "2026-11-01",
                                           "source_page": 1, "original_text": "Synthetic result 1.2 mg/L"}])
        staged = json.loads(self.tool("daughter", "manage_records", "stage", "--file", raw,
                                     "--extraction-json", extraction, "--fields-json", fields).stdout)
        self.assert_attributed(staged)
        self.tool("father", "manage_records", "confirm", staged["id"], "--reviewed")
        entry = self.profile()["records"]["检验结果"][0]
        self.assert_attributed(entry)
        self.assertEqual(entry["confirmed_by"]["reporter_member_id"], self.father["id"])
        self.assert_attributed(entry["fields"][0], self.father, "self")
        self.assertFalse((self.actor_health / "imports").exists())

    def test_self_and_family_reports_merge_but_two_sources_and_names_survive(self):
        self.add("father", True, "--severity", "3", "--risk-level", "RED")
        self.add("daughter", True, "--severity", "1", "--risk-level", "GREEN", chief="家属补充的合成额部头痛")
        data = self.profile()
        self.assertEqual(data["summary"]["consultationCount"], 2)
        self.assertEqual(data["summary"]["episodeCount"], 1)
        self.assertEqual(len(self.source_files()), 2)
        self.assert_attributed(data["consultations"][0], self.father, "self")
        self.assert_attributed(data["consultations"][1])
        self.assertFalse(data["consultations"][0]["language_sample_eligible"])
        self.assertFalse(data["consultations"][1]["language_sample_eligible"])
        episode = data["consultation_episodes"][0]
        self.assertEqual(len(episode["report_ids"]), 2)
        self.assertTrue(episode["conflicts"])
        content = (self.health / "consultations" / "episodes" / (episode["id"] + ".md")).read_text()
        self.assertEqual({item["value"] for item in episode["risk_flags"]}, {"RED", "GREEN"})
        for text in ("张父", "张女", "红色风险", "绿色风险", "2026-11-01 09:00"):
            self.assertIn(text, content)
        listing = self.tool("daughter", "add_consultation", "list").stdout
        self.assertIn("1 个已整理健康事件", listing)
        self.assertIn("2 份问诊来源记录", listing)

    def test_same_reporting_day_different_occurrence_times_are_separate(self):
        self.add()
        later = json.loads(self.event.read_text())
        later.update(start="2026-11-01T16:00:00+08:00", end="2026-11-01T18:00:00+08:00")
        self.add("daughter", self.write("later.json", later))
        data = self.profile()
        self.assertEqual(data["consultations"][0]["date"], data["consultations"][1]["date"])
        self.assertEqual(len(data["consultation_episodes"]), 2)

    def test_historical_same_occurrence_merges_despite_different_report_dates(self):
        # A deterministic clock shim isolates reporting time from actual symptom time.
        env = dict(self.env, FAMILY_DOCTOR_FAMILY_HOME=str(self.home), FAMILY_DOCTOR_PROFILE="father",
                   FAMILY_DOCTOR_MEMBER_ID=self.father["id"], FAMILY_DOCTOR_HOME=str(self.health.parent))
        script = "import sys; sys.path.insert(0,sys.argv[1]); import add_consultation as c; c.now_iso=lambda:'2026-11-02T10:00:00+08:00'; sys.exit(c.main(sys.argv[2:]))"
        result = subprocess.run([sys.executable, "-c", script, str(SCRIPTS), "add", "--role", "self",
                                 "--chief-complaint", "历史合成额部头痛", "--event-json", str(self.event)],
                                env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.add("daughter")
        data = self.profile()
        self.assertNotEqual(data["consultations"][0]["date"], data["consultations"][1]["date"])
        self.assertEqual(len(data["consultation_episodes"]), 1)

    def test_unknown_occurrence_never_merges_just_because_words_match(self):
        self.add("father", False)
        self.add("daughter", False)
        self.assertEqual(len(self.profile()["consultation_episodes"]), 2)

    def test_explicit_factual_continuity_can_link_uncertain_times(self):
        self.add("father", False)
        episode_id = self.profile()["consultation_episodes"][0]["id"]
        self.add("daughter", False, "--episode-id", episode_id, ok=False)
        self.add("daughter", False, "--episode-id", episode_id, "--merge-basis", "两人已明确说明为同一次持续头痛，未出现新的发作")
        self.assertEqual(len(self.profile()["consultations"]), 2)
        self.assertEqual(len(self.profile()["consultation_episodes"]), 1)

    def test_retry_is_idempotent_changed_payload_rejected_other_profiles_independent(self):
        self.add("father", True, "--request-id", "message-1")
        sources = self.source_files()
        self.add("father", True, "--request-id", "message-1")
        self.assertEqual(self.source_files(), sources)
        self.add("father", True, "--request-id", "message-1", chief="已更改的合成症状", ok=False)
        self.assertEqual(self.source_files(), sources)
        self.add("daughter", True, "--request-id", "message-1")
        self.assertEqual(len(self.profile()["consultations"]), 2)
        self.assertEqual(len(self.profile()["consultation_episodes"]), 1)

    def test_concurrent_reports_converge_to_one_complete_episode(self):
        for name, profile in (("张子", "son"), ("张配偶", "spouse")):
            self.family("add-member", "--name", name, "--profile", profile)
        processes = []
        for profile in ("father", "daughter", "son", "spouse"):
            command = [sys.executable, str(ROOT / "family.py"), "--home", str(self.home), "--profile", profile,
                       "--member", self.father["id"], "run", "add_consultation", "--", "add",
                       "--chief-complaint", "并发合成额部头痛", "--event-json", str(self.event), "--request-id", "shared-id"]
            processes.append(subprocess.Popen(command, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, stdout + stderr)
        data = self.profile()
        self.assertEqual(len(data["consultations"]), 4)
        self.assertEqual(len(data["consultation_episodes"]), 1)
        episode = data["consultation_episodes"][0]
        self.assertEqual(len(episode["report_ids"]), 4)
        content = (self.health / "consultations" / "episodes" / (episode["id"] + ".md")).read_text()
        for name in ("张父", "张女", "张子", "张配偶"):
            self.assertIn(name, content)
        self.assertEqual(len(self.source_files()), 4)

    def test_merge_split_and_rebuild_preserve_all_source_bytes(self):
        self.add("father", False)
        self.add("daughter", False)
        before = self.source_files()
        ids = [episode["id"] for episode in self.profile()["consultation_episodes"]]
        self.tool("father", "add_consultation", "merge", "--episodes", *ids, "--basis", "已核实为同次症状的补充描述")
        self.assertEqual(len(self.profile()["consultation_episodes"]), 1)
        self.assertEqual(self.source_files(), before)
        source_id = self.profile()["consultations"][1]["id"]
        self.tool("daughter", "add_consultation", "split", "--report", source_id, "--basis", "进一步核对发现是第二次发作")
        self.assertEqual(len(self.profile()["consultation_episodes"]), 2)
        self.assertEqual(self.source_files(), before)
        episode = self.profile()["consultation_episodes"][0]
        path = self.health / "consultations" / "episodes" / (episode["id"] + ".md")
        expected = path.read_bytes()
        path.unlink()
        self.tool("father", "add_consultation", "rebuild")
        self.assertEqual(path.read_bytes(), expected)
        self.assertEqual(self.source_files(), before)

    def test_health_report_groups_event_and_keeps_family_source(self):
        self.add("father", True, chief="本人合成描述标记")
        self.add("daughter", True, chief="家属合成描述标记")
        output = self.base / "report.md"
        self.tool("daughter", "generate_health_report", "--output", output)
        content = output.read_text()
        self.assertIn("张父", content)
        self.assertIn("张女", content)
        self.assertIn("本人合成描述标记", content)
        self.assertIn("家属合成描述标记", content)
        self.assertIn("健康事件", content)
        self.assertNotIn("属于代问诊而未纳入", content)

    def test_erased_member_can_reinitialize_identity_and_consult_again(self):
        actor_before = (self.actor_health / "user-profile.json").read_bytes()
        self.add()
        registry_before = (self.home / "family.json").read_bytes()
        self.tool("father", "doctor", "erase", "--confirm", "DELETE-PERSONAL-HEALTH-DATA")
        self.assertFalse(self.health.exists())
        self.assertEqual((self.home / "family.json").read_bytes(), registry_before)
        self.tool("father", "doctor", "init")
        data = self.profile()
        self.assertEqual(data["owner"]["member_id"], self.father["id"])
        self.assertEqual(data["owner"]["name"], "张父")
        self.assertEqual(data["consultations"], [])
        self.add("daughter", True, chief="重新初始化后的合成问诊")
        self.assertEqual(len(self.profile()["consultations"]), 1)
        self.assert_attributed(self.profile()["consultations"][0])
        self.assertEqual((self.actor_health / "user-profile.json").read_bytes(), actor_before)

    def test_recent_symptoms_use_observation_time_not_backfill_time(self):
        self.tool("daughter", "add_symptom", "add", "--symptom", "历史合成症状不得进入近期",
                  "--severity", "2", "--observed-at", "2020-01-01T09:00:00+08:00")
        self.tool("father", "add_symptom", "add", "--symptom", "当前合成症状应进入近期", "--severity", "1")
        saved = json.loads((self.health / "symptoms.json").read_text())["symptoms"]
        self.assertEqual(len(saved), 2)
        self.assertNotEqual(saved[0]["recorded_at"][:10], saved[0]["observed_at"][:10])
        output = self.base / "recent-report.md"
        self.tool("father", "generate_health_report", "--output", output)
        content = output.read_text()
        self.assertNotIn("历史合成症状不得进入近期", content)
        self.assertIn("当前合成症状应进入近期", content)

    def test_explicit_event_file_missing_or_null_rejected_without_new_source(self):
        self.add()
        before = (self.health / "user-profile.json").read_bytes()
        sources = self.source_files()
        for event in (self.base / "not-created.json", self.write("null-event.json", None)):
            self.add("daughter", event, ok=False)
            self.assertEqual((self.health / "user-profile.json").read_bytes(), before)
            self.assertEqual(self.source_files(), sources)

    def test_source_and_candidate_links_resolve_for_default_and_custom_reports(self):
        # Exercise encoded spaces both in the shared data root and export path.
        old_home = self.home
        self.home = self.base / "家庭 资料"
        old_home.rename(self.home)
        self.health = self.home / "members" / self.father["id"] / "health-records"
        self.actor_health = self.home / "members" / self.daughter["id"] / "health-records"
        self.add()
        uncertain = json.loads(self.event.read_text())
        uncertain["precision"] = "day"
        self.add("daughter", self.write("coarse-event.json", uncertain))
        self.assertEqual(len(self.profile()["consultation_episodes"]), 2)
        self.assertTrue(any(episode.get("candidate_episode_ids") for episode in self.profile()["consultation_episodes"]))
        self.tool("father", "generate_health_report")
        custom = self.base / "custom exports" / "family report.md"
        self.tool("daughter", "generate_health_report", "--output", custom)
        for report in (self.health / "reports" / "latest.md", custom):
            links = re.findall(r"\[([^\]]+)\]\(([^)]+)\)", report.read_text())
            sources = [(label, target) for label, target in links if label == "原始问诊文件"]
            candidates = [(label, target) for label, target in links if label.startswith("相关事件")]
            self.assertEqual(len(sources), 2)
            self.assertTrue(candidates)
            for label, target in sources + candidates:
                parsed = urlparse(target)
                self.assertEqual(parsed.scheme, "", target)
                self.assertNotIn(" ", target)
                resolved = (report.parent / unquote(parsed.path)).resolve()
                self.assertTrue(resolved.is_file(), (label, target, str(resolved)))
            if report == custom:
                self.assertTrue(any("%20" in target for _, target in sources + candidates))

    def test_proxy_denial_remains_family_statement_in_report(self):
        self.tool("daughter", "update_profile", "status", "--field", "allergies", "--status", "denied",
                  "--source", "女儿表示父亲目前未发现过敏史")
        evidence = self.profile()["owner"]["factStatusEvidence"]["allergies"]
        self.assert_attributed(evidence)
        output = self.base / "denied-report.md"
        self.tool("father", "generate_health_report", "--output", output)
        content = output.read_text()
        self.assertIn("家属 张女", content)
        self.assertIn("未视为本人确认", content)
        self.assertNotIn("本人已明确否认", content)


if __name__ == "__main__":
    unittest.main()
