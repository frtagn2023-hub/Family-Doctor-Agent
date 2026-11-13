#!/usr/bin/env python3
"""Descriptive owner trends; separate units/contexts, no clinical conclusions."""
import argparse
import json
from datetime import datetime, timedelta

from health_store import (DEFAULT_UNITS, VITAL_TYPES, VITALS_FILE, ensure_self,
                          finite_number, load_json, normalize_vitals, parse_time)


def build_report(kind="all", days=7):
    if days <= 0:
        raise ValueError("天数必须大于 0")
    kind = VITAL_TYPES.get(kind, kind)
    data = normalize_vitals(load_json(VITALS_FILE, {"vitals": []}))
    now = datetime.now().astimezone()
    cutoff = now - timedelta(days=days)
    groups = {}
    skipped = []
    for record in data["vitals"]:
        if kind != "all" and record.get("type") != kind:
            continue
        try:
            measured = parse_time(record.get("measured_at"))
        except ValueError:
            skipped.append({"id": record.get("id"), "reason": "测量时间缺失或无效"})
            continue
        if not cutoff <= measured <= now:
            continue
        context = None
        if record.get("type") == "blood_sugar":
            context = record.get("context") or ("fasting" if record.get("fasting") else "unknown")
        key = (record.get("type"), record.get("unit"), context)
        groups.setdefault(key, []).append(record)
    result = {"days": days, "generated_at": now.isoformat(), "groups": [], "skipped": skipped,
              "notice": "仅描述已记录数据；不同单位和血糖测量场景分开统计，单位未知的历史记录不计算均值。"}
    for (record_type, unit, context), records in sorted(groups.items(), key=lambda item: str(item[0])):
        records.sort(key=lambda record: parse_time(record["measured_at"]))
        group = {"type": record_type, "unit": unit, "context": context, "count": len(records), "readings": records}
        if unit is None:
            group["assessment"] = "unknown_unit"
        else:
            valid = []
            for record in records:
                try:
                    if record_type == "blood_pressure":
                        values = (finite_number(record.get("systolic"), "收缩压"), finite_number(record.get("diastolic"), "舒张压"))
                    else:
                        minimum = (-273.15 if unit == "°C" else -459.67) if record_type == "temperature" else 0
                        values = (finite_number(record.get("value"), "数值", minimum, inclusive=record_type == "spo2"),)
                    valid.append(values)
                except ValueError:
                    skipped.append({"id": record.get("id"), "reason": "历史数值无效，未计入统计"})
            if valid:
                labels = ("systolic", "diastolic") if record_type == "blood_pressure" else ("value",)
                group["statistics"] = {}
                for column, label in enumerate(labels):
                    numbers = [row[column] for row in valid]
                    group["statistics"][label] = {"count": len(numbers), "average": round(sum(numbers) / len(numbers), 2),
                                                  "min": min(numbers), "max": max(numbers), "first": numbers[0],
                                                  "latest": numbers[-1], "change": round(numbers[-1] - numbers[0], 2)}
        result["groups"].append(group)
    return result


def print_report(report):
    print("近 {} 天体征趋势".format(report["days"]))
    print(report["notice"])
    if not report["groups"]:
        print("暂无该时间范围内的体征记录")
    for group in report["groups"]:
        print("{} | {} | {} 条 | 场景 {}".format(group["type"], group["unit"] or "单位未知", group["count"], group["context"] or "不适用"))
        for label, values in group.get("statistics", {}).items():
            print("  {}: 均值 {}，范围 {}–{}，首末差 {}".format(label, values["average"], values["min"], values["max"], values["change"]))
    if report["skipped"]:
        print("{} 条记录时间或数值无效，请核对原始数据。".format(len(report["skipped"])))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--type", choices=list(DEFAULT_UNITS) + list(VITAL_TYPES) + ["all"], default="all")
    parser.add_argument("--period", choices=["week", "month"], default="week")
    parser.add_argument("--days", type=int)
    parser.add_argument("--chart", action="store_true", help="兼容旧参数；当前输出结构化统计")
    parser.add_argument("--role", default="self")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        ensure_self(args.role)
        report = build_report(args.type, args.days if args.days is not None else (7 if args.period == "week" else 30))
        print(json.dumps(report, ensure_ascii=False, indent=2)) if args.json else print_report(report)
    except (ValueError, PermissionError, OSError) as exc:
        parser.exit(2, "错误: {}\n".format(exc))


if __name__ == "__main__":
    main()
