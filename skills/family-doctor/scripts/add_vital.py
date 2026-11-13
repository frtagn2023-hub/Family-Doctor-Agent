#!/usr/bin/env python3
"""Record owner vital signs with explicit units and separate measurement time.

Defaults for NEW entries: mmHg, mmol/L, kg, °C, bpm, %. Legacy missing units
stay unknown. These checks validate data shape, not clinical safety.
"""
import argparse
import json
import sys
import uuid

from health_store import (VITALS_FILE, ALLOWED_UNITS, DEFAULT_UNITS, VITAL_TYPES,
                          ensure_self, finite_number, normalize_vitals, now_iso,
                          parse_time, update_json, attribute_record, refresh_file_views)


def add_vital(args):
    if not args.source.strip():
        raise ValueError("来源不能为空")
    kind = VITAL_TYPES.get(args.type, args.type)
    unit = args.unit or DEFAULT_UNITS[kind]
    unit = {"C": "°C", "F": "°F", "lbs": "lb"}.get(unit, unit)
    if unit not in ALLOWED_UNITS[kind]:
        raise ValueError("{} 单位必须为 {}".format(kind, ", ".join(sorted(ALLOWED_UNITS[kind]))))
    recorded = now_iso()
    measured = parse_time(args.measured_at).isoformat() if args.measured_at else recorded
    record = {"id": "vital_" + uuid.uuid4().hex, "type": kind, "unit": unit,
              "recorded_at": recorded, "measured_at": measured, "notes": args.notes,
              "source": {"type": "user_report", "reference": args.source.strip()},
              "confirmation_status": "confirmed" if args.confirmed else "unconfirmed"}
    if args.confirmed:
        record.update(confirmed_by="user", confirmed_at=recorded)
    attribute_record(record, getattr(args, "role", "self"))
    if kind == "blood_pressure":
        if args.value is not None:
            raise ValueError("血压请使用 --systolic 和 --diastolic")
        systolic = finite_number(args.systolic, "收缩压")
        diastolic = finite_number(args.diastolic, "舒张压")
        if systolic < diastolic:
            raise ValueError("收缩压不能小于舒张压，请核对原始读数")
        record.update(systolic=systolic, diastolic=diastolic, value="{:g}/{:g}".format(systolic, diastolic))
    else:
        if args.systolic is not None or args.diastolic is not None:
            raise ValueError("非血压记录请使用 --value")
        minimum = (-273.15 if unit == "°C" else -459.67) if kind == "temperature" else 0
        value = finite_number(args.value, "数值", minimum, inclusive=kind == "spo2")
        if kind == "spo2" and value > 100:
            raise ValueError("血氧百分比必须在 0–100 之间")
        record["value"] = value
    if args.fasting and kind != "blood_sugar":
        raise ValueError("--fasting 仅适用于血糖")
    if kind == "blood_sugar":
        record["context"] = "fasting" if args.fasting else args.context
        record["fasting"] = record["context"] == "fasting" if record["context"] != "unknown" else None

    def append(data):
        data = normalize_vitals(data)
        data["vitals"].append(record)
        return data
    update_json(VITALS_FILE, append, {"vitals": []})
    return record


def common(parser, suppress=False):
    parser.add_argument("--role", default=argparse.SUPPRESS if suppress else "self")
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS if suppress else False)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--type" in argv and not any(word in argv for word in ("add", "trends")):
        argv.insert(0, "add")
    parser = argparse.ArgumentParser(description=__doc__)
    common(parser)
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add")
    common(add, True)
    add.add_argument("--type", choices=list(DEFAULT_UNITS) + list(VITAL_TYPES), required=True)
    add.add_argument("--systolic", type=float)
    add.add_argument("--diastolic", type=float)
    add.add_argument("--value", type=float)
    add.add_argument("--unit", help="新记录默认 mmHg/mmol/L/kg/°C/bpm/%%，可显式覆盖支持的单位")
    add.add_argument("--measured-at", help="实际测量时间，ISO 8601；省略则用记录时间")
    add.add_argument("--fasting", action="store_true")
    add.add_argument("--context", choices=["fasting", "after_meal", "random", "unknown"], default="unknown")
    add.add_argument("--notes", default="")
    add.add_argument("--source", default="用户自述/手动录入", help="资料来源说明")
    add.add_argument("--confirmed", action="store_true", help="用户已核对记录；不代表医生确认")
    trends = commands.add_parser("trends")
    common(trends, True)
    trends.add_argument("--type", choices=list(DEFAULT_UNITS) + list(VITAL_TYPES) + ["all"], default="all")
    trends.add_argument("--days", type=int, default=30)
    args = parser.parse_args(argv)
    try:
        ensure_self(args.role)
        if args.command == "trends":
            from get_vital_trends import build_report, print_report
            result = build_report(args.type, args.days)
            print(json.dumps(result, ensure_ascii=False, indent=2)) if args.json else print_report(result)
        else:
            result = add_vital(args)
            refresh_file_views(include_episodes=False)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print("已记录: {} = {} {}；测量时间 {}".format(result["type"], result["value"], result["unit"], result["measured_at"]))
    except (ValueError, PermissionError, OSError) as exc:
        parser.exit(2, "错误: {}\n".format(exc))


if __name__ == "__main__":
    main()
