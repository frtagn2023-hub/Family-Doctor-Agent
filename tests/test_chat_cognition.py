"""Synthetic evidence, privacy, source isolation, longitudinal and storage checks."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from chat_cognition import ChatCognitionStore, DIMENSIONS, analysis_template


class ChatCognitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-chat-cognition-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "household"
        self.home.mkdir()
        self.father, self.child = "m_" + "a" * 32, "m_" + "b" * 32
        self.registry = {"schema_version": 1, "name": "合成观察家庭", "members": {
            self.father: {"id": self.father, "name": "合成父亲"}, self.child: {"id": self.child, "name": "合成女儿"}},
            "profiles": {"father": self.father, "daughter": self.child}}
        self.save_registry()
        self.clock = patch("chat_cognition._now", return_value=datetime(2026, 11, 13, 4, tzinfo=timezone.utc))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.store = ChatCognitionStore(self.home, "father")
        self.path = self.home / "cognition" / "chat-observations.json"

    def save_registry(self):
        (self.home / "family.json").write_text(json.dumps(self.registry, ensure_ascii=False))

    def payload(self, date="2026-11-12", suffix="", signal=True, domain="memory_repetition", mode="self", **updates):
        prefix = date + suffix
        messages = [
            {"id": prefix + "-u1", "role": "user", "at": date + "T08:00:00+08:00", "text": "合成秘密原话：下午几点去看医生？"},
            {"id": prefix + "-a1", "role": "assistant", "at": date + "T08:01:00+08:00", "text": "下午三点。"},
            {"id": prefix + "-u2", "role": "user", "at": date + "T08:02:00+08:00", "text": "合成秘密原话：下午几点去看医生？"},
        ]
        payload = {"schema_version": 1, "session_id": "session-" + prefix, "window_id": "window-" + prefix,
                   "member": "合成父亲", "source_mode": mode, "input_mode": "text", "conditions": [],
                   "messages": messages, "analysis": analysis_template()}
        if domain:
            outcome = "possible_signal" if signal else "no_signal"
            payload["analysis"]["dimensions"][domain] = {"status": outcome, "opportunities": [{
                "response_ids": [messages[2]["id"]], "context_ids": [messages[0]["id"], messages[1]["id"]],
                "outcome": outcome, "evidence": [{"message_id": messages[2]["id"], "quote": messages[2]["text"]}]}]}
        payload.update(updates)
        return payload

    def public(self, store=None, **kwargs):
        return next(m["chatAnalysis"] for m in (store or self.store).snapshot(**kwargs)["members"] if m["id"] == self.father)

    def dim(self, key="memory_repetition", store=None, **kwargs):
        return next(d for d in self.public(store, **kwargs)["dimensions"] if d["key"] == key)

    def acute(self, payload):
        user = payload["messages"][-1]
        user["text"] = "刚刚突然不知道自己在哪里，和平时不一样。"
        payload["analysis"] = analysis_template()
        payload["analysis"]["urgent"] = {"status": "possible_acute_change", "reason_codes": ["sudden_confusion"],
            "evidence": [{"message_id": user["id"], "quote": "不知道自己在哪里"}],
            "onset_evidence": [{"message_id": user["id"], "quote": "刚刚突然"}]}
        return payload

    def test_prepare_and_empty_snapshot_are_read_only_and_unassessed(self):
        payload = self.payload()
        del payload["analysis"]
        prepared = self.store.prepare(payload)
        self.assertEqual(prepared["quality"], "usable")
        self.assertEqual(set(prepared["analysisTemplate"]["dimensions"]), set(DIMENSIONS))
        public = self.public()
        self.assertEqual(public["status"], "insufficient")
        self.assertTrue(all(d["status"] == "not_assessable" for d in public["dimensions"]))
        self.assertFalse(self.path.parent.exists())

    def test_single_signal_is_information_and_unassessed_is_not_no_signal(self):
        self.store.record(self.payload())
        public = self.public()
        self.assertEqual(public["status"], "observing")
        self.assertEqual(public["alerts"][0]["level"], "info")
        self.assertEqual(public["alerts"][0]["sourceMode"], "self")
        self.assertEqual(self.dim()["status"], "possible_signal")
        self.assertEqual(self.dim("orientation")["status"], "not_assessable")
        self.store.record(self.payload("2026-11-11", signal=False, domain="comprehension"))
        self.assertEqual(self.dim("comprehension")["status"], "no_signal")

    def test_replay_is_byte_identical_and_same_window_changes_are_rejected(self):
        payload = self.payload()
        first = self.store.record(payload)
        original = self.path.read_bytes()
        duplicate = self.store.record(payload)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(first["revision"], duplicate["revision"])
        self.assertEqual(original, self.path.read_bytes())
        changed = copy.deepcopy(payload)
        changed["conditions"] = ["fatigue"]
        with self.assertRaises(ValueError):
            self.store.record(changed)
        self.assertEqual(original, self.path.read_bytes())

    def test_overlapping_windows_and_changed_ids_cannot_inflate_same_expression(self):
        payload = self.payload()
        self.store.record(payload)
        overlap = copy.deepcopy(payload)
        overlap["window_id"] = "overlap-window"
        self.assertTrue(self.store.record(overlap)["duplicate"])
        renamed = self.payload(suffix="renamed")
        self.assertTrue(self.store.record(renamed)["duplicate"])
        self.assertEqual(self.public()["self"]["opportunities"], 1)
        # A genuinely later repeated expression remains an independent event.
        later = self.payload(suffix="later")
        for message in later["messages"]:
            message["at"] = message["at"].replace("T08", "T09")
        self.store.record(later)
        self.assertEqual(self.dim()["opportunities"], 2)
        self.assertEqual(self.dim()["signalDays"], 1)
        self.assertEqual(self.public()["status"], "observing")

    def test_stable_id_content_role_or_time_changes_fail_atomically(self):
        self.store.record(self.payload())
        original = self.path.read_bytes()
        changed = self.payload()
        changed["window_id"] = "changed-window"
        changed["messages"][0]["text"] = "SECRET_CHANGED_MESSAGE"
        with self.assertRaises(ValueError) as caught:
            self.store.record(changed)
        self.assertNotIn("SECRET_CHANGED_MESSAGE", str(caught.exception))
        self.assertEqual(original, self.path.read_bytes())

    def test_conflicting_outcome_across_windows_is_rejected_not_silently_deduplicated(self):
        self.store.record(self.payload(signal=False))
        before = self.path.read_bytes()
        conflict = self.payload(window_id="changed-interpretation")
        with self.assertRaises(ValueError) as caught:
            self.store.record(conflict)
        self.assertIn("同一维度已有不同结论", str(caught.exception))
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(self.dim()["status"], "no_signal")
        self.assertEqual(self.dim()["opportunities"], 1)
        # Renaming every incoming message cannot evade the same-event conflict.
        with self.assertRaises(ValueError):
            self.store.record(self.payload(suffix="renamed-conflict"))
        self.assertEqual(before, self.path.read_bytes())

    def test_duplicate_new_alias_is_remembered_without_new_observation_and_cannot_be_reused(self):
        first = self.store.record(self.payload())
        renamed = self.payload(suffix="alternate-source-id")
        duplicate = self.store.record(renamed)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["revision"], first["revision"])
        stored = json.loads(self.path.read_text())
        self.assertEqual(len(stored["records"]), 1)
        self.assertEqual(len(stored["messages"]), 3)
        self.assertEqual(len(stored["aliases"]), 6)
        self.assertEqual(self.dim()["opportunities"], 1)
        after_alias = self.path.read_bytes()
        self.assertTrue(self.store.record(renamed)["duplicate"])
        self.assertEqual(after_alias, self.path.read_bytes())
        # The previously unseen id is now bound even though its first request
        # was only a duplicate. Changing its literal text must not add a sample.
        renamed["window_id"] = "reused-alternate-id"
        renamed["messages"][-1]["text"] = "另一段秘密原话：今天星期几？"
        renamed["analysis"]["dimensions"]["memory_repetition"]["opportunities"][0]["evidence"][0]["quote"] = renamed["messages"][-1]["text"]
        with self.assertRaises(ValueError) as caught:
            self.store.record(renamed)
        self.assertIn("同一消息标识", str(caught.exception))
        self.assertNotIn("另一段秘密原话", str(caught.exception))
        self.assertEqual(after_alias, self.path.read_bytes())
        self.assertEqual(self.dim()["opportunities"], 1)

    def test_three_independent_days_required_and_sources_do_not_combine(self):
        daughter = ChatCognitionStore(self.home, "daughter")
        self.store.record(self.payload("2026-11-10"))
        self.store.record(self.payload("2026-11-11"))
        daughter.record(self.payload("2026-11-12", mode="caregiver"))
        public = self.public()
        self.assertEqual(public["status"], "observing")
        self.assertEqual(public["self"]["signalDays"], 2)
        self.assertEqual(public["caregiver"]["signalDays"], 1)
        self.store.record(self.payload("2026-11-12"))
        self.assertEqual(self.public()["status"], "review")
        self.assertEqual(next(a for a in self.public()["alerts"] if a["level"] == "review")["code"], "repeated_self_signal")

    def test_caregiver_language_never_becomes_subject_language_or_reference(self):
        daughter = ChatCognitionStore(self.home, "daughter")
        for date in ("2026-11-10", "2026-11-11", "2026-11-12"):
            daughter.record(self.payload(date, mode="caregiver"))
        public = self.public()
        self.assertEqual(public["self"]["opportunities"], 0)
        self.assertEqual(public["caregiver"]["opportunities"], 3)
        self.assertEqual(self.dim()["status"], "not_assessable")
        self.assertFalse(self.dim()["reference"]["available"])
        self.assertEqual(public["alerts"][0]["sourceMode"], "caregiver")
        with self.assertRaises(ValueError):
            daughter.record(self.payload())
        with self.assertRaises(ValueError):
            self.store.record(self.payload(mode="caregiver"))

    def test_quality_conditions_cannot_be_overridden_and_limited_does_not_reserve_evidence(self):
        limited = self.payload(conditions=["assistant_misunderstanding"])
        result = self.store.record(limited)
        self.assertEqual(result["quality"], "limited")
        self.assertEqual(result["newOpportunities"], 0)
        self.assertEqual(self.public()["status"], "insufficient")
        complete = self.payload(window_id="complete-window")
        self.assertEqual(self.store.record(complete)["newOpportunities"], 1)
        self.assertEqual(self.public()["self"]["opportunities"], 1)
        excluded = self.payload("2026-11-11", conditions=["quoted_or_forwarded"])
        self.assertEqual(self.store.record(excluded)["quality"], "excluded")
        self.assertEqual(self.public()["self"]["opportunities"], 1)
        payload = self.payload(quality="usable")
        with self.assertRaises(ValueError):
            self.store.record(payload)

    def test_missing_context_forces_limited_and_acute_bypasses_ordinary_sample_gate(self):
        payload = self.acute(self.payload())
        payload["messages"] = payload["messages"][-1:]
        result = self.store.record(payload)
        self.assertEqual(result["quality"], "limited")
        self.assertEqual(result["newUrgent"], 1)
        self.assertEqual(self.public()["status"], "urgent")
        alert = self.public()["alerts"][0]
        self.assertEqual(alert["code"], "sudden_confusion")
        self.assertIsNone(alert["sourceMode"])
        self.assertEqual(self.public()["self"]["opportunities"], 0)

    def test_acute_evidence_can_be_added_after_an_overlapping_ordinary_window(self):
        payload = self.acute(self.payload())
        ordinary = copy.deepcopy(payload)
        ordinary["analysis"] = analysis_template()
        self.store.record(ordinary)
        payload["window_id"] = "acute-followup-window"
        result = self.store.record(payload)
        self.assertEqual(result["newOpportunities"], 0)
        self.assertEqual(result["newUrgent"], 1)
        self.assertEqual(self.public()["status"], "urgent")
        replay = copy.deepcopy(payload)
        replay["window_id"] = "acute-replay-window"
        self.assertTrue(self.store.record(replay)["duplicate"])
        self.assertEqual(self.public()["alerts"][0]["days"], 1)

    def test_quoted_acute_is_excluded_and_onset_cannot_be_invented(self):
        quoted = self.acute(self.payload(conditions=["quoted_or_forwarded"]))
        self.assertEqual(self.store.record(quoted)["newUrgent"], 0)
        self.assertEqual(self.public()["status"], "insufficient")
        no_onset = self.acute(self.payload("2026-11-11"))
        no_onset["analysis"]["urgent"]["onset_evidence"][0]["quote"] = "自己在哪里"
        with self.assertRaises(ValueError):
            self.store.record(no_onset)

    def test_evidence_must_be_literal_and_user_owned_with_real_opportunities(self):
        variants = []
        payload = self.payload()
        op = payload["analysis"]["dimensions"]["memory_repetition"]["opportunities"][0]
        op["evidence"][0]["quote"] = "HALLUCINATED_SECRET"
        variants.append(payload)
        payload = self.payload()
        op = payload["analysis"]["dimensions"]["memory_repetition"]["opportunities"][0]
        op["evidence"] = [{"message_id": payload["messages"][1]["id"], "quote": "下午三点。"}]
        variants.append(payload)
        payload = self.payload()
        payload["analysis"]["dimensions"]["memory_repetition"]["opportunities"][0]["context_ids"] = []
        variants.append(payload)
        payload = self.payload()
        payload["analysis"]["dimensions"]["orientation"]["status"] = "no_signal"
        variants.append(payload)
        payload = self.payload()
        payload["analysis"]["dimensions"]["memory_repetition"]["status"] = "not_assessable"
        variants.append(payload)
        for variant in variants:
            with self.subTest(variant=variants.index(variant)), self.assertRaises(ValueError) as caught:
                self.store.record(variant)
            self.assertNotIn("HALLUCINATED_SECRET", str(caught.exception))
        self.assertFalse(self.path.exists())

    def test_strict_input_rejects_unknown_fields_future_naive_time_and_bad_source(self):
        variants = []
        for field, value in (("schema_version", True), ("member", "UNREGISTERED_SECRET"), ("conditions", ["normal"]),
                             ("source_mode", "clinician"), ("risk_probability", .9), ("messages", [])):
            variants.append(self.payload(**{field: value}))
        for stamp in ("2026-11-14T08:00:00+08:00", "2026-11-12T08:00:00", True, "NaN"):
            payload = self.payload()
            payload["messages"][-1]["at"] = stamp
            variants.append(payload)
        for payload in variants:
            with self.assertRaises(ValueError) as caught:
                self.store.record(payload)
            self.assertNotIn("UNREGISTERED_SECRET", str(caught.exception))
        self.assertFalse(self.path.exists())

    def test_identical_messages_with_aliases_in_one_window_cannot_multiply_evidence(self):
        payload = self.payload()
        clone = dict(payload["messages"][-1], id="duplicate-alias")
        payload["messages"].append(clone)
        with self.assertRaises(ValueError):
            self.store.record(payload)

    def test_equal_timestamp_does_not_allow_later_message_as_prior_context(self):
        payload = self.payload()
        payload["messages"][0]["text"] = "合成问题：预约是在什么时候？"
        for message in payload["messages"]:
            message["at"] = "2026-11-12T08:00:00+08:00"
        invalid = copy.deepcopy(payload)
        opportunity = invalid["analysis"]["dimensions"]["memory_repetition"]["opportunities"][0]
        opportunity["response_ids"] = [invalid["messages"][0]["id"]]
        opportunity["context_ids"] = [invalid["messages"][1]["id"]]
        opportunity["evidence"] = [{"message_id": invalid["messages"][0]["id"], "quote": invalid["messages"][0]["text"]}]
        with self.assertRaises(ValueError) as caught:
            self.store.record(invalid)
        self.assertIn("事后消息", str(caught.exception))
        self.assertFalse(self.path.exists())
        # Equal-resolution timestamps remain valid when the actual order agrees.
        self.assertEqual(self.store.record(payload)["newOpportunities"], 1)

    def test_no_raw_text_quotes_or_source_ids_in_files_or_web_snapshot(self):
        payload = self.payload()
        self.store.record(payload)
        private = self.path.read_text()
        public = json.dumps(self.store.snapshot(), ensure_ascii=False)
        for secret in ("合成秘密原话", payload["session_id"], payload["window_id"], payload["messages"][0]["id"], "quote", "response_ids"):
            self.assertNotIn(secret, private)
            self.assertNotIn(secret, public)
        for field in ("reporter_profile", "fingerprint", "message_key", "father", "session_key"):
            self.assertNotIn(field, public)

    def test_personal_reference_requires_six_old_days_and_three_current_comparable_days(self):
        for day in range(1, 7):
            self.store.record(self.payload("2026-09-{:02d}".format(day), signal=False))
        for day in (10, 11):
            self.store.record(self.payload("2026-11-{:02d}".format(day)))
        self.assertFalse(self.dim()["reference"]["available"])
        self.store.record(self.payload("2026-11-12"))
        reference = self.dim()["reference"]
        self.assertTrue(reference["available"])
        self.assertEqual(reference["days"], 6)
        self.assertEqual(reference["comparison"], "more_signals")
        self.store.record(self.payload("2026-11-13", input_mode="voice"))
        self.assertFalse(self.dim()["reference"]["available"])

    def test_message_date_not_later_window_date_controls_day_and_window(self):
        payload = self.payload("2026-10-14")  # Outside the 30 Shanghai calendar days.
        payload["messages"].append({"id": "later-context", "role": "user", "at": "2026-11-12T08:03:00+08:00", "text": "好的。"})
        self.store.record(payload)
        self.assertEqual(self.public()["self"]["opportunities"], 0)
        self.assertEqual(self.public()["status"], "insufficient")
        self.assertEqual(self.public(days=31)["self"]["opportunities"], 1)

    def test_registry_is_reloaded_and_changes_during_record_fail_without_writing(self):
        self.registry["members"][self.father]["name"] = "新的合成姓名"
        self.save_registry()
        self.assertEqual(self.store.snapshot()["members"][0]["name"], "新的合成姓名")
        payload = self.payload(member=self.father)
        changed = copy.deepcopy(self.registry)
        changed["members"][self.father]["name"] = "再次改名"
        with patch.object(self.store, "_registry", side_effect=[self.registry, changed]), self.assertRaises(ValueError):
            self.store.record(payload)
        self.assertFalse(self.path.exists())

    def test_corrupt_or_tampered_store_is_never_reset_or_overwritten(self):
        self.store.record(self.acute(self.payload()))
        original = json.loads(self.path.read_text())
        corruptions = []
        value = copy.deepcopy(original)
        value["records"][0]["created_at"] = "2099-01-01T00:00:00+00:00"
        corruptions.append(value)
        value = copy.deepcopy(original)
        value["records"][0]["urgent"][0]["at"] = "2026-11-11T00:00:00+00:00"
        corruptions.append(value)
        value = copy.deepcopy(original)
        assistant = next(key for key, msg in value["messages"].items() if msg["role"] == "assistant")
        value["records"][0]["urgent"][0]["message_keys"] = [assistant]
        corruptions.append(value)
        value = copy.deepcopy(original)
        value["records"][0]["id"] = 3
        corruptions.append(value)
        value = copy.deepcopy(original)
        next(iter(value["messages"].values()))["fingerprint"] = "0" * 64
        corruptions.append(value)
        for value in corruptions:
            self.path.write_text(json.dumps(value))
            before = self.path.read_bytes()
            with self.assertRaises(ValueError):
                self.store.snapshot()
            with self.assertRaises(ValueError):
                self.store.record(self.payload("2026-11-11"))
            self.assertEqual(before, self.path.read_bytes())
        self.path.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaises(ValueError):
            self.store.snapshot()

    def test_atomic_concurrency_backups_permissions_and_symlink_rejection(self):
        payload = self.payload()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.store.record(payload), range(2)))
        self.assertEqual(sum(not result["duplicate"] for result in results), 1)
        first = self.path.read_bytes()
        self.store.record(self.payload("2026-11-11"))
        backups = list((self.path.parent / ".backups").glob("*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), first)
        if os.name != "nt":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        target = self.path.with_name("protected.json")
        self.path.rename(target)
        self.path.symlink_to(target)
        with self.assertRaises(ValueError):
            self.store.snapshot()


if __name__ == "__main__":
    unittest.main()
