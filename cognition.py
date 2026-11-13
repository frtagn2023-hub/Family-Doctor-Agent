#!/usr/bin/env python3
"""Validated observation imports and Hermes chat analysis; no diagnosis or outbound alerts."""
import argparse
import json
from pathlib import Path
import sys

from cognition_store import CognitionStore, ImportValidationError, MAX_CONTENT_BYTES, RevisionConflict


def strict_chat_json(content):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("JSON 字段重复")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("JSON 数值无效")
    return json.loads(content, object_pairs_hook=pairs, parse_constant=constant)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--home")
    parser.add_argument("--profile")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "import"):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument("--file", required=True)
        command.add_argument("--format", choices=("json", "csv"))
        command.add_argument("--batch", action="store_true", help="允许历史文件包含其他已登记 profile 的来源，逐条保留归属")
    stats = commands.add_parser("stats", allow_abbrev=False)
    stats.add_argument("--days", type=int, default=30)
    template = commands.add_parser("template", allow_abbrev=False)
    template.add_argument("--format", choices=("json", "csv"), default="json")
    for name in ("chat-prepare", "chat-record"):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument("--file", default="-", help="JSON 输入；- 从标准输入读取，避免另存聊天文件")
    chat_stats = commands.add_parser("chat-stats", allow_abbrev=False)
    chat_stats.add_argument("--days", type=int, default=30)
    args = parser.parse_args(argv)
    try:
        if args.command.startswith("chat-"):
            from chat_cognition import ChatCognitionStore
            store = ChatCognitionStore(args.home, args.profile)
            if args.command == "chat-stats":
                result = store.snapshot(args.days)
            else:
                if args.file == "-":
                    content = sys.stdin.read(MAX_CONTENT_BYTES + 1)
                else:
                    path = Path(args.file)
                    if path.stat().st_size > MAX_CONTENT_BYTES:
                        raise ValueError("输入过大")
                    content = path.read_text(encoding="utf-8-sig")
                if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
                    raise ValueError("输入过大")
                payload = strict_chat_json(content)
                result = store.prepare(payload) if args.command == "chat-prepare" else store.record(payload)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "template":
            from cognition_store import template_json, template_csv
            print(json.dumps(template_json(), ensure_ascii=False, indent=2) if args.format == "json" else template_csv(), end="\n")
            return 0
        store = CognitionStore(args.home, args.profile)
        if args.command == "stats":
            print(json.dumps(store.snapshot(args.days), ensure_ascii=False, indent=2))
            return 0
        path = Path(args.file)
        if path.stat().st_size > MAX_CONTENT_BYTES:
            raise ValueError("文件过大")
        content = path.read_text(encoding="utf-8-sig")
        format = args.format or path.suffix.lstrip(".").lower()
        result = store.preview(content, format, strict_reporter=not args.batch)
        if args.command == "import" and result["ok"]:
            result = store.commit(content, format, expected_revision=result["revision"],
                                  strict_reporter=not args.batch, expected_digest=result["digest"])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    except ImportValidationError as exc:
        print(json.dumps(exc.preview, ensure_ascii=False, indent=2))
        return 1
    except RevisionConflict:
        print("导入期间资料或家庭登记有变化，请重新校验后重试。", file=sys.stderr)
        return 1
    except (ValueError, OSError, TypeError, UnicodeError):
        print("认知资料操作失败，请检查家庭登记、文件格式、大小和资料完整性；未覆盖原资料。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
