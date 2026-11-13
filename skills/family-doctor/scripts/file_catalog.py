"""Rebuildable, named file views over one member's authoritative health records."""
import copy
from datetime import date, datetime, timezone
import os
from pathlib import Path
import re
from urllib.parse import quote
from zoneinfo import ZoneInfo

from health_store import HEALTH_DIR, USER_PROFILE, _lock, atomic_write_text, load_json, now_iso

KINDS = {"document": "医疗文件", "episode": "健康事件", "consultation": "问诊来源",
         "report": "生成报告", "ledger": "健康台账", "session": "问诊进度"}
STATUSES = {"active": "已整理", "pending": "待核对", "archived": "已归档"}


def safe_relative(value):
    if not isinstance(value, str) or not value or "\\" in value or any(ord(c) < 32 for c in value):
        raise ValueError("文件索引路径无效")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts or ".backups" in relative.parts:
        raise ValueError("文件索引不能指向档案外或历史备份")
    current = HEALTH_DIR
    if current.is_symlink():
        raise ValueError("档案目录不能是符号链接")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("文件索引路径不能包含符号链接")
    return current


def _files(directory, suffixes):
    folder = safe_relative(directory)
    if not folder.exists():
        return []
    result = []
    for root, directories, files in os.walk(folder, followlinks=False):
        for name in directories + files:
            path = Path(root) / name
            if path.is_symlink():
                raise ValueError("档案中存在符号链接：" + str(path))
        directories[:] = [name for name in directories if not name.startswith(".")]
        for name in sorted(files):
            path = Path(root) / name
            if not name.startswith(".") and path.suffix.lower() in suffixes:
                result.append(path)
    return sorted(result)


def _calendar(value):
    if not value:
        return None
    if not isinstance(value, str):
        raise ValueError("日期字段必须为字符串或空值")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is not None:
            stamp = stamp.astimezone(ZoneInfo(os.environ.get("FAMILY_DOCTOR_TIMEZONE", "Asia/Shanghai")))
        return stamp.date()
    except (ValueError, KeyError) as exc:
        raise ValueError("文件索引中的日期或时区无效：" + value) from exc


def _instant(value):
    """Compare explicit event instants without guessing a missing timezone."""
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("事件发生时间必须是带时区的 ISO 8601 时间") from exc
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("事件发生时间缺少时区，不能推测实际先后")
    return stamp.astimezone(timezone.utc)


def _sort_time(value):
    """Sort known instants consistently; date-only/naive legacy values stay coarse.

    Coarse values are placed after exact values on the same calendar day. Their
    timezone or time of day is never fabricated or written back to the record.
    """
    day = _calendar(value)
    if day is None:
        return (0, float("-inf"))
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    instant = _instant(value).timestamp() if stamp.tzinfo is not None and stamp.utcoffset() is not None else float("-inf")
    return day.toordinal(), instant


def _profile(profile):
    profile = load_json(USER_PROFILE, {"owner": {}, "records": {}, "consultations": []}) if profile is None else profile
    if not isinstance(profile, dict) or not isinstance(profile.get("owner"), dict):
        raise ValueError("成员档案格式无效")
    if not isinstance(profile.get("records", {}), dict) or not isinstance(profile.get("consultations", []), list):
        raise ValueError("成员资料索引格式无效")
    return profile


def _owned(record, owner):
    if record.get("subject_member_id") and owner.get("member_id") != record["subject_member_id"]:
        raise ValueError("文件来源关联了其他成员，已停止生成目录")


def _names(sources):
    return list(dict.fromkeys(str(s["reporter_name"]) for s in sources if s.get("reporter_name")))


def _base(kind, key, title, name, path=None):
    if not isinstance(key, str) or not key or any(ord(c) < 32 for c in key):
        raise ValueError("文件条目编号无效")
    if path:
        safe_relative(path)
    return {"id": kind + ":" + key, "kind": kind, "title": str(title or "未命名资料"),
            "member_name": name, "status": "active", "path": path,
            "occurred_start": None, "occurred_end": None, "occurred_ranges": [],
            "recorded_at": None, "source_ids": [], "source_names": [], "alias_ids": []}


def _document_range(entry, record):
    document_date = record.get("document_date")
    entry["document_date"] = document_date
    dates = [document_date] if document_date else [field.get("observed_at") for field in record.get("fields", []) if isinstance(field, dict) and field.get("observed_at")]
    for value in dates:
        _calendar(value)
    dates = sorted(set(dates), key=lambda value: _calendar(value))
    entry["occurred_ranges"] = [{"start": value, "end": value} for value in dates]
    if dates:
        entry.update(occurred_start=dates[0], occurred_end=dates[-1])
    entry["date_basis"] = "document_date" if document_date else "field_observation_dates" if dates else "unknown"


def _receipt_summary(receipt):
    return {key: copy.deepcopy(receipt.get(key)) for key in (
        "import_id", "original_name", "raw_file", "imported_at", "reporter_name", "reporter_member_id",
        "report_mode", "document_date", "title", "document_type", "duplicate_conflicts") if receipt.get(key) is not None}


def _documents(profile, name):
    owner = profile["owner"]
    result, by_id, by_hash, published_receipts = [], {}, {}, {}
    for category, records in profile.get("records", {}).items():
        if not isinstance(records, list):
            raise ValueError("医疗文件分类索引必须为数组")
        for record in records:
            if not isinstance(record, dict) or not record.get("id"):
                raise ValueError("医疗文件索引条目无效")
            _owned(record, owner)
            key = record["id"]
            if key in by_id:
                raise ValueError("同一医疗文件出现在多个分类")
            entry = _base("document", key, record.get("title") or record.get("original_name") or category,
                          name, record.get("raw_file"))
            entry.update(document_type=category, document_date=record.get("document_date"),
                         recorded_at=record.get("imported_at") or record.get("confirmed_at"),
                         confirmed_at=record.get("confirmed_at"), sha256=record.get("sha256"),
                         original_name=record.get("original_name"), field_count=len(record.get("fields", [])),
                         content_conflicts=[], conflict_count=0, has_conflicts=False,
                         pending_uploads=[], pending_review_count=0)
            uploads = record.get("upload_sources") or [dict(record, import_id=key)]
            if not isinstance(uploads, list) or any(not isinstance(u, dict) for u in uploads):
                raise ValueError("文件上传来源必须为数组")
            for upload in uploads:
                _owned(upload, owner)
                conflicts = upload.get("duplicate_conflicts", [])
                if not isinstance(conflicts, list) or any(not isinstance(c, dict) for c in conflicts):
                    raise ValueError("重复上传的差异记录格式无效")
                entry["content_conflicts"].extend({"field": conflict.get("field"),
                    "reporter_name": upload.get("reporter_name"), "imported_at": upload.get("imported_at"),
                    "import_id": upload.get("import_id")} for conflict in conflicts)
            entry["conflict_count"] = len(entry["content_conflicts"])
            entry["has_conflicts"] = bool(entry["content_conflicts"])
            entry["upload_sources"] = [_receipt_summary(u) for u in uploads]
            entry["source_ids"] = list(dict.fromkeys(u.get("import_id") or key for u in uploads))
            entry["source_names"] = _names(uploads)
            entry["alias_ids"] = ["document:" + source for source in entry["source_ids"] if source != key]
            for receipt_id in {key, *entry["source_ids"]}:
                if receipt_id in published_receipts:
                    raise ValueError("同一上传来源被重复归入不同正式文档")
                published_receipts[receipt_id] = entry
            entry["status"] = "active" if record.get("status") == "confirmed" else "pending"
            _document_range(entry, record)
            by_id[key] = entry
            if record.get("sha256"):
                if record["sha256"] in by_hash:
                    raise ValueError("正式文件索引含相同内容的重复文档，请先核对整理")
                by_hash[record["sha256"]] = entry
            result.append(entry)
    pending_groups = {}
    for path in _files("imports", {".json"}):
        receipt = load_json(path)
        if not isinstance(receipt, dict) or receipt.get("id") != path.stem:
            raise ValueError("导入来源文件结构无效：" + path.name)
        # Publication can commit the canonical index before the receipt file.
        # Once indexed, all receipt metadata/provenance comes exclusively from
        # upload_sources, even while a stale pending copy awaits reconciliation.
        if receipt["id"] in published_receipts:
            continue
        _owned(receipt, owner)
        raw = receipt.get("raw_file")
        if raw:
            safe_relative(raw)
        if receipt.get("status") == "confirmed":
            raise ValueError("已确认的上传来源缺少正式文件索引，请先恢复")
        canonical = by_hash.get(receipt.get("sha256"))
        if canonical is not None:
            alias = "document:" + receipt["id"]
            if alias != canonical["id"] and alias not in canonical["alias_ids"]:
                canonical["alias_ids"].append(alias)
            if receipt["id"] not in canonical["source_ids"]:
                canonical["source_ids"].append(receipt["id"])
            if receipt.get("reporter_name") and receipt["reporter_name"] not in canonical["source_names"]:
                canonical["source_names"].append(receipt["reporter_name"])
            canonical["pending_uploads"].append(_receipt_summary(dict(receipt, import_id=receipt["id"])))
            canonical["pending_review_count"] += 1
            continue
        group_key = receipt.get("sha256") or receipt["id"]
        pending_groups.setdefault(group_key, []).append(receipt)
    for receipts in pending_groups.values():
        receipts.sort(key=lambda r: (r.get("imported_at") or "", r["id"]))
        first = receipts[0]
        entry = _base("document", first["id"], first.get("title") or first.get("original_name"), name, first.get("raw_file"))
        entry.update(status="pending", document_type=first.get("document_type"),
                     sha256=first.get("sha256"), original_name=first.get("original_name"),
                     recorded_at=first.get("imported_at"), field_count=len(first.get("fields", [])),
                     upload_sources=[], pending_uploads=[_receipt_summary(dict(r, import_id=r["id"])) for r in receipts],
                     pending_review_count=len(receipts), source_ids=[r["id"] for r in receipts], source_names=_names(receipts),
                     alias_ids=["document:" + r["id"] for r in receipts[1:]])
        _document_range(entry, first)
        result.append(entry)
    for entry in result:
        entry["upload_count"] = len(entry["source_ids"])
    return result


def build_catalog(profile=None, include_archived=False, include_sources=False):
    """Return logical member items; physical duplicates are source receipts.

    Callers selecting a household route validate its role before calling. Passing
    a profile supplies a consistent authoritative snapshot and does not mutate it.
    """
    profile = _profile(profile)
    owner = profile["owner"]
    name = owner.get("name") or "姓名未记录"
    result = _documents(profile, name)
    reports = {}
    for report in profile.get("consultations", []):
        if not isinstance(report, dict) or not report.get("id") or report["id"] in reports:
            raise ValueError("问诊来源索引无效或重复")
        _owned(report, owner)
        reports[report["id"]] = report
    grouped = set()
    episodes = profile.get("consultation_episodes", [])
    if not isinstance(episodes, list):
        raise ValueError("事件索引必须为数组")
    for episode in episodes:
        _owned(episode, owner)
        ids = episode.get("report_ids", [])
        if not ids or len(set(ids)) != len(ids) or set(ids).intersection(grouped) or not set(ids) <= reports.keys():
            raise ValueError("事件引用的问诊来源缺失或重复")
        grouped.update(ids)
        sources = [reports[key] for key in ids]
        keys = list(dict.fromkeys(s.get("key") for source in sources for s in source.get("event", {}).get("symptoms", []) if s.get("key")))
        title = "、".join(keys) or sources[0].get("chiefComplaint") or "健康事件"
        entry = _base("episode", episode["id"], title, name, episode.get("file"))
        ranges = [{"start": source.get("event", {}).get("start"), "end": source.get("event", {}).get("end")} for source in sources]
        ranges = [value for value in ranges if value["start"] or value["end"]]
        times = [value[key] for value in ranges for key in ("start", "end") if value[key]]
        times.sort(key=_instant)
        entry.update(occurred_start=times[0] if times else None, occurred_end=times[-1] if times else None,
                     occurred_ranges=ranges, recorded_at=max((s.get("recordedAt") or "" for s in sources), default=None) or None,
                     source_ids=ids[:], source_names=_names(sources), episode_status=episode.get("status"),
                     source_count=len(ids), has_source_differences=bool(episode.get("conflicts")),
                     candidate_episode_ids=episode.get("candidate_episode_ids", [])[:])
        from file_lifecycle import documents_for_episode
        linked = documents_for_episode(profile, episode)
        entry["linked_documents"] = [{"id": doc.get("file_item_id") or "document:" + doc["id"],
                                      "title": doc.get("title") or doc.get("original_name") or "医疗文件",
                                      "document_date": doc.get("document_date"), "path": doc.get("raw_file"),
                                      "archived": bool(doc.get("archived")), "basis": doc.get("link_basis"),
                                      "source_names": _names(doc.get("upload_sources") or [doc])} for doc in linked]
        for doc in entry["linked_documents"]:
            if doc["path"]:
                safe_relative(doc["path"])
        result.append(entry)
    for key, report in reports.items():
        if key in grouped and not include_sources:
            continue
        entry = _base("consultation", key, report.get("chiefComplaint"), name, report.get("file"))
        event = report.get("event") or {}
        entry.update(occurred_start=event.get("start"), occurred_end=event.get("end"),
                     occurred_ranges=[{"start": event.get("start"), "end": event.get("end")}] if event.get("start") or event.get("end") else [],
                     recorded_at=report.get("recordedAt") or report.get("date"), source_ids=[key], source_names=_names([report]),
                     grouped=key in grouped, ownership="confirmed" if report.get("subject_member_id") or report.get("role") == "self" else "unresolved")
        result.append(entry)
    for path in _files("reports", {".md", ".pdf"}):
        relative = path.relative_to(HEALTH_DIR).as_posix()
        entry = _base("report", relative, path.name, name, relative)
        entry.update(recorded_at=datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(),
                     recorded_at_basis="file_modified_time", size_bytes=path.stat().st_size)
        result.append(entry)
    for filename, title, field in (("user-profile.json", "成员档案与资料索引", None), ("medications.json", "用药台账", "medications"),
                                   ("vitals.json", "体征台账", "vitals"), ("symptoms.json", "症状观察台账", "symptoms")):
        path = safe_relative(filename)
        if path.exists():
            data = profile if field is None else load_json(path)
            if not isinstance(data, dict):
                raise ValueError("健康台账格式无效")
            entry = _base("ledger", filename, title, name, filename)
            values = data.get(field, []) if field else []
            count = sum(len(v) for v in values.values()) if isinstance(values, dict) else len(values)
            entry.update(record_count=count, recorded_at=data.get("lastUpdated"))
            result.append(entry)
    for path in _files("sessions", {".json"}):
        session = load_json(path)
        if not isinstance(session, dict) or session.get("id") != path.stem:
            raise ValueError("问诊进度文件格式无效")
        _owned(session, owner)
        entry = _base("session", session["id"], session.get("chief_complaint") or "问诊进度", name, path.relative_to(HEALTH_DIR).as_posix())
        entry.update(recorded_at=session.get("updated_at") or session.get("created_at"), stage=session.get("stage"), source_names=_names([session]))
        result.append(entry)
    management = profile.get("file_management", {})
    if not isinstance(management, dict) or not isinstance(management.get("items", {}), dict):
        raise ValueError("文件管理状态格式无效")
    states = management.get("items", {})
    for entry in result:
        if entry["kind"] == "document":
            entry["related_episodes"] = [{"id": episode["id"], "title": episode["title"], "path": episode["path"]}
                                         for episode in result if episode["kind"] == "episode"
                                         and any(doc["id"] == entry["id"] for doc in episode.get("linked_documents", []))]
    identities = set()
    for entry in result:
        if entry["id"] in identities:
            raise ValueError("统一文件目录出现重复编号")
        identities.add(entry["id"])
        state = states.get(entry["id"])
        if state is None:
            state = next((states[key] for key in entry["alias_ids"] if key in states), {})
        if not isinstance(state, dict):
            raise ValueError("文件归档状态无效")
        entry["base_status"] = entry["status"]
        if state.get("archived"):
            entry["status"] = "archived"
            entry["archive_reason"] = state.get("reason") or state.get("archive_reason")
        entry["file_exists"] = bool(entry["path"] and safe_relative(entry["path"]).is_file())
    result = [entry for entry in result if include_archived or entry["status"] != "archived"]
    result.sort(key=lambda e: (_sort_time(e.get("occurred_start")), _sort_time(e.get("recorded_at")), e["id"]), reverse=True)
    return result


def filter_entries(entries, kind="all", status="default", query=None, date_from=None, date_to=None, date_basis="occurred"):
    if kind not in {"all", *KINDS} or status not in {"default", "all", "active", "pending", "archived"}:
        raise ValueError("目录类型或状态无效")
    if date_basis not in {"occurred", "recorded"}:
        raise ValueError("日期筛选依据无效")
    first = date.fromisoformat(date_from) if date_from else None
    last = date.fromisoformat(date_to) if date_to else None
    if first and last and first > last:
        raise ValueError("开始日期不能晚于结束日期")
    found = []
    for entry in entries:
        if kind != "all" and entry["kind"] != kind:
            continue
        if status == "default" and entry["status"] == "archived":
            continue
        if status == "pending":
            if entry["status"] == "archived" or (entry["status"] != "pending" and not entry.get("pending_review_count")):
                continue
        elif status not in {"default", "all"} and entry["status"] != status:
            continue
        if query:
            searchable = [entry["title"], entry["member_name"], entry.get("document_type", ""), *entry["source_names"]]
            searchable += [u.get("original_name", "") for u in entry.get("upload_sources", []) + entry.get("pending_uploads", [])]
            if query.casefold() not in " ".join(str(s) for s in searchable).casefold():
                continue
        if first or last:
            ranges = [{"start": entry.get("recorded_at"), "end": entry.get("recorded_at")}] if date_basis == "recorded" else entry.get("occurred_ranges", [])
            matches = False
            for span in ranges:
                start = _calendar(span.get("start") or span.get("end"))
                end = _calendar(span.get("end") or span.get("start"))
                if start is not None and end is not None and (last is None or start <= last) and (first is None or end >= first):
                    matches = True
            if not matches:
                continue
        found.append(entry)
    return found


def get_entry(item_id, profile=None):
    profile = _profile(profile)
    entries = build_catalog(profile, include_archived=True, include_sources=True)
    matched = [entry for entry in entries if item_id == entry["id"] or item_id in entry["alias_ids"]]
    if len(matched) != 1:
        raise ValueError("未找到唯一文件条目，请使用目录中的完整编号")
    result = copy.deepcopy(matched[0])
    kind, key = result["id"].split(":", 1)
    if kind == "document":
        records = [r for values in profile.get("records", {}).values() for r in values if r.get("id") == key]
        if records:
            result["details"] = copy.deepcopy(records[0])
        else:
            pending = load_json(safe_relative("imports/" + key + ".json"))
            if pending:
                result["details"] = {k:v for k,v in pending.items() if k != "pages"}
    elif kind == "episode":
        result["details"] = copy.deepcopy(next(e for e in profile["consultation_episodes"] if e["id"] == key))
        result["sources"] = [copy.deepcopy(r) for r in profile["consultations"] if r["id"] in result["source_ids"]]
    elif kind == "consultation":
        result["details"] = copy.deepcopy(next(r for r in profile["consultations"] if r["id"] == key))
    elif kind in {"ledger", "session"}:
        result["details"] = load_json(safe_relative(result["path"]))
    return result


def _display(value):
    return re.sub(r"([\\`*_{}\[\]()<>#|!])", r"\\\1", str(value)).replace("\n", " ").replace("\r", " ")


def render_catalog(entries, member_name=None, generated_at=None):
    name = member_name or (entries[0]["member_name"] if entries else "成员")
    lines = ["# " + _display(name) + "的文件目录", "", "生成时间：" + _display(generated_at or now_iso()),
             "", "此目录为可重建快照；原始资料和索引保留。归档只影响文件列表，不删除病史或撤销已核对事实。",
             "发生日期与上传/记录日期分开；未知日期不按文件修改时间补齐。", ""]
    for kind, label in KINDS.items():
        selected = [entry for entry in entries if entry["kind"] == kind]
        if not selected:
            continue
        lines.extend(["## " + label, ""])
        for entry in selected:
            spans = []
            for span in entry.get("occurred_ranges", []):
                start, end = span.get("start"), span.get("end")
                text = _display(start or end or "未知")
                if start and end and start != end:
                    text += " 至 " + _display(end)
                if text not in spans:
                    spans.append(text)
            lines.extend(["### " + _display(entry["title"]), "", "- 状态：" + STATUSES.get(entry["status"], "待核实"),
                          "- 发生/文档日期：" + ("；".join(spans) or "未知"),
                          ("- 文件更新时间：" if entry.get("recorded_at_basis") == "file_modified_time" else "- 上传/记录时间：") + _display(entry.get("recorded_at") or "未记录"),
                          "- 提供者：" + _display("、".join(entry["source_names"]) or "来源姓名未记录")])
            if entry.get("pending_review_count"):
                lines.append("- 待核对上传来源：" + str(entry["pending_review_count"]))
            if entry.get("has_conflicts"):
                lines.append("- 来源差异：{} 项，原已核对内容未被覆盖；查看详情可比较各方提交。".format(entry["conflict_count"]))
            if entry["kind"] == "document" and (entry.get("upload_count") or entry.get("source_count")):
                lines.append("- 上传来源数：" + str(entry.get("upload_count") or entry["source_count"]))
            elif entry.get("source_count"):
                lines.append("- 来源问诊数：" + str(entry["source_count"]))
            for doc in entry.get("linked_documents", []):
                label = _display(doc["title"]) + ("（已归档，仍保留为医疗依据）" if doc["archived"] else "")
                lines.append("- 关联文件：" + ("[" + label + "](" + quote(doc["path"], safe="/.") + ")" if doc.get("path") else label))
            for related in entry.get("related_episodes", []):
                lines.append("- 关联事件：" + ("[" + _display(related["title"]) + "](" + quote(related["path"], safe="/.") + ")" if related.get("path") else _display(related["title"])))
            if entry.get("path"):
                lines.append("- [查看文件](" + quote(entry["path"], safe="/.") + ")" if entry["file_exists"] else "- 展示文件尚缺失，可核对索引后重建")
            lines.append("")
    if not entries:
        lines.append("当前筛选条件下暂无文件。")
    return "\n".join(lines) + "\n"


def render_document_conflicts(entry):
    """Show retained differences with people, units and original field labels."""
    labels = {"document_type": "文件分类", "document_date": "文档日期", "title": "文件标题", "fields": "提取字段"}
    lines = []
    def describe(value):
        if isinstance(value, list):
            rows = []
            for field in value:
                if not isinstance(field, dict):
                    raise ValueError("重复上传的字段差异结构无效")
                text = "{}：{} {}".format(field.get("name") or "字段名未知", field.get("value", "未知"), field.get("unit") or "")
                for key, label in (("reference_range", "参考范围"), ("observed_at", "观察日期"),
                                   ("source_page", "来源页"), ("original_text", "原文")):
                    if field.get(key) is not None:
                        text += "；{}：{}".format(label, field[key])
                rows.append(_display(text))
            return "；".join(rows) or "未提供字段"
        return _display(value if value is not None else "未记录")
    for source in entry.get("details", {}).get("upload_sources", []):
        conflicts = source.get("duplicate_conflicts", [])
        if not conflicts:
            continue
        if not lines:
            lines.extend(["## 来源差异（原已核对内容未被覆盖）", ""])
        lines.append("提供者：{}；上传时间：{}；文件：{}".format(
            _display(source.get("reporter_name") or "姓名未记录"), _display(source.get("imported_at") or "未记录"),
            _display(source.get("original_name") or "名称未记录")))
        for conflict in conflicts:
            lines.extend(["- " + labels.get(conflict.get("field"), _display(conflict.get("field") or "差异字段")),
                          "  - 原已核对：" + describe(conflict.get("canonical")),
                          "  - 本次提交：" + describe(conflict.get("submitted"))])
        lines.append("")
    return "\n".join(lines)


def rebuild_catalog():
    with _lock(USER_PROFILE):
        profile = _profile(None)
        entries = build_catalog(profile, include_archived=True)
        archived = sum(entry["status"] == "archived" for entry in entries)
        target = safe_relative("catalog.md")
        content = render_catalog([entry for entry in entries if entry["status"] != "archived"], profile["owner"].get("name"))
        if archived:
            content += "\n另有 {} 项已归档资料，可使用文件管理 list --status archived 查看或恢复；原始医疗资料仍保留。\n".format(archived)
        atomic_write_text(target, content)
    return target
