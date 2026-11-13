#!/usr/bin/env python3
"""Browse and organize the selected member's files without changing clinical facts."""
import argparse
import json
import sys

from health_store import USER_PROFILE, _lock, ensure_self, load_json
from file_catalog import (KINDS, build_catalog, filter_entries, get_entry,
                          rebuild_catalog, render_catalog, render_document_conflicts)


def common(parser):
    parser.add_argument("--role", default="self")
    parser.add_argument("--json", action="store_true", help="输出结构化目录，列表不含整页 OCR 原文")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    subs = parser.add_subparsers(dest="command", required=True)
    listing = subs.add_parser("list", allow_abbrev=False)
    common(listing)
    listing.add_argument("--kind", choices=["all", *KINDS], default="all")
    listing.add_argument("--status", choices=["all", "active", "pending", "archived"], default="default")
    listing.add_argument("--query", help="按标题、文件名、提供者和分类查找")
    listing.add_argument("--from", dest="date_from", help="起始日期 YYYY-MM-DD，包含当日")
    listing.add_argument("--to", dest="date_to", help="结束日期 YYYY-MM-DD，包含当日")
    listing.add_argument("--date-basis", choices=["occurred", "recorded"], default="occurred",
                         help="默认按实际发生/文档日期，recorded 按上传或记录时间")
    listing.add_argument("--sources", action="store_true", help="同时展开已归入事件的原始问诊来源")
    show = subs.add_parser("show", allow_abbrev=False)
    common(show)
    show.add_argument("item_id", help="目录中的完整定位编号")
    rebuild = subs.add_parser("rebuild", allow_abbrev=False)
    common(rebuild)
    for name in ("archive", "restore"):
        sub = subs.add_parser(name, allow_abbrev=False)
        common(sub)
        sub.add_argument("item_id")
        sub.add_argument("--reason", required=True)
    for name in ("link", "unlink"):
        sub = subs.add_parser(name, allow_abbrev=False)
        common(sub)
        sub.add_argument("document_id")
        sub.add_argument("episode_id")
        sub.add_argument("--basis", required=True, help="资料属于该次症状/就诊的事实依据")
    export = subs.add_parser("export", allow_abbrev=False)
    common(export)
    export.add_argument("episode_id")
    export.add_argument("--output", required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        ensure_self(args.role)
        if args.command == "list":
            with _lock(USER_PROFILE):
                profile = load_json(USER_PROFILE, {"owner": {}, "records": {}, "consultations": []})
                entries = build_catalog(profile=profile, include_archived=True,
                                        include_sources=args.sources or args.kind == "consultation")
                result = filter_entries(entries, args.kind, args.status, args.query,
                                        args.date_from, args.date_to, args.date_basis)
            print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render_catalog(result, profile["owner"].get("name")))
        elif args.command == "show":
            with _lock(USER_PROFILE):
                result = get_entry(args.item_id)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(render_catalog([result]))
                for field in result.get("details", {}).get("fields", []):
                    print("{}：{} {}；观察日期：{}；来源第 {} 页".format(
                        field.get("name", "未命名字段"), field.get("value", "未知"), field.get("unit") or "",
                        field.get("observed_at") or "未知", field.get("source_page") or "未知"))
                differences = render_document_conflicts(result)
                if differences:
                    print(differences)
        elif args.command == "rebuild":
            if USER_PROFILE.exists():
                from add_consultation import rebuild_artifacts
                rebuild_artifacts()
            result = {"path": str(rebuild_catalog())}
            print(json.dumps(result, ensure_ascii=False) if args.json else "文件目录已更新：" + result["path"])
        else:
            from file_lifecycle import (archive_item, restore_item, link_document,
                                        unlink_document, export_episode)
            if args.command == "archive":
                result = archive_item(args.item_id, args.reason, role=args.role)
            elif args.command == "restore":
                result = restore_item(args.item_id, args.reason, role=args.role)
            elif args.command == "link":
                result = link_document(args.document_id, args.episode_id, args.basis, role=args.role)
            elif args.command == "unlink":
                result = unlink_document(args.document_id, args.episode_id, args.basis, role=args.role)
            else:
                result = export_episode(args.episode_id, args.output, role=args.role)
            if args.command != "export":
                from health_store import refresh_file_views
                refresh_file_views()
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str) if args.json else
                  ("文件整理已完成，原始资料保留。" if args.command != "export" else "事件资料已导出：" + args.output))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print("文件管理操作失败：" + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
