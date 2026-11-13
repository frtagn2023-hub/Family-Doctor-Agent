#!/usr/bin/env python3
"""Initialize the personal profile without replacing previously recorded facts."""
import argparse
import re
import sys

from health_store import (WORKSPACE, USER_PROFILE, ensure_self, load_json,
                          update_json, now_iso, record_context, refresh_file_views)

USER_MD = WORKSPACE / "USER.md"


def profile_is_initialized(profile):
    return profile.get("onboarding", {}).get("status", profile.get("onboarding_status")) == "complete"


def existing_name():
    """Read actual Markdown field contents, not the trailing bold marker."""
    if not USER_MD.exists():
        return ""
    match = re.search(r"^-[ \t]*\*\*Name:\*\*[ \t]*([^\r\n]*)", USER_MD.read_text(encoding="utf-8"), re.M)
    name = match.group(1).strip() if match else ""
    return "" if name in {"未填写", "未记录", "未知"} else name


def collect_user_info():
    print("可按回车跳过；先处理当前健康问题时，也可以稍后再完善档案。")
    info = {}
    for key, prompt in (("name", "希望怎么称呼你："), ("gender", "性别（可跳过）："),
                        ("age", "年龄（岁，可跳过）：")):
        value = input(prompt).strip()
        if key == "age" and value:
            if not value.isdigit() or not 0 <= int(value) < 150:
                raise ValueError("年龄应为 0 至 149 的整数；不足一岁的情况请在档案中补充出生日期。")
        if value:
            info[key] = value
    return info


def save_to_user_profile(info, role="self"):
    ensure_self(role)
    context = record_context(role)
    def update(profile):
        owner = profile.setdefault("owner", {})
        if context:
            if owner and owner.get("member_id") != context["subject_member_id"]:
                raise ValueError("已有档案与家庭成员编号不一致，不能自动覆盖")
            owner.update(member_id=context["subject_member_id"], name=context["subject_name"])
        # Initialization only fills gaps. Updating medical facts is a separate action.
        for key in ("name", "nickname", "gender", "age", "birthDate"):
            if info.get(key) and not owner.get(key):
                owner[key] = info[key]
        for key in ("allergies", "chronicConditions", "medications", "familyHistory"):
            owner.setdefault(key, [])
        owner.setdefault("emergencyContact", {})
        owner.setdefault("factStatus", {})
        for key in ("allergies", "chronicConditions", "medications", "familyHistory"):
            owner["factStatus"].setdefault(key, "unknown" if owner.get(key) else "not_asked")
            if owner["factStatus"][key] in ("confirmed_none", "explicitly_denied"):
                owner["factStatus"][key] = "denied"
        profile.setdefault("records", {})
        profile.setdefault("consultations", [])
        profile.setdefault("summary", {})
        profile["version"] = "3.0"
        profile["onboarding"] = {"status": "complete", "completed_at": now_iso()}
        profile.pop("onboarding_status", None)
        profile["lastUpdated"] = now_iso()
    return update_json(USER_PROFILE, update, default={})


def main(argv=None):
    parser = argparse.ArgumentParser(description="建立本人健康档案；已有事实不会被初始化覆盖")
    parser.add_argument("--role", default="self")
    parser.add_argument("--name")
    parser.add_argument("--gender")
    parser.add_argument("--age")
    parser.add_argument("--skip", action="store_true", help="跳过信息采集，完成可选引导")
    args = parser.parse_args(argv)
    try:
        ensure_self(args.role)
        profile = load_json(USER_PROFILE, default={})
        if profile_is_initialized(profile):
            print("引导已完成，现有健康档案保持原样。")
            return 0
        # Migrate actual existing identity without restarting a questionnaire.
        info = {key: getattr(args, key) for key in ("name", "gender", "age") if getattr(args, key)}
        if not info and not args.skip:
            name = profile.get("owner", {}).get("name") or existing_name()
            info = {"name": name} if name else collect_user_info()
        if info.get("age") and (not str(info["age"]).isdigit() or not 0 <= int(info["age"]) < 150):
            raise ValueError("年龄应为 0 至 149 的整数")
        save_to_user_profile(info, args.role)
        refresh_file_views(include_episodes=False)
        print("本人档案引导已完成；未提供的信息保留为未知，可随时补充。")
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(f"初始化失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
