"""Synthetic event reconciliation cases: no clinical data or external services."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "family-doctor" / "scripts"))
from consultation_episodes import (add_report_to_episodes, merge_episodes, normalize_event,
                                   render_episode, split_report)


def event(start="2026-11-11T08:00:00+08:00", end="2026-11-11T10:00:00+08:00", **changes):
    result = {"symptoms": [{"key": "咳嗽", "site": "胸部", "polarity": "present"}],
              "symptoms_complete": True, "start": start, "end": end,
              "precision": "minute", "course": "ongoing", "time_text": "当天上午持续两小时"}
    result.update(changes)
    return result


def report(report_id, metadata=None, **changes):
    result = {"id": report_id, "subject_member_id": "member_parent", "patient": "合成人名",
              "reporter_member_id": "member_parent", "reporter_name": "合成提供者",
              "role": "self", "chiefComplaint": "合成咳嗽", "symptoms": {"severity": 2},
              "recordedAt": "2026-11-11T11:00:00+08:00", "event": event() if metadata is None else metadata,
              "riskLevel": "UNKNOWN", "file": "consultations/" + report_id + ".md"}
    result.update(changes)
    return result


class EpisodeTests(unittest.TestCase):
    def setUp(self):
        self.profile = {"consultations": []}

    def add(self, record, **kwargs):
        self.profile["consultations"].append(record)
        return add_report_to_episodes(self.profile, record, **kwargs)

    def test_same_event_self_and_proxy_preserve_both_sources(self):
        first = report("r1", riskLevel="RED", symptoms={"severity": 8}, history="本人所述")
        second = report("r2", role="family_proxy", reporter_member_id="member_child",
                        riskLevel="GREEN", history="家属所述")
        original = copy.deepcopy([first, second])
        episode = self.add(first)
        self.assertEqual(self.add(second)["id"], episode["id"])
        self.assertEqual(len(self.profile["consultation_episodes"]), 1)
        self.assertEqual(self.profile["consultations"], original)
        self.assertEqual(episode["report_ids"], ["r1", "r2"])
        self.assertEqual(episode["report_count"], 2)
        self.assertEqual({f["field"] for f in episode["conflicts"]}, {"symptoms.severity", "history"})
        rendered = render_episode(episode, self.profile["consultations"])
        for value in ("红色风险", "绿色风险", "本人所述", "家属所述", "家庭代问诊", "../r1.md", "../r2.md"):
            self.assertIn(value, rendered)
        self.assertEqual(self.profile["consultations"], original)

    def test_occurrence_time_not_reporting_date(self):
        episode = self.add(report("r1"))
        # Days later, the child reports the very same earlier occurrence.
        self.assertEqual(self.add(report("r2", recordedAt="2026-11-15T20:00:00+08:00"))["id"], episode["id"])
        later = report("r3", event("2026-11-11T18:00:00+08:00", "2026-11-11T20:00:00+08:00"))
        self.assertNotEqual(self.add(later)["id"], episode["id"])

    def test_equivalent_timezone_intervals_merge(self):
        episode = self.add(report("r1"))
        utc = event("2026-11-11T00:00:00Z", "2026-11-11T02:00:00+00:00")
        self.assertEqual(self.add(report("r2", utc))["id"], episode["id"])

    def test_same_name_different_member_never_merge(self):
        episode = self.add(report("r1"))
        other = self.add(report("r2", subject_member_id="member_other"))
        self.assertNotEqual(other["id"], episode["id"])
        self.assertEqual(other["candidate_episode_ids"], [])
        with self.assertRaises(ValueError):
            merge_episodes(self.profile, [episode["id"], other["id"]], "同名不是同一个人")

    def test_member_rename_does_not_break_match(self):
        episode = self.add(report("r1"))
        self.assertEqual(self.add(report("r2", patient="更新后的合成人名"))["id"], episode["id"])

    def test_unknown_identity_never_auto_merges(self):
        first = self.add(report("r1", subject_member_id=None))
        second = self.add(report("r2", subject_member_id=None))
        self.assertNotEqual(first["id"], second["id"])

    def test_unknown_coarse_recurrent_and_incomplete_never_auto_merge(self):
        for changes in ({"precision": "day"}, {"precision": "unknown"}, {"course": "recurrent"},
                        {"course": "unknown"}, {"symptoms_complete": False}, {"start": None}, {"end": None}, {"time_text": " "}):
            with self.subTest(changes=changes):
                self.setUp()
                first = self.add(report("r1"))
                second = self.add(report("r2", event(**changes)))
                self.assertNotEqual(first["id"], second["id"])
                self.assertIn(first["id"], second["candidate_episode_ids"])

    def test_absent_event_creates_independent_records(self):
        first = self.add(report("r1", {}))
        second = self.add(report("r2", {}))
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["candidate_episode_ids"], [])

    def test_broad_interval_containment_does_not_create_high_match(self):
        first = self.add(report("r1"))
        broad = event("2026-01-01T00:00:00+08:00", "2026-12-01T00:00:00+08:00")
        second = self.add(report("r2", broad))
        self.assertNotEqual(first["id"], second["id"])

    def test_different_site_negation_and_partial_symptoms_do_not_auto_merge(self):
        variants = [
            [{"key": "咳嗽", "site": "其他部位", "polarity": "present"}],
            [{"key": "咳嗽", "site": "胸部", "polarity": "denied"}],
            [{"key": "咳嗽", "site": "", "polarity": "present"}],
            [{"key": "咳嗽", "site": "胸部", "polarity": "present"},
             {"key": "腹痛", "site": "右下腹", "polarity": "present"}],
        ]
        for symptoms in variants:
            with self.subTest(symptoms=symptoms):
                self.setUp()
                first = self.add(report("r1"))
                second = self.add(report("r2", event(symptoms=symptoms)))
                self.assertNotEqual(first["id"], second["id"])

    def test_touching_windows_and_point_times_not_auto_merged(self):
        first = self.add(report("r1"))
        touching = self.add(report("r2", event("2026-11-11T10:00:00+08:00", "2026-11-11T11:00:00+08:00")))
        self.assertNotEqual(first["id"], touching["id"])
        point = self.add(report("r3", event("2026-11-11T09:00:00+08:00", "2026-11-11T09:00:00+08:00")))
        self.assertNotEqual(first["id"], point["id"])

    def test_pairwise_check_prevents_temporal_chain(self):
        first = self.add(report("r1"))
        # B overlaps A strongly; C overlaps B strongly, but not A strongly enough.
        b = event("2026-11-11T08:15:00+08:00", "2026-11-11T10:15:00+08:00")
        self.assertEqual(first["id"], self.add(report("r2", b))["id"])
        c = event("2026-11-11T08:30:00+08:00", "2026-11-11T10:30:00+08:00")
        self.assertNotEqual(first["id"], self.add(report("r3", c))["id"])

    def test_multiple_candidate_episodes_prevent_auto_selection(self):
        first = self.add(report("r1", event(precision="day")))
        second = self.add(report("r2"))
        third = self.add(report("r3"))
        self.assertEqual(len(self.profile["consultation_episodes"]), 3)
        self.assertEqual(set(third["candidate_episode_ids"]), {first["id"], second["id"]})

    def test_explicit_link_requires_basis_and_same_subject(self):
        first = self.add(report("r1", event(precision="day")))
        second = report("r2", event(start=None, end=None))
        self.profile["consultations"].append(second)
        with self.assertRaises(ValueError):
            add_report_to_episodes(self.profile, second, episode_id=first["id"], basis=" ")
        linked = add_report_to_episodes(self.profile, second, episode_id=first["id"], basis="家属明确说明是本人已记录的那次")
        self.assertEqual(linked["id"], first["id"])
        self.assertTrue(linked["auto_merge_blocked"])
        self.assertEqual(self.profile["consultation_episode_actions"][-1]["action"], "link")
        third = report("r3", subject_member_id="member_other")
        self.profile["consultations"].append(third)
        with self.assertRaises(ValueError):
            add_report_to_episodes(self.profile, third, episode_id=first["id"], basis="尝试跨人关联")

    def test_explicit_merge_and_split_are_auditable_and_sources_unchanged(self):
        first = self.add(report("r1", event(precision="day")))
        second = self.add(report("r2", event(precision="day")))
        before = copy.deepcopy(self.profile["consultations"])
        merged = merge_episodes(self.profile, [first["id"], second["id"]], "问诊上下文明确指出同一次发作")
        self.assertEqual(len(self.profile["consultation_episodes"]), 1)
        self.assertEqual(merged["report_ids"], ["r1", "r2"])
        split = split_report(self.profile, "r2", "核实后发现第二份实际指另一次发作")
        self.assertEqual(len(self.profile["consultation_episodes"]), 2)
        self.assertEqual(split["report_ids"], ["r2"])
        self.assertTrue(split["auto_merge_blocked"])
        self.assertEqual(self.profile["consultations"], before)
        actions = self.profile["consultation_episode_actions"]
        self.assertEqual([a["action"] for a in actions], ["merge", "split"])
        self.assertEqual(len(actions[0]["before"]), 2)
        self.assertEqual(actions[1]["before"][0]["report_ids"], ["r1", "r2"])

    def test_repeated_grouping_same_record_is_idempotent(self):
        source = report("r1")
        first = self.add(source)
        second = add_report_to_episodes(self.profile, source)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["report_ids"], ["r1"])

    def test_normalization_rejects_invalid_times_but_preserves_missing(self):
        for bad in (event(start="2026-11-11T08:00:00"), event(start="yesterday"),
                    event(end="2026-11-10T08:00:00+08:00"), event(symptoms_complete="yes"),
                    event(symptoms=[]), event(precision="second")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize_event(bad)
        self.assertIsNone(normalize_event(None)["start"])
        self.assertEqual(normalize_event(None)["precision"], "unknown")

    def test_symptom_order_does_not_change_full_set_match(self):
        symptoms = [{"key": "咳嗽", "site": "胸部", "polarity": "present"},
                    {"key": "头痛", "site": "前额", "polarity": "present"}]
        first = self.add(report("r1", event(symptoms=symptoms)))
        self.assertEqual(first["id"], self.add(report("r2", event(symptoms=list(reversed(symptoms)))))["id"])

    def test_cross_member_source_corruption_rejected(self):
        first = self.add(report("r1"))
        first["subject_member_id"] = "other_member"
        with self.assertRaises(ValueError):
            render_episode(first, self.profile["consultations"])

    def test_manually_linked_different_symptoms_remain_visible(self):
        first = self.add(report("r1", reporter_name="合成人甲"))
        second = report("r2", event(symptoms=[{"key": "头痛", "site": "右侧", "polarity": "denied"}]),
                        reporter_name="合成人乙", role="family_proxy", selfCare="合成护理记录", observation="合成观察记录")
        self.add(second, episode_id=first["id"], basis="同次就诊中对另一项症状的补充核实")
        self.assertEqual({item["key"] for item in first["symptoms"]}, {"咳嗽", "头痛"})
        rendered = render_episode(first, self.profile["consultations"])
        for value in ("## 合成人甲", "## 合成人乙", "头痛", "右侧", "报告否认", "合成护理记录", "合成观察记录"):
            self.assertIn(value, rendered)
        self.assertNotIn("## 来源：r", rendered)
        self.assertIn("recorded_at", self.profile["consultation_episode_actions"][-1])


if __name__ == "__main__":
    unittest.main()
