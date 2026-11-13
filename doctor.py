#!/usr/bin/env python3
"""Initialize, check, export or erase a personal Family Doctor workspace."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent


def renderer_status():
    """Import in a bounded child process: installation alone is not readiness."""
    installed = importlib.util.find_spec("weasyprint") is not None
    if not installed:
        return {"installed": False, "usable": False, "error": "未安装可选 PDF 渲染器"}
    probe = """import json
try:
    import weasyprint
except Exception as exc:
    message = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    print(json.dumps({'usable': False, 'error': type(exc).__name__ + ': ' + message[:200]}))
else:
    print(json.dumps({'usable': True, 'error': None}))
"""
    try:
        result = subprocess.run([sys.executable, "-c", probe], text=True, capture_output=True,
                                timeout=10, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        if result.returncode != 0:
            return {"installed": True, "usable": False, "error": "渲染器运行检查失败（退出码 {}）".format(result.returncode)}
        status = json.loads(result.stdout.strip().splitlines()[-1])
        return {"installed": True, "usable": status.get("usable") is True, "error": status.get("error")}
    except subprocess.TimeoutExpired:
        return {"installed": True, "usable": False, "error": "渲染器运行检查超时（10 秒）"}
    except (OSError, ValueError, IndexError, TypeError):
        return {"installed": True, "usable": False, "error": "无法读取渲染器运行检查结果"}


def validate_document(path, data, health_dir):
    """Validate structures as well as JSON syntax; never repair during a check."""
    if not isinstance(data, dict):
        raise ValueError("档案必须为 JSON 对象: " + str(path))
    relative = path.relative_to(health_dir)
    if relative == Path("user-profile.json"):
        if not isinstance(data.get("owner"), dict):
            raise ValueError("本人档案缺少有效 owner 对象")
        for key, kind in (("records", dict), ("summary", dict), ("consultations", list)):
            if key in data and not isinstance(data[key], kind):
                raise ValueError("本人档案 {} 结构错误".format(key))
        if "consultation_episodes" in data:
            from consultation_episodes import normalize_event, render_episode
            if not isinstance(data["consultation_episodes"], list):
                raise ValueError("事件索引必须为列表")
            seen = set()
            for episode in data["consultation_episodes"]:
                if not isinstance(episode, dict) or not isinstance(episode.get("report_ids"), list):
                    raise ValueError("事件索引结构无效")
                refs = episode["report_ids"]
                if not refs or len(set(refs)) != len(refs) or seen.intersection(refs):
                    raise ValueError("问诊来源被重复归入不同事件")
                seen.update(refs)
                render_episode(episode, data.get("consultations", []))
            for record in data.get("consultations", []):
                if record.get("event") is not None:
                    normalize_event(record["event"])
    elif relative == Path("types.json"):
        if not isinstance(data.get("types"), dict):
            raise ValueError("类型注册表缺少有效 types 对象")
    elif relative == Path("medications.json"):
        from health_store import normalize_medications
        normalize_medications(data)
    elif relative == Path("vitals.json"):
        from health_store import normalize_vitals
        normalize_vitals(data)
    elif relative == Path("symptoms.json"):
        if not isinstance(data.get("symptoms"), list):
            raise ValueError("症状档案缺少 symptoms 列表")
    elif relative.parts[0] == "imports":
        from manage_records import validate_record, verify_raw
        validate_record(data, path.stem)
        verify_raw(data)
    elif relative.parts[0] == "sessions":
        from consultation_session import validate_session
        validate_session(data, path.stem)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", help="数据工作区；也可设置 FAMILY_DOCTOR_HOME")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "check", "export", "erase"):
        p = sub.add_parser(name)
        p.add_argument("--role", default="self", choices=["self", "proxy", "本人", "代问诊"])
        if name == "export":
            p.add_argument("--output", required=True)
        if name == "erase":
            p.add_argument("--confirm", required=True, help="必须为 DELETE-PERSONAL-HEALTH-DATA")
    args = parser.parse_args()
    if args.data_dir:
        os.environ["FAMILY_DOCTOR_HOME"] = args.data_dir
    sys.path.insert(0, str(ROOT / "skills" / "family-doctor" / "scripts"))
    from health_store import WORKSPACE, HEALTH_DIR, USER_PROFILE, atomic_write_json, ensure_self, load_json, record_context, refresh_file_views
    try:
        ensure_self(args.role)
        context = record_context(args.role)
        if HEALTH_DIR.is_symlink():
            raise ValueError("健康档案目录不能是符号链接")
        if args.command == "init":
            WORKSPACE.mkdir(parents=True, exist_ok=True, mode=0o700)
            HEALTH_DIR.mkdir(exist_ok=True, mode=0o700)
            # Preflight both existing files before creating any missing template.
            for filename in ("user-profile.json", "types.json"):
                target = HEALTH_DIR / filename
                if target.is_symlink():
                    raise ValueError("健康档案文件不能是符号链接")
                if target.exists():
                    existing = validate_document(target, load_json(target), HEALTH_DIR)
                    if filename == "user-profile.json" and context and existing["owner"].get("member_id") != context["subject_member_id"]:
                        raise ValueError("已有档案与家庭成员关联不一致，不能自动初始化覆盖")
            for filename in ("user-profile.json", "types.json"):
                target = HEALTH_DIR / filename
                if not target.exists():
                    template = validate_document(target, load_json(ROOT / "templates" / filename), HEALTH_DIR)
                    if filename == "user-profile.json" and context:
                        template["owner"].update(member_id=context["subject_member_id"], name=context["subject_name"])
                    atomic_write_json(target, template)
            print("已初始化：" + str(HEALTH_DIR))
            print("随后所有脚本使用同一 FAMILY_DOCTOR_HOME=" + str(WORKSPACE))
            refresh_file_views(include_episodes=False)
        elif args.command == "check":
            issues = []
            file_issues = []
            initialized = False
            for path in HEALTH_DIR.rglob("*") if HEALTH_DIR.exists() else []:
                try:
                    if path.is_symlink():
                        raise ValueError("档案中存在符号链接: " + str(path))
                    if path.is_file() and path.suffix == ".json":
                        validate_document(path, load_json(path), HEALTH_DIR)
                        if path == USER_PROFILE:
                            if context and load_json(path)["owner"].get("member_id") != context["subject_member_id"]:
                                raise ValueError("档案成员编号与当前家庭路由不一致")
                            initialized = True
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    issues.append(str(exc))
            if initialized:
                try:
                    from file_lifecycle import check_integrity
                    file_issues = check_integrity(load_json(USER_PROFILE))
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    file_issues.append(str(exc))
            print(json.dumps({"workspace": str(WORKSPACE), "initialized": initialized,
                              "json_errors": issues,
                              "file_errors": file_issues,
                              "optional_dependencies": {
                                  "pypdf": importlib.util.find_spec("pypdf") is not None,
                                  "weasyprint": renderer_status(),
                                  "node": shutil.which("node") is not None,
                                  "pdftoppm": shutil.which("pdftoppm") is not None},
                              "host_model_privacy": "由宿主决定，无法通过本地存储设置验证"},
                             ensure_ascii=False, indent=2))
            return 1 if issues or file_issues or not initialized else 0
        elif args.command == "export":
            if not HEALTH_DIR.exists():
                raise ValueError("尚无健康档案")
            requested_output = Path(args.output).expanduser()
            if requested_output.is_symlink():
                raise ValueError("导出目标不能为符号链接")
            output = requested_output.resolve()
            if output == HEALTH_DIR.resolve() or HEALTH_DIR.resolve() in output.parents:
                raise ValueError("导出文件应位于健康档案目录之外")
            if output.exists():
                raise ValueError("导出目标已存在，请使用新文件名")
            output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temporary = tempfile.mkstemp(dir=output.parent, prefix=".health-export-")
            os.close(fd)
            try:
                with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                    for path in sorted(HEALTH_DIR.rglob("*")):
                        if path.is_symlink():
                            raise ValueError("导出中发现符号链接，已停止")
                        if path.is_file() and not path.name.endswith(".lock"):
                            archive.write(path, str(Path("health-records") / path.relative_to(HEALTH_DIR)))
                os.link(temporary, output)  # Exclusive destination: never overwrite an export.
                print("已导出本地 ZIP（含原始资料和历史备份）：" + str(output))
            finally:
                os.unlink(temporary)
        elif args.command == "erase":
            if args.confirm != "DELETE-PERSONAL-HEALTH-DATA":
                raise ValueError("删除确认短语不匹配")
            # Only the configured health-records tree is in scope; never source or another directory.
            if HEALTH_DIR.exists():
                shutil.rmtree(HEALTH_DIR)
            print("已删除本地健康档案目录（含其中的自动备份）；其他位置的导出文件不在此操作范围。")
    except (OSError, ValueError, TypeError) as exc:
        print("操作失败：" + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
