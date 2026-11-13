#!/usr/bin/env python3
"""Owner-only symptom records; omitted information remains unknown."""
import argparse
import json
import sys
import uuid
from health_store import SYMPTOMS_FILE, ensure_self, load_json, now_iso, parse_time, update_json, attribute_record, refresh_file_views


def validate_records(data):
    if not isinstance(data, dict) or not isinstance(data.get("symptoms", []), list):
        raise ValueError("症状档案结构无效，symptoms 必须是列表")
    return data


def add_symptom(args):
    if not args.symptom.strip():
        raise ValueError("症状不能为空")
    if not args.source.strip():
        raise ValueError("来源不能为空")
    if not 0 <= args.severity <= 10:
        raise ValueError("严重程度必须在 0–10 之间")
    record = {"id": "sym_" + uuid.uuid4().hex, "symptom": args.symptom.strip(), "severity": args.severity,
              "recorded_at": now_iso(), "source": {"type": "user_report", "reference": args.source.strip()},
              "confirmation_status": "confirmed" if args.confirmed else "unconfirmed"}
    if args.confirmed:
        record.update(confirmed_by="user", confirmed_at=record["recorded_at"])
    for field in ("duration", "location", "character", "triggers", "alleviates", "worsens", "associated", "notes"):
        record[field] = getattr(args, field)
    record["observed_at"] = parse_time(args.observed_at).isoformat() if args.observed_at else record["recorded_at"]
    attribute_record(record, getattr(args, "role", "self"))

    def append(data):
        data = validate_records(data)
        data.setdefault("symptoms", []).append(record)
        data["version"] = "3.0"
        return data
    update_json(SYMPTOMS_FILE, append, {"symptoms": []})
    return record


def common(parser, suppress=False):
    parser.add_argument("--role", default=argparse.SUPPRESS if suppress else "self")
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS if suppress else False)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--symptom" in argv and not any(word in argv for word in ("add", "list")):
        argv.insert(0, "add")
    parser = argparse.ArgumentParser(description=__doc__)
    common(parser)
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    common(add, True)
    add.add_argument("--symptom", required=True)
    add.add_argument("--severity", type=int, required=True, help="用户自评 0–10，不代表医疗风险分级")
    for field in ("duration", "location", "character", "triggers", "alleviates", "worsens", "associated", "notes"):
        add.add_argument("--" + field, default=None, help="未提供则存为未知")
    add.add_argument("--observed-at", help="症状观察时间，ISO 8601")
    add.add_argument("--source", default="用户自述/手动录入", help="资料来源说明")
    add.add_argument("--confirmed", action="store_true", help="用户已核对记录；不代表医生确认")
    listing = commands.add_parser("list")
    common(listing, True)
    args = parser.parse_args(argv)
    try:
        ensure_self(args.role)
        result = add_symptom(args) if args.command == "add" else validate_records(load_json(SYMPTOMS_FILE, {"symptoms": []})).get("symptoms", [])
        if args.command == "add":
            refresh_file_views(include_episodes=False)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "add":
            print("已记录: {}，用户自评 {}/10，记录 ID {}".format(result["symptom"], result["severity"], result["id"]))
        else:
            if not result:
                print("暂无症状记录")
            for record in reversed(result):
                print("{} | {} | {}/10".format(record.get("observed_at", record.get("recorded_at", "时间未知")), record.get("symptom", "未记录"), record.get("severity", "未知")))
    except (ValueError, PermissionError, OSError) as exc:
        parser.exit(2, "错误: {}\n".format(exc))


if __name__ == "__main__":
    main()
