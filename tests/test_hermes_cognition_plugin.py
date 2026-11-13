"""Synthetic Hermes hook + core integration; never imports a real profile."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("synthetic_hermes_cognition_plugin",
    ROOT / "integrations/hermes/family-doctor-cognition/__init__.py")
PLUGIN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PLUGIN)


class HermesCognitionPluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-hermes-cognition-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.father, self.child = "m_" + "a" * 32, "m_" + "b" * 32
        self.registry = {"schema_version": 1, "name": "合成家庭", "members": {
            self.father: {"id": self.father, "name": "合成父亲"},
            self.child: {"id": self.child, "name": "合成女儿"}},
            "profiles": {"father": self.father, "daughter": self.child}}
        (self.home / "family.json").write_text(json.dumps(self.registry), encoding="utf-8")
        self.environment = patch.dict(os.environ, {
            "FAMILY_DOCTOR_CODE": str(ROOT), "FAMILY_DOCTOR_FAMILY_HOME": str(self.home),
            "HERMES_SESSION_PLATFORM": "feishu", "HERMES_SESSION_PROFILE": "father",
            "HERMES_SESSION_ID": "synthetic-session", "HERMES_SESSION_MESSAGE_ID": "message-1",
            "FAMILY_DOCTOR_PROFILE": "", "HERMES_CRON_SESSION": ""}, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        PLUGIN._WINDOWS.clear()
        self.addCleanup(PLUGIN._WINDOWS.clear)

    def before(self, text="合成原文：明天去公园。", **kwargs):
        return PLUGIN.before_turn(session_id=os.environ["HERMES_SESSION_ID"], platform="feishu", user_message=text, **kwargs)

    def after(self, text="合成回复：明天下午一起去。"):
        PLUGIN.after_turn(session_id=os.environ["HERMES_SESSION_ID"], platform="feishu", assistant_response=text)

    def prepare(self, **overrides):
        args = {"source_mode": "self", "input_mode": "text", "conditions": []}
        args.update(overrides)
        return json.loads(PLUGIN.prepare_observation(args))

    def test_registration_matches_installed_api_without_model_or_delivery(self):
        class Context:
            def __init__(self):
                self.hooks, self.tools = {}, {}
            def register_hook(self, name, callback):
                self.hooks[name] = callback
            def register_tool(self, **kwargs):
                self.tools[kwargs["name"]] = kwargs
        context = Context()
        PLUGIN.register(context)
        self.assertEqual(set(context.hooks), {"pre_llm_call", "post_llm_call"})
        self.assertEqual(set(context.tools), {"family_cognition_prepare", "family_cognition_record"})
        self.assertTrue(all(tool["toolset"] == "family_cognition" for tool in context.tools.values()))

    def test_hook_only_returns_instructions_not_raw_and_prepare_does_not_write(self):
        raw = "SYNTHETIC_PRIVATE_SENTINEL_你好"
        context = self.before(raw)
        self.assertNotIn(raw, context["context"])
        self.assertIn("不得发送飞书认知提醒", context["context"])
        self.assertIn("chat-cognition-rubric.md", context["context"])
        prepared = self.prepare()
        self.assertTrue(prepared["ok"])
        self.assertEqual(prepared["input"]["messages"][0]["text"], raw)
        self.assertEqual(prepared["prepared"]["quality"], "limited")
        self.assertEqual(prepared["time_basis"], "local_observed_at")
        self.assertEqual(set(path.name for path in self.home.iterdir()), {"family.json"})

    def test_current_model_result_is_validated_and_raw_never_persists(self):
        self.before("合成私密原文一：明天去公园。")
        self.prepare()
        self.after("合成助手：你想上午还是下午去？")
        os.environ["HERMES_SESSION_MESSAGE_ID"] = "message-2"
        self.before("合成私密原文二：下午去。")
        prepared = self.prepare()
        self.assertEqual(prepared["prepared"]["quality"], "usable")
        messages = prepared["input"]["messages"]
        analysis = prepared["prepared"]["analysisTemplate"]
        analysis["dimensions"]["comprehension"] = {"status": "no_signal", "opportunities": [{
            "response_ids": [messages[-1]["id"]], "context_ids": [messages[-2]["id"]],
            "outcome": "no_signal", "evidence": [{"message_id": messages[-1]["id"], "quote": "下午去。"}]}]}
        result = json.loads(PLUGIN.record_observation({"preparation_id": prepared["preparation_id"], "analysis": analysis}))
        self.assertEqual(result, {"ok": True, "saved": True})
        path = self.home / "cognition/chat-observations.json"
        self.assertTrue(path.exists())
        content = path.read_text()
        for secret in ("合成私密原文", "合成助手", "下午去。", '"quote":', '"text":'):
            self.assertNotIn(secret, content)
        original = path.read_bytes()
        result = json.loads(PLUGIN.record_observation({"preparation_id": prepared["preparation_id"], "analysis": analysis}))
        self.assertTrue(result["ok"])
        self.assertEqual(path.read_bytes(), original)

    def test_gateway_retry_preserves_time_and_window_before_its_response(self):
        self.before()
        first = self.prepare()["input"]
        self.after()
        self.before()
        second = self.prepare()["input"]
        self.assertEqual(first, second)
        self.assertEqual(len(second["messages"]), 1)

    def test_reused_message_id_with_changed_text_does_not_create_observation(self):
        self.before("合成原文 A")
        self.assertIsNone(self.before("合成原文 B"))
        self.assertFalse(self.prepare()["ok"])
        self.assertFalse((self.home / "cognition").exists())

    def test_contextvar_profile_wins_over_ambient_environment(self):
        values = {"HERMES_SESSION_PLATFORM": "feishu", "HERMES_SESSION_PROFILE": "daughter",
                  "HERMES_SESSION_ID": "synthetic-session", "HERMES_SESSION_MESSAGE_ID": "daughter-message"}
        with patch.object(PLUGIN, "_session_value", side_effect=lambda name: values.get(name, "")):
            self.before()
            prepared = self.prepare()
            self.assertEqual(prepared["input"]["member"], self.child)

    def test_unregistered_conflicting_missing_and_nonhuman_routes_do_nothing(self):
        for changes in ({"HERMES_SESSION_PROFILE": "unknown"}, {"FAMILY_DOCTOR_PROFILE": "daughter"},
                        {"HERMES_SESSION_MESSAGE_ID": ""}, {"HERMES_CRON_SESSION": "1"}):
            with self.subTest(changes=changes), patch.dict(os.environ, changes):
                self.assertIsNone(self.before())
        self.assertIsNone(PLUGIN.before_turn(session_id="x", platform="cli", user_message="合成代码任务"))
        self.assertIsNone(self.before(parent_session_id="synthetic-parent"))
        self.assertFalse(PLUGIN._WINDOWS)

    def test_preparation_cannot_be_committed_from_another_profile_or_later_turn(self):
        self.before()
        prepared = self.prepare()
        args = {"preparation_id": prepared["preparation_id"], "analysis": prepared["prepared"]["analysisTemplate"]}
        with patch.dict(os.environ, {"HERMES_SESSION_PROFILE": "daughter"}):
            self.assertFalse(json.loads(PLUGIN.record_observation(args))["ok"])
        os.environ["HERMES_SESSION_MESSAGE_ID"] = "message-2"
        self.before("合成新一轮")
        self.assertFalse(json.loads(PLUGIN.record_observation(args))["ok"])

    def test_record_adds_quality_restrictions_and_never_accepts_raw_or_paths(self):
        self.before()
        prepared = self.prepare(conditions=["fatigue"])
        analysis = prepared["prepared"]["analysisTemplate"]
        args = {"preparation_id": prepared["preparation_id"], "analysis": analysis,
                "additional_conditions": ["source_uncertain"]}
        result = json.loads(PLUGIN.record_observation(args))
        self.assertTrue(result["ok"])
        records = json.loads((self.home / "cognition/chat-observations.json").read_text())["records"]
        self.assertEqual(records[0]["quality"], "excluded")
        self.assertIn("fatigue", records[0]["conditions"])
        self.assertIn("source_uncertain", records[0]["conditions"])
        for extra in ({"text": "private"}, {"file": "/tmp/arbitrary"}, {"member": self.child}):
            self.assertFalse(json.loads(PLUGIN.record_observation(dict(args, **extra)))["ok"])

    def test_limits_and_expired_window_do_not_return_original_content(self):
        with patch.object(PLUGIN, "MAX_SESSIONS", 2), patch.object(PLUGIN, "MAX_MESSAGES", 3):
            for sid in ("one", "two", "three"):
                os.environ["HERMES_SESSION_ID"] = sid
                for n in range(4):
                    os.environ["HERMES_SESSION_MESSAGE_ID"] = str(n)
                    self.before("合成内容" + str(n))
            self.assertEqual(len(PLUGIN._WINDOWS), 2)
            self.assertTrue(all(len(state["messages"]) == 3 for state in PLUGIN._WINDOWS.values()))
            state = next(reversed(PLUGIN._WINDOWS.values()))
            state["seen"] -= PLUGIN.TTL_SECONDS + 1
            self.assertFalse(self.prepare()["ok"])
        self.assertIsNone(self.before("x" * (PLUGIN.MAX_MESSAGE_CHARS + 1)))

    def test_invalid_evidence_returns_only_generic_error_and_preserves_storage(self):
        self.before("合成测试")
        prepared = self.prepare()
        analysis = deepcopy(prepared["prepared"]["analysisTemplate"])
        analysis["private_report"] = "DO_NOT_ECHO_RAW_VALUE"
        result = PLUGIN.record_observation({"preparation_id": prepared["preparation_id"], "analysis": analysis})
        self.assertFalse(json.loads(result)["ok"])
        self.assertNotIn("DO_NOT_ECHO", result)
        self.assertFalse((self.home / "cognition/chat-observations.json").exists())

    def test_subject_switch_self_caregiver_self_records_without_mixing_windows(self):
        os.environ["HERMES_SESSION_PROFILE"] = "daughter"
        cases = [(self.child, "self"), (self.child, "self"),
                 (self.father, "caregiver"), (self.father, "caregiver"),
                 (self.child, "self"), (self.child, "self")]
        for index, (member, mode) in enumerate(cases):
            os.environ["HERMES_SESSION_MESSAGE_ID"] = "scope-" + str(index)
            self.before("合成来源切换消息" + str(index))
            prepared = self.prepare(member=member, source_mode=mode)
            self.assertTrue(prepared["ok"])
            self.assertEqual(prepared["input"]["member"], member)
            self.assertEqual(len(prepared["input"]["messages"]), 1 if index % 2 == 0 else 3)
            result = json.loads(PLUGIN.record_observation({"preparation_id": prepared["preparation_id"],
                "analysis": prepared["prepared"]["analysisTemplate"]}))
            self.assertTrue(result["ok"], result)
            self.after("合成回应" + str(index))
        same_message_other_member = self.prepare(member=self.father, source_mode="caregiver")
        self.assertEqual(same_message_other_member["code"], "message_scope_conflict")
        records = json.loads((self.home / "cognition/chat-observations.json").read_text())["records"]
        self.assertEqual([record["member_id"] for record in records], [member for member, _ in cases])

    def test_input_mode_switch_starts_new_segment_and_member_name_resolves_to_same_scope(self):
        self.before()
        self.prepare(member="合成父亲")
        self.after()
        os.environ["HERMES_SESSION_MESSAGE_ID"] = "typed-2"
        self.before("合成文字二")
        self.assertEqual(len(self.prepare(member=self.father)["input"]["messages"]), 3)
        self.after()
        os.environ["HERMES_SESSION_MESSAGE_ID"] = "voice-3"
        self.before("合成语音转写")
        prepared = self.prepare(input_mode="voice", conditions=["asr_uncertain"])
        self.assertEqual(len(prepared["input"]["messages"]), 1)
        self.assertIn("asr_uncertain", prepared["prepared"]["conditions"])
        self.assertEqual(self.prepare(input_mode="text")["code"], "message_scope_conflict")

    def test_quality_limits_follow_old_messages_but_missing_context_can_improve(self):
        self.before("合成疲劳表达")
        prepared = self.prepare(conditions=["fatigue"])
        self.assertIn("missing_context", prepared["prepared"]["conditions"])
        result = json.loads(PLUGIN.record_observation({"preparation_id": prepared["preparation_id"],
            "analysis": prepared["prepared"]["analysisTemplate"], "additional_conditions": ["asr_uncertain"]}))
        self.assertTrue(result["ok"])
        self.after()
        os.environ["HERMES_SESSION_MESSAGE_ID"] = "message-2"
        self.before("合成下一轮")
        prepared = self.prepare(conditions=[])
        self.assertEqual(prepared["input"]["conditions"], ["asr_uncertain", "fatigue"])
        self.assertNotIn("missing_context", prepared["prepared"]["conditions"])
        self.assertEqual(prepared["prepared"]["quality"], "limited")

    def test_unavailable_message_clears_context_instead_of_joining_across_a_gap(self):
        self.before()
        self.prepare()
        self.after()
        os.environ["HERMES_SESSION_MESSAGE_ID"] = "non-text-2"
        self.assertIsNone(PLUGIN.before_turn(session_id=os.environ["HERMES_SESSION_ID"], platform="feishu",
            user_message=[{"type": "image", "text": "synthetic"}]))
        self.assertFalse(PLUGIN._WINDOWS)
        os.environ["HERMES_SESSION_MESSAGE_ID"] = "text-3"
        self.before("合成后续消息")
        prepared = self.prepare()
        self.assertEqual(len(prepared["input"]["messages"]), 1)
        self.assertEqual(prepared["prepared"]["quality"], "limited")


if __name__ == "__main__":
    unittest.main()
