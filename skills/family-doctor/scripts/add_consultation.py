#!/usr/bin/env python3
"""Preserve consultation sources and consolidate explicitly timed health events."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

from health_store import (USER_PROFILE, CONSULTATIONS_DIR, ensure_self, load_json,
                          update_json, atomic_write_text, now_iso, attribute_record,
                          record_context, _lock, refresh_file_views)
from consultation_episodes import (normalize_event, add_report_to_episodes,
                                   render_episode, merge_episodes, split_report)

UNKNOWN = "未知（未记录或未询问）"


def is_own_consultation(record, owner):
    """Only explicitly self records are used; legacy role is not inferred from names."""
    if owner.get("member_id"):
        return (record.get("subject_member_id") == owner["member_id"]
                and record.get("report_mode") in {"self", "proxy"}
                and bool(record.get("reporter_member_id")))
    role = record.get("role")
    patient = str(record.get("patient") or "").strip()
    names = {str(owner.get(key) or "").strip() for key in ("name", "nickname")}
    names.discard("")
    if role is not None:
        if role not in ("self", "本人"):
            return False
        return not patient or (bool(names) and patient in names)
    # Legacy indexes did not store role, so matching names cannot establish ownership.
    return False


def render_record(record):
    symptoms = record["symptoms"]
    source = record["source"]
    role_label = "家属代问诊" if record.get("report_mode") == "proxy" else "本人"
    lines = [f"# {record['patient'] or '本人'}的问诊来源记录 - {record['date']}", "", "## 患者信息",
             f"- 姓名：{record['patient'] or '未提供'}", f"- 问诊角色：{role_label}",
             f"- 年龄：{record['age'] or UNKNOWN}", f"- 性别：{record['gender'] or UNKNOWN}",
             "", "## 主诉", record["chiefComplaint"], "", "## 症状采集"]
    labels = {"location": "部位", "character": "性质", "severity": "严重程度（0–10）",
              "duration": "持续时间", "triggers": "诱因", "alleviates": "缓解因素",
              "worsens": "加重因素", "associated": "伴随症状"}
    for key, label in labels.items():
        lines.append(f"- {label}：{symptoms[key] if symptoms[key] is not None else UNKNOWN}")
    for label, value in (("既往史", record["history"]), ("用药史", record["medications"]),
                         ("AI 提出的可能原因（待核实，不是医生诊断）", record["aiHypothesis"]),
                         ("医生诊断（按所提供资料记录，未独立核验）", record["clinicianDiagnosis"]),
                         ("风险等级", "待评估" if record["riskLevel"] == "UNKNOWN" else record["riskLevel"]),
                         ("就医建议", record["medicalAdvice"]), ("药物建议", record["drugAdvice"]),
                         ("自我护理", record["selfCare"]), ("复诊时间", record["followup"]),
                         ("观察要点", record["observation"])):
        lines.extend(["", f"## {label}", value or UNKNOWN])
    lines.extend(["", "## 资料来源与确认状态", f"- 来源类型：{source['type']}",
                  f"- 来源引用：{source['reference'] or '未提供'}",
                  f"- 来源日期：{source['date'] or '未提供'}",
                  f"- 确认状态：{record['confirmation_status']}",
                  f"- 记录时间：{record['recordedAt']}", ""])
    if record.get("reporter_name"):
        lines.extend([f"- 信息提供者：{record['reporter_name']}",
                      "- 本文件是问诊整理资料，不是可直接用于语言能力评估的原始表达。", ""])
    event = record.get("event", {})
    if event:
        lines.extend(["## 症状发生时间（与问诊记录时间分开）",
                      f"- 原始时间说法：{event.get('time_text') or '未提供'}",
                      f"- 起始：{event.get('start') or '未知'}；结束：{event.get('end') or '未知'}",
                      f"- 时间精度：{event.get('precision', 'unknown')}", ""])
    return "\n".join(lines)


def validate_profile(profile):
    if (not isinstance(profile, dict) or not isinstance(profile.get("owner"), dict)
            or not isinstance(profile.get("consultations", []), list)
            or not isinstance(profile.get("summary", {}), dict)):
        raise ValueError("问诊索引结构错误，不能自动重置")
    return profile


def update_counts(profile):
    reports = profile.get("consultations", [])
    summary = profile.setdefault("summary", {})
    summary["consultationCount"] = len(reports)
    summary["episodeCount"] = len(profile.get("consultation_episodes", []))
    summary["latestConsultation"] = max((r.get("date", "") for r in reports), default=None)
    profile["lastUpdated"] = now_iso()


def artifact_path(record_id, episode=False):
    prefix = r"(?:episode|ep)_[0-9a-f]{32}" if episode else r"cons_[0-9a-f]{32}"
    if not re.fullmatch(prefix, record_id):
        raise ValueError("无效的问诊或事件编号")
    return CONSULTATIONS_DIR / ("episodes" if episode else "") / (record_id + ".md")


def rebuild_artifacts():
    """The single member index is authoritative; derived files are repairable.

    Lock while reading AND publishing so concurrent profiles cannot replace an
    episode with an older snapshot. Source files are retained on merge/split.
    """
    with _lock(USER_PROFILE):
        profile = validate_profile(load_json(USER_PROFILE))
        reports = profile.get("consultations", [])
        for record in reports:
            if not re.fullmatch(r"cons_[0-9a-f]{32}", record.get("id", "")):
                continue  # Old records are not silently rewritten or migrated.
            path = artifact_path(record["id"])
            if not path.exists():
                atomic_write_text(path, render_record(record))
        active_files = set()
        for episode in profile.get("consultation_episodes", []):
            path = artifact_path(episode["id"], episode=True)
            active_files.add(path.name)
            content = render_episode(episode, reports)
            from document_views import render_document_links
            content += render_document_links(profile, episode, path.parent)
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                atomic_write_text(path, content)
        # Retired derived files stay as notices rather than stale active events.
        directory = CONSULTATIONS_DIR / "episodes"
        for path in directory.glob("*.md") if directory.exists() else []:
            if path.name not in active_files:
                content = "# 已重新整理的事件主档\n\n此事件已合并或拆分，请以当前问诊索引和事件列表为准。原始来源记录仍保留。\n"
                if path.read_text(encoding="utf-8") != content:
                    atomic_write_text(path, content)
    return len(active_files)


def publish_artifacts():
    try:
        rebuild_artifacts()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # A failed view must not undo committed health information or invite a
        # duplicate consultation on retry. Rebuild reads the committed index.
        print("问诊已入档，但展示文件尚未完整更新；可运行 rebuild 恢复：" + str(exc), file=sys.stderr)
    refresh_file_views(include_episodes=False)


def add_consultation(args):
    ensure_self(getattr(args, "role", "self"))
    if not args.chief_complaint.strip():
        raise ValueError("主诉不能为空")
    if args.severity is not None and not 0 <= args.severity <= 10:
        raise ValueError("严重程度应为 0 至 10")
    if args.clinician_diagnosis and (args.source != "clinician_document" or not args.source_ref):
        raise ValueError("记录医生诊断需要 --source clinician_document 和 --source-ref")
    event_input = None
    if getattr(args, "event_json", None):
        if not Path(args.event_json).is_file():
            raise ValueError("指定的事件信息文件不存在")
        event_input = load_json(args.event_json)
        if not isinstance(event_input, dict):
            raise ValueError("指定的事件信息文件必须包含 JSON 对象")
    event = normalize_event(event_input)
    context = record_context(args.role)
    request_id = getattr(args, "request_id", None)
    if not request_id and context:
        request_id = os.environ.get("HERMES_SESSION_MESSAGE_ID")
    if request_id is not None and (not request_id.strip() or len(request_id) > 1000):
        raise ValueError("request-id 必须为不超过 1000 字符的非空来源键")
    request_key = None
    if request_id:
        scope = [context.get("reporter_profile"), context.get("observed_platform"),
                 context.get("reporter_member_id"), context.get("subject_member_id"), request_id]
        request_key = hashlib.sha256(json.dumps(scope, ensure_ascii=False).encode()).hexdigest()
    timestamp = now_iso()
    record_id = "cons_" + uuid.uuid4().hex
    filepath = artifact_path(record_id)
    selected = []
    def update(profile):
        validate_profile(profile)
        owner = profile["owner"]
        names = {owner.get("name"), owner.get("nickname"), context.get("subject_name")} - {None, ""}
        if args.patient and (not names or args.patient not in names):
            raise ValueError("姓名与所选成员档案不一致，请先核对这次咨询的是谁")
        if context and owner.get("member_id") != context["subject_member_id"]:
            raise ValueError("家庭成员编号与档案不一致")
        record = {
            "id": record_id, "date": timestamp[:10], "role": context.get("report_mode", "self"),
            "patient": context.get("subject_name") or owner.get("name") or owner.get("nickname") or "",
            "age": args.age or owner.get("age"), "gender": args.gender or owner.get("gender"),
            "chiefComplaint": args.chief_complaint,
            "symptoms": {key: getattr(args, key) for key in (
                "location", "character", "severity", "duration", "triggers", "alleviates", "worsens", "associated")},
            "history": args.history, "medications": args.medications,
            "aiHypothesis": args.ai_hypothesis or args.diagnosis,
            "clinicianDiagnosis": args.clinician_diagnosis,
            "riskLevel": args.risk_level or "UNKNOWN", "medicalAdvice": args.medical_advice,
            "drugAdvice": args.drug_advice, "selfCare": args.self_care,
            "followup": args.followup, "observation": args.observation,
            "source": {"type": args.source, "reference": args.source_ref, "date": args.source_date},
            "confirmation_status": args.confirmation_status,
            "recordedAt": timestamp, "file": "consultations/" + filepath.name,
            "event": event, "language_sample_eligible": False,
        }
        attribute_record(record, args.role)
        fingerprint_fields = {k: v for k, v in record.items() if k not in {
            "id", "date", "recordedAt", "file", "observed_message_id", "observed_user_id",
            "observed_actor_id", "reporter_name", "subject_name", "patient", "age", "gender"}}
        fingerprint_fields["linked_episode_id"] = getattr(args, "episode_id", None)
        fingerprint_fields["merge_basis"] = getattr(args, "merge_basis", None)
        fingerprint = hashlib.sha256(json.dumps(fingerprint_fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if request_key:
            matches = [r for r in profile.get("consultations", []) if r.get("request_key") == request_key]
            if matches:
                if len(matches) != 1 or matches[0].get("content_fingerprint") != fingerprint:
                    raise ValueError("同一来源键已有不同内容，不能当作重试覆盖；补充问诊请使用新的 request-id")
                selected.append(matches[0])
                return
            record.update(request_key=request_key, content_fingerprint=fingerprint)
        profile.setdefault("consultations", []).append(record)
        before_episodes = copy.deepcopy(profile.get("consultation_episodes", []))
        add_report_to_episodes(profile, record, episode_id=getattr(args, "episode_id", None),
                               basis=getattr(args, "merge_basis", None))
        from file_lifecycle import sync_episode_file_states
        sync_episode_file_states(profile, before_episodes, "add", context=context)
        # Source artifact exists before publishing its authoritative index.
        atomic_write_text(filepath, render_record(record))
        update_counts(profile)
        selected.append(record)
    try:
        update_json(USER_PROFILE, update, default={"version": "3.0", "owner": {}, "consultations": []})
    except Exception:
        try:
            saved = load_json(USER_PROFILE, default={})
            indexed = any(item.get("id") == record_id for item in saved.get("consultations", []))
            if not indexed and filepath.exists():
                filepath.unlink()
        except (OSError, ValueError, TypeError, AttributeError):
            pass  # Preserve recovery evidence when commit outcome is uncertain.
        raise
    publish_artifacts()
    saved = selected[0]
    print(f"{saved['patient'] or '本人'}的问诊已保存（提供者：{saved.get('reporter_name') or '本人'}）：{artifact_path(saved['id'])}")
    return artifact_path(saved["id"])


def list_consultations(args):
    ensure_self(getattr(args, "role", "self"))
    profile = validate_profile(load_json(USER_PROFILE, default={"owner": {}, "consultations": []}))
    records = [c for c in profile.get("consultations", []) if is_own_consultation(c, profile.get("owner", {}))]
    if getattr(args, "sources", False):
        for record in sorted(records, key=lambda item: item.get("date", ""), reverse=True):
            print(f"{record.get('patient') or '本人'} | {record.get('date', '日期未知')} | {record.get('chiefComplaint', '')} | 提供者：{record.get('reporter_name') or '本人'} | {record.get('id', '')}")
        if not records:
            print("暂无归属明确的问诊来源记录")
        return
    included = {r["id"] for r in records}
    episodes = [e for e in profile.get("consultation_episodes", []) if set(e["report_ids"]) <= included]
    print(f"{profile.get('owner', {}).get('name') or '本人'}：{len(episodes)} 个已整理健康事件，{len(records)} 份问诊来源记录")
    for episode in episodes:
        print(render_episode(episode, records))
    grouped = {rid for e in episodes for rid in e["report_ids"]}
    for record in records:
        if record["id"] not in grouped:
            print(f"待整理旧记录：{record.get('date', '')} | {record.get('chiefComplaint', '')}")


def reorganize(args):
    def update(profile):
        validate_profile(profile)
        before_actions = len(profile.get("consultation_episode_actions", []))
        before_episodes = copy.deepcopy(profile.get("consultation_episodes", []))
        if args.command == "merge":
            merge_episodes(profile, args.episodes, args.basis)
        else:
            split_report(profile, args.report, args.basis)
        from file_lifecycle import sync_episode_file_states
        sync_episode_file_states(profile, before_episodes, args.command, context=record_context(args.role))
        for action in profile.get("consultation_episode_actions", [])[before_actions:]:
            action.update(record_context(args.role))
        update_counts(profile)
    update_json(USER_PROFILE, update)
    publish_artifacts()
    print("事件主档已重新整理，原始问诊来源保留。")


def build_parser():
    parser = argparse.ArgumentParser(description="问诊来源与健康事件管理；家庭模式通过 family.py run 调用")
    parser.add_argument("--role", default="self")
    subs = parser.add_subparsers(dest="command", required=True)
    add = subs.add_parser("add")
    add.add_argument("--role", default=argparse.SUPPRESS)
    add.add_argument("--chief-complaint", required=True)
    add.add_argument("--event-json", help="症状实际发生时间和症状集合；未知时不要猜测")
    add.add_argument("--request-id", help="稳定来源键，用于同一消息/调用重试去重")
    add.add_argument("--episode-id", help="已核实为同一事件的主档编号，须同时提供 --merge-basis")
    add.add_argument("--merge-basis", help="确认属于同一次发作的事实依据，不是授权")
    for key in ("patient", "age", "gender", "location", "character", "duration", "triggers", "alleviates", "worsens", "associated", "history", "medications", "diagnosis", "ai-hypothesis", "clinician-diagnosis", "medical-advice", "drug-advice", "self-care", "followup", "observation", "source-ref", "source-date"):
        add.add_argument("--" + key)
    add.add_argument("--severity", type=float)
    add.add_argument("--risk-level", choices=["UNKNOWN", "RED", "ORANGE", "YELLOW", "GREEN", "待评估", "高风险", "中风险", "低风险"])
    add.add_argument("--source", choices=["user_report", "caregiver_report", "clinician_document", "ai_assessment", "other"], default="user_report")
    add.add_argument("--confirmation-status", choices=["unconfirmed", "user_confirmed", "document_recorded"], default="unconfirmed")
    listing = subs.add_parser("list")
    listing.add_argument("--role", default=argparse.SUPPRESS)
    listing.add_argument("--sources", action="store_true", help="展开保留的原始问诊来源")
    for name in ("merge", "split", "rebuild"):
        command = subs.add_parser(name)
        command.add_argument("--role", default=argparse.SUPPRESS)
        if name == "merge":
            command.add_argument("--episodes", nargs="+", required=True)
            command.add_argument("--basis", required=True)
        elif name == "split":
            command.add_argument("--report", required=True)
            command.add_argument("--basis", required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        ensure_self(args.role)
        if args.command == "add":
            add_consultation(args)
        elif args.command == "list":
            list_consultations(args)
        elif args.command == "rebuild":
            print("已重建 {} 份事件主档".format(rebuild_artifacts()))
            refresh_file_views(include_episodes=False)
        else:
            reorganize(args)
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(f"问诊记录操作失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
