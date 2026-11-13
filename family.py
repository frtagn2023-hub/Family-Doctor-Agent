#!/usr/bin/env python3
"""One household, independent Hermes profiles, named member health directories."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "skills" / "family-doctor" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from family_context import (family_home, nonempty, read_registry, resolve_context,
                            resolve_member, safe_path, validate_registry)
from health_store import atomic_write_json, atomic_write_text, load_json, now_iso, update_json

TOOLS = {name: SCRIPTS / (name + ".py") for name in (
    "add_consultation", "consultation_session", "manage_records", "update_profile",
    "add_symptom", "add_medication", "add_vital", "list_medications", "check_interactions",
    "get_vital_trends", "generate_health_report", "generate_emergency_card", "welcome", "file_manager")}
TOOLS["doctor"] = ROOT / "doctor.py"


def initialize(home, name):
    name = nonempty(name, "家庭名称")
    def create(current):
        if current is not None:
            validate_registry(current)
            if current["name"] != name:
                raise ValueError("该目录已有其他名称的家庭，未修改现有登记")
            return current
        return {"schema_version": 1, "name": name, "created_at": now_iso(), "members": {}, "profiles": {}}
    return update_json(safe_path(home, "family.json"), create)


def add_member(home, name, profile=None):
    name = nonempty(name, "成员姓名")
    profile = nonempty(profile, "Hermes profile") if profile is not None else None
    result = {}
    def add(current):
        validate_registry(current)
        if profile in current["profiles"]:
            raise ValueError("该 Hermes profile 已有关联；未创建新成员或更改关联")
        member_id = "m_" + uuid.uuid4().hex
        timestamp = now_iso()
        member = {"id": member_id, "name": name, "created_at": timestamp}
        target = safe_path(home, "members", member_id, "health-records", "user-profile.json")
        # Create both templates before publishing the registry entry. A failed
        # publication may leave a recoverable orphan, never a wrongly bound user.
        if target.parent.exists():
            raise ValueError("新成员目录意外存在，已停止")
        template = load_json(ROOT / "templates" / "user-profile.json")
        template["owner"].update(name=name, member_id=member_id)
        template["lastUpdated"] = timestamp
        atomic_write_json(target, template)
        atomic_write_json(target.parent / "types.json", load_json(ROOT / "templates" / "types.json"))
        current["members"][member_id] = member
        if profile:
            current["profiles"][profile] = member_id
        result.update(member, profiles=[profile] if profile else [])
    read_registry(home)  # Never create an implicit household by adding a member.
    update_json(safe_path(home, "family.json"), add)
    return result


def bind_profile(home, member, profile):
    profile = nonempty(profile, "Hermes profile")
    result = {}
    def bind(current):
        validate_registry(current)
        member_id = resolve_member(current, member)
        bound = current["profiles"].get(profile)
        if bound is not None and bound != member_id:
            raise ValueError("该 Hermes profile 已关联其他成员，不能自动重新绑定")
        current["profiles"][profile] = member_id
        result.update(current["members"][member_id], profile=profile)
    read_registry(home)
    update_json(safe_path(home, "family.json"), bind)
    return result


def list_members(home):
    data = read_registry(home)
    return {"name": data["name"], "members": [
        dict(member, profiles=[profile for profile, member_id in data["profiles"].items() if member_id == key])
        for key, member in data["members"].items()]}


def routed_arguments(tool, arguments, role):
    arguments = list(arguments)
    if arguments and arguments[0] == "--":
        arguments.pop(0)
    if "--" in arguments:
        raise ValueError("工具参数不支持额外的 -- 分隔符")
    cleaned = []
    index = 0
    while index < len(arguments):
        arg = arguments[index]
        option = arg.split("=", 1)[0]
        # Existing argparse entry points permit abbreviated option names. Check
        # those too so '--rol self' cannot override the wrapper's selected role.
        if option.startswith("--") and "--role".startswith(option):
            if "=" in arg:
                value = arg.split("=", 1)[1]
            else:
                index += 1
                if index >= len(arguments):
                    raise ValueError("--role 缺少值")
                value = arguments[index]
            value = {"本人": "self", "代问诊": "proxy"}.get(value, value)
            if value != role:
                raise ValueError("指定的 --role 与本人 / 家属代问诊路由不一致")
        elif tool == "doctor" and option.startswith("--") and "--data-dir".startswith(option):
            raise ValueError("家庭路由不能被 --data-dir 改写")
        else:
            cleaned.append(arg)
        index += 1
    # Every allowlisted tool accepts --role after its subcommand / other args.
    return cleaned + ["--role", role]


def run_tool(home, profile, member, tool, arguments):
    if tool not in TOOLS or not TOOLS[tool].is_file():
        raise ValueError("工具不在家庭资料工具清单中")
    context = resolve_context(home, profile, member)
    arguments = routed_arguments(tool, arguments, context["report_mode"])
    env = tool_environment(context)
    return subprocess.run([sys.executable, str(TOOLS[tool])] + arguments, env=env).returncode


def tool_environment(context):
    return dict(os.environ, FAMILY_DOCTOR_FAMILY_HOME=context["family_home"],
               FAMILY_DOCTOR_HOME=context["workspace"], FAMILY_DOCTOR_PROFILE=context["profile"],
               FAMILY_DOCTOR_MEMBER_ID=context["subject_member_id"])


def household_files(home, profile, member=None, rebuild=False):
    """Use a separate routed process per member; module paths never switch live."""
    context = resolve_context(home, profile, member)
    registry = read_registry(home)
    selected = [context["subject_member_id"]] if member else list(registry["members"])
    results = []
    for member_id in selected:
        route = resolve_context(home, context["profile"], member_id)
        env = tool_environment(route)
        row = {"name": route["subject_name"], "member_id": member_id,
               "catalog": "members/" + member_id + "/health-records/catalog.md"}
        if rebuild:
            rebuilt = subprocess.run([sys.executable, str(TOOLS["file_manager"]), "rebuild", "--role", route["report_mode"]],
                                     env=env, text=True, capture_output=True)
            if rebuilt.returncode:
                row["error"] = rebuilt.stderr.strip() or "成员文件目录重建失败"
                results.append(row)
                continue
        listing = subprocess.run([sys.executable, str(TOOLS["file_manager"]), "list", "--status", "all", "--json",
                                  "--role", route["report_mode"]], env=env, text=True, capture_output=True)
        if listing.returncode:
            row["error"] = listing.stderr.strip() or "成员文件目录读取失败"
        else:
            try:
                payload = json.loads(listing.stdout)
                entries = payload if isinstance(payload, list) else payload["items"]
                counts = {}
                for entry in entries:
                    counts[entry["kind"]] = counts.get(entry["kind"], 0) + 1
                row.update(counts=counts, archived=sum(e.get("status") == "archived" for e in entries),
                           pending=sum(e.get("status") == "pending" or bool(e.get("pending_uploads")) for e in entries))
            except (ValueError, TypeError, KeyError) as exc:
                row["error"] = "成员目录结果无法读取：" + str(exc)
        results.append(row)
    result = {"family_name": registry["name"], "generated_at": now_iso(), "members": results}
    if rebuild:
        if member:
            # A filtered request refreshes only that member. It must never
            # replace an existing whole-family catalogue with a partial view.
            if results and not results[0].get("error"):
                result["catalog"] = str(safe_path(home, "members", selected[0], "health-records", "catalog.md"))
            return result
        def literal(value):
            return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("[", "\\[").replace("]", "\\]")
        lines = ["# " + literal(registry["name"]) + "的文件总览", "", "生成时间：" + result["generated_at"], "",
                 "按成员姓名浏览。此页是可重建的目录快照，不合并成员档案或 Hermes 会话。", "",
                 "| 成员 | 文档条目 | 健康事件 | 待核对资料 | 已归档 |", "|---|---:|---:|---:|---:|"]
        for row in results:
            if row.get("error"):
                lines.append("| " + literal(row["name"]) + " | 读取失败：" + literal(row["error"]) + " | — | — | — |")
            else:
                counts = row["counts"]
                lines.append("| [{}]({}) | {} | {} | {} | {} |".format(literal(row["name"]), row["catalog"], counts.get("document", 0),
                             counts.get("episode", 0), row["pending"], row["archived"]))
        lines.extend(["", "文档条目按原文件内容去重；健康事件条目中的待核实关联不代表已确认的独立发作。", ""])
        target = safe_path(home, "catalog.md")
        atomic_write_text(target, "\n".join(lines))
        result["catalog"] = str(target)
    return result


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--home", help="共享家庭资料目录，也可设置 FAMILY_DOCTOR_FAMILY_HOME")
    parser.add_argument("--profile", help="当前 Hermes profile；也可使用 FAMILY_DOCTOR_PROFILE / HERMES_SESSION_PROFILE")
    parser.add_argument("--member", help="被咨询成员的姓名或编号；省略为当前 profile 对应的人")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", allow_abbrev=False)
    init.add_argument("--name", default="我的家庭")
    add = sub.add_parser("add-member", allow_abbrev=False)
    add.add_argument("--name", required=True)
    add.add_argument("--profile", default=argparse.SUPPRESS)
    bind = sub.add_parser("bind-profile", allow_abbrev=False)
    bind.add_argument("--member", required=True)
    bind.add_argument("--profile", required=True)
    sub.add_parser("members", allow_abbrev=False)
    files = sub.add_parser("files", allow_abbrev=False)
    files.add_argument("--json", action="store_true")
    files.add_argument("--rebuild", action="store_true", help="重建各成员目录和家庭文件总览")
    run = sub.add_parser("run", allow_abbrev=False)
    run.add_argument("tool", choices=sorted(TOOLS))
    run.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        home = family_home(args.home)
        if args.command == "init":
            result = initialize(home, args.name)
        elif args.command == "add-member":
            result = add_member(home, args.name, args.profile)
        elif args.command == "bind-profile":
            result = bind_profile(home, args.member, args.profile)
        elif args.command == "members":
            result = list_members(home)
        elif args.command == "files":
            result = household_files(home, args.profile, args.member, args.rebuild)
            if not args.json:
                print(result["family_name"] + "的文件总览")
                for member in result["members"]:
                    if member.get("error"):
                        print(member["name"] + "：" + member["error"])
                    else:
                        print("{}：{} 份文档，{} 个事件，{} 项待核对，{} 项已归档".format(member["name"],
                              member["counts"].get("document", 0), member["counts"].get("episode", 0), member["pending"], member["archived"]))
                if result.get("catalog"):
                    print("文件总览：" + result["catalog"])
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if any(row.get("error") for row in result["members"]) else 0
        else:
            return run_tool(home, args.profile, args.member, args.tool, args.arguments)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print("家庭资料操作失败：" + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
