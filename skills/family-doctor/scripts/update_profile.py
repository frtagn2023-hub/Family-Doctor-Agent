#!/usr/bin/env python3
"""Update only explicitly supplied owner facts after optional onboarding.

New facts use structured provenance. Legacy string facts remain unchanged;
their source or confirmation must never be guessed during an unrelated update.
"""
import argparse
from datetime import date
import json
import re
import sys

from health_store import USER_PROFILE, ensure_self, now_iso, update_json, attribute_record, record_context, refresh_file_views

FACT_FIELDS = ("allergies", "chronicConditions", "familyHistory")
FACT_STATUSES = {"present", "unknown", "not_asked", "denied"}
LEGACY_STATUSES = {"confirmed_none", "explicitly_denied"}
GENDERS = {"male", "female", "other", "unknown", "男", "女", "其他", "未知"}


def nonempty(value, label, max_length=1000):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " 不能为空")
    value = value.strip()
    if len(value) > max_length or any(ord(character) < 32 for character in value):
        raise ValueError(label + " 过长或包含控制字符")
    return value


def validated_owner(profile):
    if not isinstance(profile, dict) or not isinstance(profile.get("owner"), dict):
        raise ValueError("本人档案不存在或结构无效；请先完成可选引导，不能自动重置已有档案")
    owner = profile["owner"]
    if "factStatus" in owner:
        statuses = owner["factStatus"]
        if not isinstance(statuses, dict):
            raise ValueError("owner.factStatus 必须是对象，原档案未改动")
        for field in FACT_FIELDS:
            if field in statuses and statuses[field] not in FACT_STATUSES | LEGACY_STATUSES:
                raise ValueError("未知事实状态: " + field)
    for field in FACT_FIELDS:
        if field not in owner:
            continue
        values = owner[field]
        if not isinstance(values, list):
            raise ValueError("owner." + field + " 必须是列表，原档案未改动")
        for fact in values:
            if isinstance(fact, str):
                nonempty(fact, "旧版事实")
            elif isinstance(fact, dict):
                nonempty(fact.get("value", fact.get("name")), "事实内容")
                if "source" in fact:
                    source = fact["source"]
                    if isinstance(source, dict):
                        if source.get("type") not in {"user_report", "caregiver_report", "document"}:
                            raise ValueError("事实来源类型无效")
                        nonempty(source.get("reference"), "事实来源引用")
                    elif isinstance(source, str):
                        nonempty(source, "事实来源")
                    else:
                        raise ValueError("事实来源结构无效")
                if "confirmation_status" in fact and fact["confirmation_status"] not in {"confirmed", "unconfirmed", "user_reported"}:
                    raise ValueError("事实确认状态无效")
            else:
                raise ValueError("事实列表必须包含有效事实，不能自动清空")
    if "emergencyContact" in owner and not isinstance(owner["emergencyContact"], dict):
        raise ValueError("owner.emergencyContact 必须是对象")
    return owner


def add_fact(args):
    ensure_self(args.role)
    value = nonempty(args.value, "事实内容")
    reference = nonempty(args.source, "来源")
    timestamp = now_iso()
    fact = {"value": value, "source": {"type": args.source_type, "reference": reference},
            "confirmation_status": "confirmed" if args.confirmed else "unconfirmed",
            "recorded_at": timestamp, "confirmed_at": timestamp if args.confirmed else None}
    attribute_record(fact, args.role)

    def update(profile):
        owner = validated_owner(profile)
        values = owner.setdefault(args.field, [])
        existing = next((item for item in values if isinstance(item, dict)
                         and item.get("value") == value and item.get("source") == fact["source"]
                         and item.get("reporter_member_id") == fact.get("reporter_member_id")), None)
        if existing is None:
            values.append(fact)
        elif args.confirmed and existing.get("confirmation_status") != "confirmed":
            existing.update(confirmation_status="confirmed", confirmed_at=timestamp)
        owner.setdefault("factStatus", {})[args.field] = "present"
        profile["lastUpdated"] = timestamp
    return update_json(USER_PROFILE, update)


def set_status(args):
    ensure_self(args.role)
    reference = nonempty(args.source, "状态来源") if args.source is not None else "explicit_profile_status_command"
    timestamp = now_iso()

    def update(profile):
        owner = validated_owner(profile)
        if owner.get(args.field) and not args.replace:
            raise ValueError("已有事实不能隐式清空；核对后使用 --replace，旧版本将保留在备份中")
        evidence = owner.setdefault("factStatusEvidence", {})
        if not isinstance(evidence, dict):
            raise ValueError("owner.factStatusEvidence 必须是对象")
        owner[args.field] = []
        owner.setdefault("factStatus", {})[args.field] = args.status
        evidence[args.field] = {"status": args.status, "source": {"type": "user_report", "reference": reference},
                               "recorded_at": timestamp}
        attribute_record(evidence[args.field], args.role)
        profile["lastUpdated"] = timestamp
    return update_json(USER_PROFILE, update)


def identity_changes(args):
    changes = {}
    for field in ("name", "nickname"):
        value = getattr(args, field)
        if value is not None:
            changes[field] = nonempty(value, field, 200)
    if args.gender is not None:
        gender = nonempty(args.gender, "gender", 50)
        if gender not in GENDERS:
            raise ValueError("gender 应为 男/女/其他/未知 或 male/female/other/unknown")
        changes["gender"] = gender
    if args.age is not None:
        if not re.fullmatch(r"\d{1,3}", str(args.age)) or not 0 <= int(args.age) < 150:
            raise ValueError("age 应为 0 至 149 的整数")
        changes["age"] = int(args.age)
    if args.birth_date is not None:
        birth = date.fromisoformat(args.birth_date)
        today = date.today()
        years = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
        if birth > today or years >= 150:
            raise ValueError("出生日期不能在未来或超过 149 岁")
        if "age" in changes and changes["age"] != years:
            raise ValueError("同时提供的年龄与出生日期不一致")
        changes["birthDate"] = birth.isoformat()
    if not changes:
        raise ValueError("至少提供一项身份字段；不进行空更新")
    return changes


def update_identity(args):
    ensure_self(args.role)
    changes = identity_changes(args)
    context = record_context(args.role)
    if context and "name" in changes and changes["name"] != context["subject_name"]:
        raise ValueError("家庭成员姓名由家庭索引统一维护，不能只修改档案姓名造成不一致")

    def update(profile):
        owner = validated_owner(profile)
        owner.update(changes)
        profile["lastUpdated"] = now_iso()
    return update_json(USER_PROFILE, update)


def update_contact(args):
    ensure_self(args.role)
    changes = {}
    for field in ("name", "phone", "relationship"):
        value = getattr(args, field)
        if value is not None:
            changes[field] = nonempty(value, field, 200 if field != "phone" else 50)
    if not changes:
        raise ValueError("至少提供一项紧急联系人字段")
    if "phone" in changes:
        phone = changes["phone"]
        if not re.fullmatch(r"[+0-9() .xX#-]+", phone) or not 3 <= len(re.sub(r"\D", "", phone)) <= 20:
            raise ValueError("联系电话格式无效")

    def update(profile):
        owner = validated_owner(profile)
        owner.setdefault("emergencyContact", {}).update(changes)
        profile["lastUpdated"] = now_iso()
    return update_json(USER_PROFILE, update)


def common(parser, suppress=False):
    parser.add_argument("--role", default=argparse.SUPPRESS if suppress else "self")
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS if suppress else False)


def make_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    common(parser)
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="追加本人过敏史、既往史或家族史；默认待确认")
    common(add, True)
    add.add_argument("--field", required=True, choices=FACT_FIELDS)
    add.add_argument("--value", required=True)
    add.add_argument("--source", required=True, help="本人陈述说明或文档引用")
    add.add_argument("--source-type", choices=("user_report", "document"), default="user_report")
    add.add_argument("--confirmed", action="store_true", help="本人已核对事实；不代表医生确诊")
    status = commands.add_parser("status", help="明确记录未知、未询问或本人否认；不推断为无")
    common(status, True)
    status.add_argument("--field", required=True, choices=FACT_FIELDS)
    status.add_argument("--status", required=True, choices=("unknown", "not_asked", "denied"))
    status.add_argument("--source", help="状态变更来源说明")
    status.add_argument("--replace", action="store_true", help="明确清空该字段当前事实，保留旧文件备份")
    identity = commands.add_parser("identity", help="只更新明确提供的身份字段")
    common(identity, True)
    for field in ("name", "nickname", "gender", "age", "birth-date"):
        identity.add_argument("--" + field)
    contact = commands.add_parser("contact", help="只更新明确提供的紧急联系人字段")
    common(contact, True)
    for field in ("name", "phone"):
        contact.add_argument("--" + field)
    contact.add_argument("--relationship", "--relation", dest="relationship")
    return parser


def main(argv=None):
    parser = make_parser()
    args = parser.parse_args(argv)
    try:
        # No profile read, lock, or directory creation may precede this guard.
        ensure_self(args.role)
        operation = {"add": add_fact, "status": set_status, "identity": update_identity, "contact": update_contact}[args.command]
        result = operation(args)
        refresh_file_views(include_episodes=False)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("已更新本人档案指定字段；已有其他记录保持原样，旧版本已备份。")
        return 0
    except (ValueError, TypeError, PermissionError, OSError) as error:
        parser.exit(2, "错误: {}\n".format(error))


if __name__ == "__main__":
    sys.exit(main())
