#!/usr/bin/env python3
"""Owner-only medication ledger. Names/doses are user records, not prescriptions."""
import argparse
import json
import sys
import uuid
from datetime import date

from health_store import (MEDICATIONS_FILE, ensure_self, load_json, normalize_medications,
                          now_iso, update_json, attribute_record, record_context, refresh_file_views)


def load_medications():
    return normalize_medications(load_json(MEDICATIONS_FILE, {"medications": []}))


def add_medication(args):
    name, dose, frequency = args.name.strip(), args.dose.strip(), args.freq.strip()
    if not all((name, dose, frequency)):
        raise ValueError("药名、剂量和频率不能为空")
    if not args.source.strip():
        raise ValueError("来源不能为空")
    start = args.start_date or date.today().isoformat()
    try:
        date.fromisoformat(start)
    except ValueError as exc:
        raise ValueError("开始日期格式必须为 YYYY-MM-DD") from exc
    record = {"id": "med_" + uuid.uuid4().hex, "name": name, "dose": dose,
              "frequency": frequency, "status": "active", "purpose": args.purpose,
              "start_date": start, "prescribed_by": args.prescriber,
              "notes": args.notes, "recorded_at": now_iso(),
              "source": {"type": "user_report", "reference": args.source.strip()},
              "confirmation_status": "confirmed" if args.confirmed else "unconfirmed"}
    if args.confirmed:
        record.update(confirmed_by="user", confirmed_at=record["recorded_at"])
    attribute_record(record, getattr(args, "role", "self"))

    def append(data):
        data = normalize_medications(data)
        data["medications"].append(record)
        return data
    update_json(MEDICATIONS_FILE, append, {"medications": []})
    return record


def set_status(args):
    selected = {}

    def change(data):
        data = normalize_medications(data)
        matches = [m for m in data["medications"] if
                   (args.id and m.get("id") == args.id) or
                   (args.name and m["name"] == args.name)]
        if len(matches) != 1:
            raise ValueError("必须唯一匹配一条药物记录；请使用记录 ID（当前匹配 {} 条）".format(len(matches)))
        med = matches[0]
        med.setdefault("id", "med_" + uuid.uuid4().hex)
        med["status"] = "stopped" if args.command == "stop" else args.status
        med["status_updated_at"] = now_iso()
        context = record_context(getattr(args, "role", "self"))
        if context:
            med.setdefault("status_history", []).append(dict(context, status=med["status"], recorded_at=med["status_updated_at"]))
        selected.update(med)
        return data
    update_json(MEDICATIONS_FILE, change, {"medications": []})
    return selected


def add_common(parser, suppress=False):
    parser.add_argument("--role", default=argparse.SUPPRESS if suppress else "self", help="仅 self/本人可访问档案；代问会被拒绝")
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS if suppress else False, help="输出 JSON")


def build_parser():
    parser = argparse.ArgumentParser(description="本人药物记录；停用仅改变档案状态，不是医疗用药建议")
    add_common(parser)
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="记录本人用药")
    add_common(add, True)
    add.add_argument("--name", required=True)
    add.add_argument("--dose", "--dosage", required=True)
    add.add_argument("--freq", "--frequency", required=True)
    add.add_argument("--purpose", default="")
    add.add_argument("--start-date")
    add.add_argument("--prescriber", default="")
    add.add_argument("--notes", default="")
    add.add_argument("--source", default="用户自述/手动录入", help="资料来源说明")
    add.add_argument("--confirmed", action="store_true", help="用户已核对记录；不代表医生确认")
    listing = commands.add_parser("list", help="查看用药")
    add_common(listing, True)
    listing.add_argument("--all", action="store_true", help="包含已停用记录")
    checking = commands.add_parser("check", help="有限演示规则的相互作用检查")
    add_common(checking, True)
    for command in ("stop", "status"):
        sub = commands.add_parser(command, help="更新记录状态，不能代替医生决定")
        add_common(sub, True)
        selector = sub.add_mutually_exclusive_group(required=True)
        selector.add_argument("--id")
        selector.add_argument("--name", help="精确药名；重名时改用 ID")
        if command == "status":
            sub.add_argument("--status", choices=["active", "stopped"], required=True)
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Preserve the original documented no-subcommand add invocation.
    if "--name" in argv and not any(word in argv for word in ("add", "list", "check", "stop", "status")):
        argv.insert(0, "add")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        ensure_self(args.role)
        if args.command == "add":
            result = add_medication(args)
        elif args.command in {"stop", "status"}:
            result = set_status(args)
        elif args.command == "check":
            from check_interactions import evaluate_current
            result = evaluate_current(load_medications()["medications"])
        else:
            result = [m for m in load_medications()["medications"] if args.all or m["status"] == "active"]
        if args.command in {"add", "stop", "status"}:
            refresh_file_views(include_episodes=False)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "check":
            from check_interactions import print_result
            print_result(result)
        elif args.command == "list":
            if not result:
                print("暂无符合条件的药物记录")
            for med in result:
                print("{} | {} {} {} | {}".format(med.get("id", "旧记录（可按药名操作）"), med["name"], med["dose"], med["frequency"], med["status"]))
        else:
            print("已保存记录: {} | {} | {}".format(result["id"], result["name"], result["status"]))
    except (ValueError, PermissionError, OSError) as exc:
        parser.exit(2, "错误: {}\n".format(exc))


if __name__ == "__main__":
    main()
