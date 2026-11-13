"""Explicit, local household routing; this is not an authentication service.

Hermes profiles stay independent. Only the member data directory is shared.
No profile is discovered, enrolled, or identified from a display name or cwd.
"""
import json
import os
from pathlib import Path
import re

MEMBER_ID = re.compile(r"m_[0-9a-f]{32}\Z")


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or any(ord(c) < 32 for c in value):
        raise ValueError(label + "必须为非空文本，且不能含控制字符")
    return value.strip()


def family_home(value=None):
    value = value if value is not None else os.environ.get("FAMILY_DOCTOR_FAMILY_HOME")
    if not value:
        raise ValueError("请通过 --home 或 FAMILY_DOCTOR_FAMILY_HOME 指定共享家庭资料目录")
    requested = Path(value).expanduser()
    if requested.is_symlink():
        raise ValueError("家庭资料目录不能是符号链接")
    home = requested.resolve()
    if home.exists() and not home.is_dir():
        raise ValueError("家庭资料路径不是目录")
    return home


def safe_path(home, *parts):
    """Reject links below the explicit household root, including dangling links."""
    home = family_home(home)
    path = home
    for part in parts:
        if not isinstance(part, str) or part in ("", ".", "..") or "/" in part or "\\" in part:
            raise ValueError("无效家庭资料路径")
        path = path / part
        if path.is_symlink():
            raise ValueError("家庭资料路径不能包含符号链接")
    return path


def validate_registry(data):
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("家庭登记文件格式无效")
    nonempty(data.get("name"), "家庭名称")
    members, profiles = data.get("members"), data.get("profiles")
    if not isinstance(members, dict) or not isinstance(profiles, dict):
        raise ValueError("家庭登记缺少 members / profiles")
    for key, member in members.items():
        if not MEMBER_ID.fullmatch(key) or not isinstance(member, dict) or member.get("id") != key:
            raise ValueError("成员编号无效")
        nonempty(member.get("name"), "成员姓名")
    for key, member_id in profiles.items():
        nonempty(key, "Hermes profile")
        if not isinstance(member_id, str) or member_id not in members:
            raise ValueError("Hermes profile 关联了不存在的成员")
    return data


def read_registry(home=None):
    path = safe_path(family_home(home), "family.json")
    if not path.exists():
        raise ValueError("尚未建立家庭资料目录，请先运行 family.py init")
    try:
        def reject_constant(value):
            raise ValueError("无效 JSON 数值")
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle, parse_constant=reject_constant)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("家庭登记文件损坏，已停止操作，未重置资料") from exc
    return validate_registry(data)


def resolve_member(registry, selector):
    selector = nonempty(selector, "成员姓名或编号")
    if selector in registry["members"]:
        return selector
    matches = [key for key, member in registry["members"].items() if member["name"] == selector]
    if len(matches) != 1:
        raise ValueError("成员姓名重名，请使用成员编号" if matches else "未找到已登记的家庭成员：" + selector)
    return matches[0]


def configured_profile(value=None):
    return nonempty(value or os.environ.get("FAMILY_DOCTOR_PROFILE") or
                    os.environ.get("HERMES_SESSION_PROFILE"), "Hermes profile")


def resolve_context(home, profile, member=None):
    home = family_home(home)
    registry = read_registry(home)
    profile = configured_profile(profile)
    if profile not in registry["profiles"]:
        raise ValueError("当前 Hermes profile 尚未关联家庭成员：" + profile)
    actor = registry["profiles"][profile]
    subject = resolve_member(registry, member) if member else actor
    workspace = safe_path(home, "members", subject)
    safe_path(home, "members", subject, "health-records", "user-profile.json")
    return {"family_home": str(home), "family_name": registry["name"], "profile": profile,
            "actor_member_id": actor, "subject_member_id": subject,
            "actor_name": registry["members"][actor]["name"],
            "subject_name": registry["members"][subject]["name"],
            "workspace": str(workspace), "report_mode": "self" if actor == subject else "proxy"}


def current_context(workspace=None):
    if not os.environ.get("FAMILY_DOCTOR_FAMILY_HOME"):
        # Routing fragments must not silently fall back into a personal directory.
        if os.environ.get("FAMILY_DOCTOR_MEMBER_ID"):
            raise ValueError("成员路由缺少 FAMILY_DOCTOR_FAMILY_HOME")
        return None
    context = resolve_context(None, None, os.environ.get("FAMILY_DOCTOR_MEMBER_ID"))
    requested = Path(workspace or os.environ.get("FAMILY_DOCTOR_HOME", "")).expanduser()
    if not os.environ.get("FAMILY_DOCTOR_HOME") and workspace is None:
        raise ValueError("家庭工具应通过 family.py run 路由到成员档案")
    if requested.is_symlink() or requested.resolve() != Path(context["workspace"]):
        raise ValueError("家庭成员与资料目录不一致，请使用 family.py run")
    return context


def validate_role(role, context=None):
    normalized = {"本人": "self", "代问诊": "proxy"}.get(str(role).strip(), str(role).strip().lower())
    context = current_context() if context is None else context
    if context is None:
        if normalized != "self":
            raise PermissionError("未关联家庭成员的代问诊不读取或写入个人档案")
    elif normalized != context["report_mode"]:
        raise ValueError("问诊角色与当前 profile / 被咨询成员不一致")
    return normalized


def provenance(role=None):
    context = current_context()
    if context is None:
        return {}
    if role is not None:
        validate_role(role, context)
    result = {"subject_member_id": context["subject_member_id"],
              "subject_name": context["subject_name"],
              "reporter_member_id": context["actor_member_id"],
              "reporter_name": context["actor_name"],
              "reporter_profile": context["profile"], "report_mode": context["report_mode"],
              "identity_basis": "configured_profile_binding"}
    # Keep available gateway provenance distinct from the configured profile owner.
    # These identifiers never enroll a person or prove who actually spoke.
    for key in ("ACTOR_ID", "USER_ID", "MESSAGE_ID", "PLATFORM"):
        value = os.environ.get("HERMES_SESSION_" + key)
        if value:
            result["observed_" + key.lower()] = value
    return result
