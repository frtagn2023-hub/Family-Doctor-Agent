#!/usr/bin/env python3
"""Export all recorded personal emergency information without truncating lists."""
import argparse
import json
import sys
from pathlib import Path

from health_store import (USER_PROFILE, MEDICATIONS_FILE, ensure_self, load_json,
                          normalize_medications, atomic_write_text, now_iso, record_context)
from generate_health_report import format_history, format_medications, display_time


def get_emergency_data(role="self"):
    ensure_self(role)
    profile = load_json(USER_PROFILE, default={})
    owner = profile.get("owner", {})
    meds = normalize_medications(load_json(MEDICATIONS_FILE, default={"medications": []}))["medications"]
    return {
        "role": role, "generated_at": now_iso(), **record_context(role),
        "name": owner.get("name") or "未提供",
        "dob": owner.get("birthDate") or "未知",
        "blood_type": owner.get("bloodType") or "未知",
        "allergies": format_history(owner, "allergies"),
        "conditions": format_history(owner, "chronicConditions"),
        "medications": [med for med in meds if med["status"] == "active"],
        "legacy_medications_unverified": format_history(owner, "medications") if owner.get("medications") else None,
        "emergency_contact": owner.get("emergencyContact") or {},
        "note": "已记录资料，未独立核验；未知不等于无。内容较多时请保留完整多页/多屏版本。",
    }


def generate_phone_format(data):
    contact = data["emergency_contact"]
    lines = [(data["name"] + "的急诊健康信息") if data.get("subject_member_id") else "本人急诊健康信息", f"生成时间：{display_time(data['generated_at'])}",
             f"姓名：{data['name']}", f"出生日期：{display_time(data['dob'])}",
             f"血型：{data['blood_type']}（未独立核验）", "", "过敏史：", data["allergies"],
             "", "既往病史：", data["conditions"], "", "当前用药：", format_medications(data["medications"])]
    if data["legacy_medications_unverified"]:
        lines.extend(["", "档案内历史用药（当前状态未核实）：", data["legacy_medications_unverified"]])
    lines.extend(["", "紧急联系人：", f"{contact.get('name') or '未记录'}；{contact.get('relationship') or '关系未知'}；{contact.get('phone') or '电话未知'}", "", data["note"]])
    return "\n".join(lines)


def generate_wallet_format(data):
    # Full text intentionally spans more than one card when necessary.
    return generate_phone_format(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description="生成本人急诊健康信息卡（保留完整关键资料）")
    parser.add_argument("--role", default="self")
    parser.add_argument("--format", choices=["phone", "wallet", "json"], default="phone")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        ensure_self(args.role)
        data = get_emergency_data(args.role)
        output = json.dumps(data, ensure_ascii=False, indent=2) if args.format == "json" else generate_phone_format(data)
        if args.output:
            path = Path(args.output).expanduser()
            atomic_write_text(path, output + "\n")
            print(f"完整急诊健康信息已保存：{path}")
        else:
            print(output)
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(f"急诊健康卡生成失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
