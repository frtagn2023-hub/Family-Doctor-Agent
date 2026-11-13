#!/usr/bin/env python3
"""Track personal consultation progress; never infer clinical urgency from text."""
import argparse
import json
import os
import sys
import uuid

from health_store import HEALTH_DIR, atomic_write_json, ensure_self, load_json, now_iso, update_json, record_context, attribute_record, refresh_file_views

FACT_STATES = {"not_asked", "unknown", "denied", "present"}
REQUIRED_FACTS = {"duration", "severity", "allergies", "medications"}


def session_path(session_id):
    if str(uuid.UUID(session_id)) != session_id:
        raise ValueError("无效会话 ID")
    path = HEALTH_DIR / "sessions" / (session_id + ".json")
    if any(part.is_symlink() for part in (HEALTH_DIR, path.parent, path)):
        raise ValueError("会话存储路径不能包含符号链接")
    return path


def validate_fact(fact):
    if not isinstance(fact, dict) or fact.get("status") not in FACT_STATES:
        raise ValueError("事实状态应为 not_asked/unknown/denied/present")
    if fact["status"] in {"present", "denied"} and (not isinstance(fact.get("source"), str) or not fact["source"].strip()):
        raise ValueError("确认存在或否认必须记录 source")
    if fact["status"] == "present" and fact.get("value") in (None, "", [], {}):
        raise ValueError("已确认存在的事实必须提供 value")


def validate_session(data, session_id):
    if not isinstance(data, dict) or data.get("id") != session_id or data.get("role") != "self":
        raise ValueError("会话不存在或结构错误")
    triage = data.get("triage")
    if not isinstance(triage, dict) or triage.get("status") not in {"unknown", "present", "absent"}:
        raise ValueError("会话分诊状态错误")
    if triage["status"] != "unknown" and (not isinstance(triage.get("basis"), str) or not triage["basis"].strip()):
        raise ValueError("会话分诊状态缺少依据")
    expected = {"unknown": "triage", "present": "urgent_assistance", "absent": "collecting"}[triage["status"]]
    if data.get("stage") not in {expected, "complete"} or not isinstance(data.get("facts"), dict):
        raise ValueError("会话阶段或事实结构错误")
    for fact in data["facts"].values():
        validate_fact(fact)
    return data


def start(args):
    if args.role in ("proxy", "代问诊") and not os.environ.get("FAMILY_DOCTOR_FAMILY_HOME"):
        return {"role": "proxy", "persistent": False, "stage": "triage",
                "instruction": "仅在当前对话处理代问诊；不读取、不写入本人档案。"}
    ensure_self(args.role)
    if not args.chief.strip():
        raise ValueError("主诉不能为空")
    data = {"id": str(uuid.uuid4()), "schema_version": 1, "role": "self",
            "stage": "triage", "chief_complaint": args.chief,
            "triage": {"status": "unknown", "basis": ""}, "facts": {},
            "created_at": now_iso(), "updated_at": now_iso()}
    attribute_record(data, args.role)
    atomic_write_json(session_path(data["id"]), data)
    return data


def update(args):
    def mutate(data):
        validate_session(data, args.session_id)
        if data["stage"] == "complete":
            raise ValueError("会话不存在或已经结束")
        if args.triage:
            if data["triage"]["status"] == "present" and args.triage != "present":
                raise ValueError("本次会话已发现危险信号，不能降级为常规流程；请继续紧急就医协助")
            if args.triage != "unknown" and not args.basis.strip():
                raise ValueError("分诊状态需要填写已确认的依据，不能凭空设为无危险信号")
            data["triage"] = {"status": args.triage, "basis": args.basis, "updated_at": now_iso()}
        if args.facts_json:
            facts = load_json(args.facts_json)
            if not isinstance(facts, dict):
                raise ValueError("facts 应为对象")
            for key, fact in facts.items():
                validate_fact(fact)
                data["facts"][key] = attribute_record({**fact, "recorded_at": now_iso()}, args.role)
        data["stage"] = {"present": "urgent_assistance", "absent": "collecting",
                         "unknown": "triage"}[data["triage"]["status"]]
        data["updated_at"] = now_iso()
        if record_context():
            data["last_updated_by"] = record_context(args.role)
    return update_json(session_path(args.session_id), mutate)


def finish(args):
    def mutate(data):
        validate_session(data, args.session_id)
        if data["stage"] == "complete":
            raise ValueError("会话不存在或已经结束")
        triage = data["triage"]["status"]
        if triage == "present" and args.disposition != "immediate":
            raise ValueError("已发现危险信号，只能结束为紧急就医协助")
        if triage == "unknown" and args.disposition not in {"undetermined", "immediate"}:
            raise ValueError("尚未确认危险信号，不能生成常规分诊结论")
        missing = sorted(key for key in REQUIRED_FACTS
                         if data["facts"].get(key, {}).get("status") not in {"present", "denied"})
        if args.disposition == "observe" and missing:
            raise ValueError("信息不足，不能输出可观察结论：" + ", ".join(missing))
        if not args.basis.strip():
            raise ValueError("请记录分诊依据和信息限制")
        data.update(stage="complete", disposition=args.disposition,
                    decision_basis=args.basis, missing_information=missing, updated_at=now_iso())
        if record_context():
            data["completed_by"] = record_context(args.role)
    return update_json(session_path(args.session_id), mutate)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "show", "update", "finish"):
        p = sub.add_parser(name)
        p.add_argument("--role", required=True, choices=["self", "proxy", "本人", "代问诊"])
        if name == "start":
            p.add_argument("--chief", required=True)
        else:
            p.add_argument("session_id")
        if name == "update":
            p.add_argument("--triage", choices=["unknown", "present", "absent"])
            p.add_argument("--facts-json")
            p.add_argument("--basis", default="")
        if name == "finish":
            p.add_argument("--disposition", required=True, choices=["immediate", "soon", "observe", "undetermined"])
            p.add_argument("--basis", required=True)
    args = parser.parse_args()
    try:
        if args.command != "start":
            ensure_self(args.role)
        result = validate_session(load_json(session_path(args.session_id)), args.session_id) if args.command == "show" else globals()[args.command](args)
        if result is None:
            raise ValueError("会话不存在")
        if args.command != "show" and result.get("persistent") is not False:
            refresh_file_views(include_episodes=False)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("操作失败：" + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
