"""Regression coverage with synthetic records in an isolated temporary workspace."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "family-doctor" / "scripts"


class ReportsTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.health = self.home / "health-records"
        self.env = dict(os.environ, FAMILY_DOCTOR_HOME=str(self.home), PYTHONDONTWRITEBYTECODE="1")
        self.env.pop("FAMILY_DOCTOR_TIMEZONE", None)

    def write_json(self, name, value):
        self.health.mkdir(exist_ok=True)
        (self.health / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def read_json(self, name):
        return json.loads((self.health / name).read_text(encoding="utf-8"))

    def run_script(self, script, *args, ok=True):
        result = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)], env=self.env, capture_output=True, text=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def run_code(self, code):
        result = subprocess.run([sys.executable, "-c", code], cwd=SCRIPTS, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def profile(self):
        return {"owner": {"name": "测试本人", "age": "40", "allergies": [], "chronicConditions": []}, "consultations": []}

    def fake_renderer(self, fail=False):
        directory = self.home / "renderer"
        directory.mkdir()
        code = '''
class CSS:
    def __init__(self, string, url_fetcher):
        self.string = string
        for url in ('file:///private/secret', 'https://example.invalid/pixel'):
            try:
                url_fetcher(url)
            except ValueError:
                pass
            else:
                raise AssertionError('resource fetch was not blocked')
class HTML:
    def __init__(self, string, url_fetcher):
        assert '<img ' not in string
        self.string = string
        try:
            url_fetcher('data:text/plain,unsafe')
        except ValueError:
            pass
        else:
            raise AssertionError('resource fetch was not blocked')
    def write_pdf(self, target, stylesheets):
        from pathlib import Path
        Path(target).write_bytes(b'%PDF-1.7\\nfixture renderer\\n')
'''
        if fail:
            code = "raise ImportError('renderer deliberately unavailable')\n"
        (directory / "weasyprint.py").write_text(code, encoding="utf-8")
        self.env["PYTHONPATH"] = str(directory)

    def test_blank_markdown_is_not_an_initialized_identity(self):
        (self.home / "USER.md").write_text("- **Name:**\n- **What to call them:**\n", encoding="utf-8")
        self.run_code("import welcome; assert welcome.existing_name() == ''")
        self.run_script("welcome.py", "--skip")
        result = self.read_json("user-profile.json")
        self.assertEqual(result["onboarding"]["status"], "complete")
        self.assertFalse(result["owner"].get("name"))

    def test_onboarding_preserves_all_existing_medical_facts(self):
        profile = self.profile()
        profile["owner"].update({"birthDate": "1980-02-03", "allergies": ["测试过敏原"], "medications": ["测试药"], "chronicConditions": ["既往记录"], "emergencyContact": {"name": "测试联系人", "phone": "000"}})
        self.write_json("user-profile.json", profile)
        self.run_script("welcome.py", "--name", "其他名字", "--age", "25")
        first = self.read_json("user-profile.json")
        for key, value in profile["owner"].items():
            self.assertEqual(first["owner"][key], value)
        self.run_script("welcome.py", "--skip")
        self.assertEqual(first, self.read_json("user-profile.json"))

    def test_template_and_welcome_share_one_structured_status(self):
        template = json.loads((SCRIPTS.parents[2] / "templates" / "user-profile.json").read_text(encoding="utf-8"))
        self.assertEqual(template["onboarding"]["status"], "not_started")
        self.assertNotIn("onboarding_status", template)
        self.write_json("user-profile.json", template)
        self.run_script("welcome.py", "--skip")
        initialized = self.read_json("user-profile.json")
        self.assertEqual(initialized["onboarding"]["status"], "complete")
        self.assertEqual(initialized["owner"]["factStatus"]["allergies"], "not_asked")
        self.assertFalse((self.home / "USER.md").exists())

    def test_legacy_onboarding_and_denial_states_normalize_without_losing_facts(self):
        profile = self.profile()
        profile["onboarding_status"] = "not_started"
        profile["owner"]["factStatus"] = {"allergies": "confirmed_none", "chronicConditions": "explicitly_denied"}
        self.write_json("user-profile.json", profile)
        self.run_script("welcome.py", "--skip")
        saved = self.read_json("user-profile.json")
        self.assertNotIn("onboarding_status", saved)
        self.assertEqual(saved["onboarding"]["status"], "complete")
        self.assertEqual(saved["owner"]["factStatus"]["allergies"], "denied")
        self.assertEqual(saved["owner"]["factStatus"]["chronicConditions"], "denied")
        self.assertEqual(saved["owner"]["name"], "测试本人")

    def test_report_distinguishes_fact_states_and_accepts_legacy_denials(self):
        self.run_code('''
import generate_health_report as r
for state in ('denied', 'confirmed_none', 'explicitly_denied'):
    text = r.format_history({'allergies': [], 'factStatus': {'allergies': state}}, 'allergies')
    assert '本人已明确否认' in text
assert '未询问' in r.format_history({'factStatus': {'allergies': 'not_asked'}}, 'allergies')
assert '未知' in r.format_history({'factStatus': {'allergies': 'unknown'}}, 'allergies')
assert '详情未记录' in r.format_history({'factStatus': {'allergies': 'present'}}, 'allergies')
assert '冲突' in r.format_history({'allergies': ['synthetic'], 'factStatus': {'allergies': 'denied'}}, 'allergies')
fact = {'value': 'synthetic', 'source': {'type': 'user_report', 'reference': 'synthetic source'}, 'confirmation_status': 'confirmed', 'recorded_at': '2026-01-02', 'confirmed_at': '2026-01-03'}
text = r.fact_text(fact)
for expected in ['synthetic source', '确认状态：已确认', '记录时间：2026-01-02', '确认时间：2026-01-03']:
    assert expected in text
''')

    @unittest.skipIf(sys.version_info < (3, 9), "named timezone option requires zoneinfo")
    def test_report_and_emergency_presentation_translate_codes_and_preserve_json(self):
        self.env["FAMILY_DOCTOR_TIMEZONE"] = "Asia/Shanghai"
        self.run_code('''
import copy
import generate_health_report as r
import generate_emergency_card as e
stamp = '2026-01-02T16:30:01.123456Z'
assert r.display_time(stamp) == '2026-01-03 00:30 UTC+08:00'
assert r.display_time('2026-01-02') == '2026-01-02'
record = {'type': 'heart_rate', 'value': 72, 'unit': 'bpm', 'measured_at': stamp,
          'source': {'type': 'user_report', 'reference': '合成记录'}, 'confirmation_status': 'unconfirmed'}
before = copy.deepcopy(record)
text = r.format_vitals([record])
for expected in ['心率', '本人自述', '确认状态：未确认', '2026-01-03 00:30 UTC+08:00', '合成记录']:
    assert expected in text
for hidden in ['heart_rate', 'user_report', 'unconfirmed', '.123456']:
    assert hidden not in text
assert record == before
assert r.code_label('UNKNOWN', r.RISK_LABELS, '待评估') == '待评估'
assert '未识别（custom_risk）' == r.code_label('custom_risk', r.RISK_LABELS, '待评估')
assert '未识别（custom_status）' == r.confirmation_text('custom_status')
assert '未识别（custom_source）' == r.source_text({'type': 'custom_source'})
med = {'name': '合成药', 'dose': '1mg', 'frequency': '每日一次', 'source': {'type': 'document'}, 'confirmation_status': 'confirmed'}
card = {'generated_at': stamp, 'name': '合成', 'dob': '2000-01-02', 'blood_type': '未知', 'allergies': '未知', 'conditions': '未知', 'medications': [med], 'legacy_medications_unverified': None, 'emergency_contact': {}, 'note': '合成卡片'}
before = copy.deepcopy(card)
text = e.generate_phone_format(card)
for expected in ['2026-01-03 00:30 UTC+08:00', '提供的文档', '确认状态：已确认']:
    assert expected in text
assert card == before
''')

    def test_all_entrypoints_reject_proxy_before_opening_corrupt_profile(self):
        self.health.mkdir()
        path = self.health / "user-profile.json"
        path.write_text("not json", encoding="utf-8")
        cases = [("welcome.py", ["--skip"]), ("add_consultation.py", ["add", "--chief-complaint", "测试"]),
                 ("add_consultation.py", ["list"]), ("generate_health_report.py", []), ("generate_emergency_card.py", [])]
        before = sorted(str(p.relative_to(self.home)) for p in self.home.rglob("*"))
        for script, args in cases:
            result = self.run_script(script, *args, "--role", "proxy", ok=False)
            self.assertIn("代问诊", result.stderr)
            self.assertNotIn("JSON", result.stderr)
        self.assertEqual(before, sorted(str(p.relative_to(self.home)) for p in self.home.rglob("*")))
        self.assertEqual(path.read_text(encoding="utf-8"), "not json")

    def test_consultation_missing_values_stay_unknown_and_uuid_prevents_overwrite(self):
        self.write_json("user-profile.json", self.profile())
        for _ in range(2):
            self.run_script("add_consultation.py", "add", "--chief-complaint", "测试主诉")
        profile = self.read_json("user-profile.json")
        self.assertEqual(len(profile["consultations"]), 2)
        self.assertEqual(len({c["id"] for c in profile["consultations"]}), 2)
        for record in profile["consultations"]:
            self.assertEqual(record["riskLevel"], "UNKNOWN")
            self.assertIsNone(record["symptoms"]["associated"])
            self.assertIsNone(record["medicalAdvice"])
            text = (self.health / record["file"]).read_text(encoding="utf-8")
            self.assertIn("待评估", text)
            self.assertNotIn("可暂观察", text)
            self.assertNotIn("无特殊", text)

    def test_consultation_rejects_mismatched_person_and_unattributed_clinician_diagnosis(self):
        self.write_json("user-profile.json", self.profile())
        self.run_script("add_consultation.py", "add", "--chief-complaint", "测试", "--patient", "其他人", ok=False)
        self.run_script("add_consultation.py", "add", "--chief-complaint", "测试", "--clinician-diagnosis", "测试结论", ok=False)
        self.assertEqual(self.read_json("user-profile.json")["consultations"], [])

    def test_consultation_index_failure_rolls_back_unpublished_artifact(self):
        self.write_json("user-profile.json", self.profile())
        self.run_code('''
import add_consultation as c
from health_store import load_json, USER_PROFILE, CONSULTATIONS_DIR
def broken(path, updater, default=None):
    updater(load_json(path, default))
    raise OSError('simulated write failure')
c.update_json = broken
assert c.main(['add', '--chief-complaint', 'synthetic']) == 1
assert not list(CONSULTATIONS_DIR.glob('*.md'))
assert load_json(USER_PROFILE)['consultations'] == []
''')

    def test_report_excludes_proxy_unknown_and_mismatched_consultations(self):
        profile = self.profile()
        today = datetime.now().astimezone().date().isoformat()
        profile["consultations"] = [
            {"date": today, "patient": "测试本人", "role": "self", "chiefComplaint": "SELF_INCLUDED", "aiHypothesis": "AI_ONLY"},
            {"date": today, "patient": "测试本人", "role": "proxy", "chiefComplaint": "PROXY_EXCLUDED"},
            {"date": today, "patient": "测试本人", "chiefComplaint": "LEGACY_UNRESOLVED"},
            {"date": today, "patient": "其他人", "role": "self", "chiefComplaint": "MISMATCH_EXCLUDED"},
        ]
        self.write_json("user-profile.json", profile)
        self.run_script("generate_health_report.py")
        report = (self.health / "reports" / "latest.md").read_text(encoding="utf-8")
        self.assertIn("SELF_INCLUDED", report)
        self.assertIn("AI 提出的可能原因（待核实，不是医生诊断）：AI_ONLY", report)
        for forbidden in ("PROXY_EXCLUDED", "LEGACY_UNRESOLVED", "MISMATCH_EXCLUDED"):
            self.assertNotIn(forbidden, report)
        self.assertIn("过敏史：未知", report)

    def test_report_includes_confirmed_document_fields_with_provenance_only(self):
        profile = self.profile()
        field = {"name": "测试指标", "value": 1.2, "unit": "mg/L", "source_page": 2,
                 "reference_range": "0–2 mg/L", "observed_at": "2026-01-02", "confirmed_at": "2026-01-03", "confirmation_status": "confirmed"}
        profile["records"] = {"检验": [{"status": "confirmed", "role": "self", "original_name": "合成报告.pdf", "fields": [field, dict(field, name="PENDING_FIELD", confirmation_status="pending")]},
                                     {"status": "confirmed", "role": "proxy", "original_name": "PROXY_DOCUMENT", "fields": [field]},
                                     {"status": "pending", "role": "self", "original_name": "PENDING_FILE", "fields": [field]}]}
        self.write_json("user-profile.json", profile)
        self.run_script("generate_health_report.py")
        report = (self.health / "reports" / "latest.md").read_text(encoding="utf-8")
        self.assertIn("1.2 mg/L", report)
        self.assertIn("参考范围：0–2 mg/L", report)
        self.assertIn("合成报告.pdf 第 2 页", report)
        self.assertIn("2026-01-02", report)
        self.assertIn("2026-01-03", report)
        self.assertNotIn("PENDING_FIELD", report)
        self.assertNotIn("PENDING_FILE", report)
        self.assertNotIn("PROXY_DOCUMENT", report)

    def test_legacy_meds_and_vitals_preserve_units_and_stopped_status(self):
        self.write_json("user-profile.json", self.profile())
        today = datetime.now().astimezone().date().isoformat()
        self.write_json("medications.json", {"medications": [{"name": "ACTIVE_TEST", "dosage": "1mg", "ongoing": True}, {"name": "STOPPED_TEST", "dosage": "2mg", "ongoing": False}]})
        self.write_json("vitals.json", {"vitals": {"glucose": [{"timestamp": today, "value": 100, "unit": "mg/dL"}, {"timestamp": today, "value": 5.5}]}})
        self.run_script("generate_health_report.py")
        report = (self.health / "reports" / "latest.md").read_text(encoding="utf-8")
        self.assertIn("ACTIVE_TEST 1mg", report)
        self.assertNotIn("STOPPED_TEST", report)
        self.assertIn("100 mg/dL", report)
        self.assertIn("5.5 单位未知", report)

    def test_date_only_records_on_first_calendar_day_are_included(self):
        self.write_json("user-profile.json", self.profile())
        self.write_json("vitals.json", {"vitals": [{"type": "weight", "value": 70, "unit": "kg", "recorded_at": "2026-01-02"}, {"type": "weight", "value": 71, "unit": "kg", "recorded_at": "2026-01-01"}]})
        self.run_code("from datetime import date; import generate_health_report as r; data=r.get_recent_data(2,today=date(2026,1,3)); assert len(data['vitals'])==1; assert data['vitals'][0]['value']==70")

    @unittest.skipIf(sys.version_info < (3, 9), "named timezone option requires zoneinfo")
    def test_shanghai_calendar_day_includes_cross_utc_midnight_measurements(self):
        self.env["FAMILY_DOCTOR_TIMEZONE"] = "Asia/Shanghai"
        self.write_json("vitals.json", {"vitals": [
            {"type": "weight", "value": 70, "unit": "kg", "measured_at": "2026-01-02T16:30:00Z"},
            {"type": "weight", "value": 71, "unit": "kg", "measured_at": "2026-01-03T00:45:00+08:00"},
            {"type": "weight", "value": 72, "unit": "kg", "measured_at": "2026-01-03T01:00:00"},
            {"type": "weight", "value": 73, "unit": "kg", "measured_at": "2026-01-03T17:00:00Z"},
        ]})
        self.run_code("from datetime import date; import generate_health_report as r; data=r.get_recent_data(1,today=date(2026,1,3)); assert [v['value'] for v in data['vitals']]==[70,71,72]; assert str(r.report_timezone())=='Asia/Shanghai'")
        self.run_script("generate_health_report.py")
        self.assertIn("时区：Asia/Shanghai", (self.health / "reports" / "latest.md").read_text(encoding="utf-8"))

    def test_invalid_report_timezone_fails_without_publishing_report(self):
        self.env["FAMILY_DOCTOR_TIMEZONE"] = "invalid/zone"
        result = self.run_script("generate_health_report.py", ok=False)
        self.assertIn("FAMILY_DOCTOR_TIMEZONE", result.stderr)
        self.assertFalse((self.health / "reports" / "latest.md").exists())

    def test_emergency_card_uses_owner_and_does_not_truncate(self):
        profile = self.profile()
        profile["owner"]["allergies"] = ["合成过敏原" + str(i) for i in range(8)]
        profile["owner"]["chronicConditions"] = ["合成病史" + str(i) for i in range(8)]
        self.write_json("user-profile.json", profile)
        self.write_json("medications.json", {"medications": [{"name": "合成药物" + str(i), "dose": "1mg", "status": "active"} for i in range(8)]})
        result = self.run_script("generate_emergency_card.py", "--format", "wallet")
        for expected in ("测试本人", "合成过敏原7", "合成病史7", "合成药物7"):
            self.assertIn(expected, result.stdout)

    def test_html_escapes_tags_and_denies_all_resource_schemes(self):
        self.run_code('''
import generate_health_report as r
text = r.markdown_to_html('# <img src="file:///secret">\\n- **<script>bad()</script>**\\n![x](https://example.invalid)')
assert '<img' not in text and '<script' not in text
assert '&lt;img' in text and '<strong>&lt;script&gt;' in text
for url in ['https://example.invalid', 'file:///secret', 'data:text/plain,x']:
    try: r.deny_resource(url)
    except ValueError: pass
    else: raise AssertionError('fetch allowed')
''')

    def test_default_pdf_option_exports_markdown_and_pdf(self):
        self.fake_renderer()
        self.run_script("generate_health_report.py", "--pdf", "--reason", '<img src="file:///private/secret">')
        self.assertTrue((self.health / "reports" / "latest.md").is_file())
        self.assertTrue((self.health / "reports" / "latest.pdf").read_bytes().startswith(b"%PDF-"))

    def test_pdf_only_and_pdf_output_never_write_markdown_into_pdf(self):
        self.fake_renderer()
        self.run_script("generate_health_report.py", "--pdf-only")
        self.assertFalse((self.health / "reports" / "latest.md").exists())
        self.assertTrue((self.health / "reports" / "latest.pdf").read_bytes().startswith(b"%PDF-"))
        path = self.home / "visit.final.pdf"
        self.run_script("generate_health_report.py", "--output", path)
        self.assertTrue(path.read_bytes().startswith(b"%PDF-"))
        self.assertFalse(path.with_suffix(".md").exists())

    def test_pdf_failure_is_nonzero_and_preserves_existing_pdf(self):
        self.fake_renderer(fail=True)
        path = self.home / "existing.pdf"
        path.write_bytes(b"%PDF-old")
        self.run_script("generate_health_report.py", "--pdf-only", "--output", path, ok=False)
        self.assertEqual(path.read_bytes(), b"%PDF-old")
        self.assertFalse(path.with_suffix(".md").exists())


if __name__ == "__main__":
    unittest.main()
