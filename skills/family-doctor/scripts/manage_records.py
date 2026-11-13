#!/usr/bin/env python3
"""Stage extracted personal documents, then explicitly review and confirm facts."""
import argparse
import copy
from datetime import date
from functools import lru_cache
import hashlib
import importlib.util
import json
import math
import os
import re
from pathlib import Path
import sys
import tempfile
import uuid

from health_store import (HEALTH_DIR, USER_PROFILE, atomic_write_json, ensure_self,
                          load_json, now_iso, update_json, attribute_record, record_context, _lock, refresh_file_views)

MAX_BYTES = 50 * 1024 * 1024
MAX_MODEL_PAGES = 50
MODEL_METADATA_KEYS = ("extraction_method", "extraction_tool", "extraction_schema_version", "coverage_basis", "extraction_warnings")
DOCUMENT_TYPES = {"体检报告", "检验结果", "处方", "影像报告", "病历", "其他"}
PROVENANCE_KEYS = ("subject_member_id", "subject_name", "reporter_member_id", "reporter_name",
                   "reporter_profile", "report_mode", "identity_basis", "observed_actor_id",
                   "observed_user_id", "observed_message_id", "observed_platform")
SOURCE_METADATA_KEYS = ("original_name", "document_type", "title", "document_date", "imported_at",
                        "reviewed_at", "reviewed_by", "request_key", "content_fingerprint") + PROVENANCE_KEYS + MODEL_METADATA_KEYS


def document_metadata(title=None, document_date=None):
    if title is not None and (not isinstance(title, str) or not title.strip() or len(title) > 300
                              or any(ord(c) < 32 for c in title)):
        raise ValueError("文档标题应为不含控制字符的非空文本，不超过 300 字")
    if document_date is not None:
        if not isinstance(document_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", document_date):
            raise ValueError("文档日期必须为 YYYY-MM-DD；未知时省略，不用上传日期代替")
        date.fromisoformat(document_date)
    return {"title": title.strip() if title else None, "document_date": document_date}


def stored_path(relative):
    """Reject traversal and symbolic links before touching personal storage."""
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("档案路径必须位于健康档案目录内")
    current = HEALTH_DIR
    for component in (None,) + relative.parts:
        if component is not None:
            current = current / component
        if current.is_symlink():
            raise ValueError("健康档案路径不能包含符号链接")
    return current


def validate_pages(pages, page_count=None, complete=False):
    if not isinstance(pages, list) or not pages:
        raise ValueError("提取结果必须包含带页码的 pages 数组")
    numbers = []
    for page in pages:
        if (not isinstance(page, dict) or type(page.get("page_number")) is not int
                or page["page_number"] <= 0 or not isinstance(page.get("text"), str)):
            raise ValueError("页码必须是正整数，页面必须包含文本")
        numbers.append(page["page_number"])
    if len(set(numbers)) != len(numbers):
        raise ValueError("页码不得重复")
    if page_count is not None and (type(page_count) is not int or page_count <= 0 or max(numbers) > page_count):
        raise ValueError("原始文档页数无效")
    if complete:
        expected = page_count if page_count is not None else len(pages)
        if sorted(numbers) != list(range(1, expected + 1)) or any(page.get("status") != "ok" for page in pages):
            raise ValueError("存在提取失败或缺失页面，请重新提取并核对后再入档")
    return pages


@lru_cache(maxsize=1)
def visual_inspector():
    """Use the installed adapter beside this skill, independent of the checkout."""
    path = Path(__file__).resolve().parents[2] / "pdf-text-extract" / "visual.py"
    spec = importlib.util.spec_from_file_location("family_doctor_visual_source", path)
    if spec is None or spec.loader is None:
        raise ValueError("视觉转录校验器不可用，请检查完整安装")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except ImportError as exc:
        raise ValueError("视觉转录校验器依赖不可用，请检查安装") from exc
    return module.inspect_source


def has_model_vision(value):
    pages = value.get("pages", value.get("source_pages", []))
    return (value.get("extraction_method") == "model_vision"
            or value.get("tool", value.get("extraction_tool")) == "family-doctor-model-vision"
            or value.get("coverage_basis") == "local_manifest"
            or (isinstance(pages, list) and any(isinstance(page, dict)
                and page.get("extraction_method") == "model_vision" for page in pages)))


def string_list(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def validate_model_pages(pages, page_count, status, actual_count=None, complete=False):
    """Check declared completeness, not transcription accuracy or who supplied it."""
    validate_pages(pages, page_count)
    if (type(page_count) is not int or not 1 <= page_count <= MAX_MODEL_PAGES
            or (actual_count is not None and page_count != actual_count)
            or sorted(page["page_number"] for page in pages) != list(range(1, page_count + 1))):
        raise ValueError("视觉转录页数必须与原文件一致，且逐页覆盖全部真实页码")
    if status not in {"ok", "partial", "empty", "error"}:
        raise ValueError("视觉转录整体状态缺失或无效")
    for page in pages:
        if (page.get("extraction_method") != "model_vision"
                or page.get("status") not in {"ok", "partial", "empty", "error"}
                or "confidence" not in page or page["confidence"] is not None
                or not string_list(page.get("issues")) or not string_list(page.get("warnings"))):
            raise ValueError("视觉转录每页必须保留 method、状态、null confidence、issues 和 warnings")
        if page["status"] == "ok" and (not page["text"].strip() or page["issues"]):
            raise ValueError("视觉转录的 ok 页面必须有非空原文，且没有未解决 issues")
    all_ok = all(page["status"] == "ok" for page in pages)
    if (status == "ok") != all_ok:
        raise ValueError("视觉转录整体状态与逐页状态不一致，不能标为完整")
    if complete and not all_ok:
        raise ValueError("视觉转录仍有 partial、empty 或 error 页面，核对并重新转录后再入档")


def validate_model_schema(value, receipt=False):
    version = value.get("extraction_schema_version" if receipt else "schema_version")
    tool = value.get("extraction_tool" if receipt else "tool")
    warnings = value.get("extraction_warnings" if receipt else "warnings")
    if (type(version) is not int or version != 1 or tool != "family-doctor-model-vision"
            or value.get("extraction_method") != "model_vision"
            or value.get("coverage_basis") != "local_manifest" or not string_list(warnings)):
        raise ValueError("视觉转录缺少完整的版本、工具、提取方法、覆盖依据或警示信息")


def inspect_model_source(path, digest):
    inspected = visual_inspector()(path, max_bytes=MAX_BYTES, max_pages=MAX_MODEL_PAGES)
    if inspected["source"]["sha256"] != digest:
        raise ValueError("视觉转录校验期间原文件内容发生变化")
    return inspected


def model_metadata(extraction):
    return {"extraction_method": "model_vision", "extraction_tool": extraction["tool"],
            "extraction_schema_version": extraction["schema_version"],
            "coverage_basis": extraction["coverage_basis"], "extraction_warnings": copy.deepcopy(extraction["warnings"])}


def validate_record(record, record_id):
    if not isinstance(record, dict) or record.get("id") != record_id:
        raise ValueError("导入记录不存在或结构错误")
    if record.get("role") != "self" or record.get("status") not in {"pending", "confirmed"}:
        raise ValueError("导入记录身份或状态错误")
    if record.get("document_type") not in DOCUMENT_TYPES or not isinstance(record.get("fields"), list):
        raise ValueError("导入记录分类或字段结构错误")
    if not isinstance(record.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"]):
        raise ValueError("导入记录 SHA256 无效")
    if not isinstance(record.get("raw_file"), str) or Path(record["raw_file"]).parts[:1] != ("raw",):
        raise ValueError("导入记录原文件路径无效")
    stored_path(record["raw_file"])
    document_metadata(record.get("title"), record.get("document_date"))
    for key in ("canonical_document_id", "duplicate_of"):
        if key in record and str(uuid.UUID(record[key])) != record[key]:
            raise ValueError("文档关联编号无效")
    validate_pages(record.get("pages"), record.get("page_count"))
    if has_model_vision(record):
        validate_model_schema(record, receipt=True)
        validate_model_pages(record["pages"], record.get("page_count"), record.get("extraction_status"))
    return record


def record_path(record_id):
    if str(uuid.UUID(record_id)) != record_id:
        raise ValueError("无效的记录 ID")
    return stored_path(Path("imports") / (record_id + ".json"))


def validated_fields(fields, pages):
    if not isinstance(fields, list) or not fields:
        raise ValueError("至少提供一项待核对字段")
    page_map = {p["page_number"]: p for p in pages}
    result = []
    for field in fields:
        if not isinstance(field, dict):
            raise ValueError("字段必须是对象")
        item = dict(field)
        for key in ("name", "original_text", "observed_at"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValueError("字段缺少 " + key)
        from datetime import date
        date.fromisoformat(item["observed_at"])
        value = item.get("value")
        if isinstance(value, bool) or value is None or isinstance(value, (dict, list)):
            raise ValueError("value 必须是文本或有限数值")
        if isinstance(value, (int, float)):
            if not math.isfinite(value) or not isinstance(item.get("unit"), str) or not item["unit"].strip():
                raise ValueError("数值必须有限且有明确单位")
        elif not isinstance(value, str) or not value.strip():
            raise ValueError("value 不能为空")
        else:
            try:
                float(value)
            except ValueError:
                pass
            else:
                raise ValueError("数值不能写成字符串；请使用 JSON 数值并提供明确 unit")
        if type(item.get("source_page")) is not int or item["source_page"] <= 0:
            raise ValueError("source_page 必须为正整数")
        page = page_map.get(item["source_page"])
        if page is None or item["original_text"] not in page.get("text", ""):
            raise ValueError("original_text 必须出现在指定页的提取文本中")
        confidence = item.get("confidence")
        if confidence is not None and (isinstance(confidence, bool) or
                not isinstance(confidence, (int, float)) or not 0 <= confidence <= 100):
            raise ValueError("confidence 应在 0–100 之间，或为 null")
        if page.get("extraction_method") == "model_vision" and confidence is not None:
            raise ValueError("模型视觉转录字段的 confidence 应为 null，不能编造 OCR 置信度")
        item["confirmation_status"] = "pending"
        result.append(item)
    return result


def stage(args):
    # One receipt per stable request, across concurrent Hermes processes. This
    # lock never acquires the member profile lock, keeping the lock order acyclic.
    prepared = prepare_stage(args)
    with _lock(stored_path(Path("imports") / ".stage")):
        return _stage(args, prepared)


def prepare_stage(args):
    stored_path("imports")
    stored_path("raw")
    source = Path(args.file).expanduser().resolve(strict=True)
    if not source.is_file() or source.stat().st_size > MAX_BYTES:
        raise ValueError("原文件必须存在且不超过 50 MiB")
    content = source.read_bytes()
    if len(content) > MAX_BYTES:
        raise ValueError("原文件不超过 50 MiB")
    digest = hashlib.sha256(content).hexdigest()
    extraction = load_json(Path(args.extraction_json))
    if (not isinstance(extraction, dict) or not isinstance(extraction.get("source"), dict)
            or extraction["source"].get("sha256") != digest):
        raise ValueError("提取结果的 SHA256 与原文件不一致")
    pages = validate_pages(extraction.get("pages"), extraction.get("page_count"))
    if has_model_vision(extraction):
        validate_model_schema(extraction)
        inspected = inspect_model_source(source, digest)
        if (not isinstance(extraction["source"].get("path"), str)
                or not extraction["source"]["path"].strip()
                or type(extraction["source"].get("size_bytes")) is not int
                or extraction["source"]["size_bytes"] != inspected["source"]["size_bytes"]):
            raise ValueError("视觉转录原件来源信息不完整或文件大小不一致")
        validate_model_pages(pages, extraction.get("page_count"), extraction.get("status"), inspected["page_count"])
    fields = validated_fields(load_json(Path(args.fields_json)), pages) if args.fields_json else []
    metadata = document_metadata(getattr(args, "title", None), getattr(args, "document_date", None))
    suffix = source.suffix.lower()
    supported = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".txt"}
    if has_model_vision(extraction) and inspected["kind"] == "image":
        supported.update({".bmp", ".gif"})
    if suffix not in supported:
        raise ValueError("不支持此原文件扩展名")
    return source, content, digest, extraction, pages, fields, suffix, metadata


def _stage(args, prepared):
    source, content, digest, extraction, pages, fields, suffix, metadata = prepared
    context = record_context(getattr(args, "role", None))
    request_id = getattr(args, "request_id", None)
    if request_id is None and context and context.get("observed_message_id"):
        # One Feishu message may carry multiple attachments.
        request_id = context["observed_message_id"] + ":" + digest + ":" + source.name
    request_key = None
    fingerprint_data = {"sha256": digest, "original_name": source.name, "document_type": args.document_type,
                        "metadata": metadata, "fields": fields, "pages": pages,
                        "page_count": extraction.get("page_count", len(pages)),
                        "extraction_status": extraction.get("status", "unknown")}
    if has_model_vision(extraction):
        fingerprint_data.update(model_metadata(extraction))
    fingerprint = hashlib.sha256(json.dumps(fingerprint_data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if request_id is not None:
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 2000:
            raise ValueError("request-id 必须为不超过 2000 字符的非空文本")
        scope = [context.get("subject_member_id"), context.get("reporter_member_id"),
                 context.get("reporter_profile"), context.get("observed_platform"), request_id]
        request_key = hashlib.sha256(json.dumps(scope, ensure_ascii=False).encode()).hexdigest()
        matches = []
        for path in stored_path("imports").glob("*.json"):
            old = validate_record(load_json(stored_path(Path("imports") / path.name)), path.stem)
            if old.get("request_key") == request_key:
                matches.append(old)
        if matches:
            if len(matches) != 1 or matches[0].get("content_fingerprint") != fingerprint:
                raise ValueError("同一上传来源键已有不同内容，不能覆盖；新的附件提交请使用新的 request-id")
            verify_raw(matches[0])
            return matches[0]
    raw_dir = stored_path("raw")
    raw_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = stored_path(Path("raw") / (digest + suffix))
    # File names/extensions are upload metadata. Identical bytes reuse one blob.
    existing_blobs = sorted(raw_dir.glob(digest + ".*"))
    if existing_blobs:
        raw = stored_path(Path("raw") / existing_blobs[0].name)
    if raw.exists() and hashlib.sha256(raw.read_bytes()).hexdigest() != digest:
        raise ValueError("已存原文件校验失败，请先恢复备份")
    if not raw.exists():
        fd, temporary = tempfile.mkstemp(dir=raw_dir, prefix=".upload-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, raw)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    record = {"id": str(uuid.uuid4()), "schema_version": 1, "role": "self",
              "status": "pending", "imported_at": now_iso(), "source_type": "document",
              "original_name": source.name, "raw_file": str(raw.relative_to(HEALTH_DIR)),
              "sha256": digest, "document_type": args.document_type,
              "extraction_status": extraction.get("status", "unknown"), "pages": pages,
              "page_count": extraction.get("page_count", len(pages)),
              "fields": fields}
    record.update(metadata)
    if has_model_vision(extraction):
        record.update(model_metadata(extraction))
    if request_key:
        record.update(request_key=request_key, content_fingerprint=fingerprint)
    attribute_record(record, getattr(args, "role", None))
    atomic_write_json(record_path(record["id"]), record)
    return record


def indexed_records(profile):
    if not isinstance(profile, dict):
        raise ValueError("本人档案格式错误")
    records = profile.get("records", {})
    if not isinstance(records, dict):
        raise ValueError("records 需先迁移为分类对象")
    if (not isinstance(profile.get("summary", {}), dict)
            or not isinstance(profile.get("consultations", []), list)):
        raise ValueError("本人档案 summary/consultations 结构错误")
    for entries in records.values():
        if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
            raise ValueError("分类记录必须是对象列表")
        for entry in entries:
            sources = entry.get("upload_sources")
            if sources is None:  # Older confirmed documents had a single receipt.
                continue
            if not isinstance(sources, list) or not sources:
                raise ValueError("文档上传来源列表无效")
            seen = set()
            for source in sources:
                if not isinstance(source, dict) or not isinstance(source.get("import_id"), str):
                    raise ValueError("文档上传来源必须包含有效编号")
                source_id = source["import_id"]
                if str(uuid.UUID(source_id)) != source_id or source_id in seen:
                    raise ValueError("文档上传来源编号无效或重复")
                seen.add(source_id)
                if (source.get("document_type") not in DOCUMENT_TYPES
                        or not isinstance(source.get("submitted_fields"), list)
                        or not source["submitted_fields"]
                        or not isinstance(source.get("duplicate_conflicts", []), list)
                        or source.get("subject_member_id") != entry.get("subject_member_id")):
                    raise ValueError("文档上传来源内容或成员关联无效")
                document_metadata(source.get("title"), source.get("document_date"))
                raw = source.get("raw_file")
                if not isinstance(raw, str) or Path(raw).parts[:1] != ("raw",):
                    raise ValueError("文档上传来源的原文件路径无效")
                stored_path(raw)
    return records


def verify_raw(record):
    raw = stored_path(record["raw_file"])
    if (not raw.is_file() or raw.stat().st_size > MAX_BYTES
            or hashlib.sha256(raw.read_bytes()).hexdigest() != record["sha256"]):
        raise ValueError("原文件校验失败")
    return raw


def canonical_pages(entry, record=None):
    pages = entry.get("source_pages")
    if pages is None:
        original = record if record and record["id"] == entry["id"] else load_json(record_path(entry["id"]))
        if not isinstance(original, dict):
            raise ValueError("旧文档缺少原始提取页，不能核验重复资料")
        pages = original.get("pages")
    return validate_pages(pages)


def validate_published(category, entry, record=None):
    if (category not in DOCUMENT_TYPES or entry.get("status") != "confirmed" or entry.get("role") != "self"
            or not isinstance(entry.get("confirmed_at"), str) or not entry["confirmed_at"].strip()):
        raise ValueError("已发布索引的来源或确认信息不一致，请先检查档案")
    raw = verify_raw(entry)
    pages = canonical_pages(entry, record)
    if has_model_vision(entry):
        validate_model_schema(entry, receipt=True)
        validate_model_pages(pages, entry.get("page_count"), entry.get("extraction_status"),
                             inspect_model_source(raw, entry["sha256"])["page_count"], complete=True)
    validated_fields(entry.get("fields"), pages)
    if any(field.get("confirmation_status") != "confirmed" or field.get("source_record") != entry["id"]
           or field.get("confirmed_at") != entry["confirmed_at"] or field.get("confirmed_by") != "user"
           for field in entry["fields"]):
        raise ValueError("已发布字段的确认信息不一致")
    return entry


def published_entry(profile, record):
    matches = [(category, item) for category, entries in indexed_records(profile).items()
               for item in entries if item.get("id") == record["id"]
               or any(source.get("import_id") == record["id"] for source in item.get("upload_sources", []))]
    if len(matches) > 1:
        raise ValueError("同一上传来源出现在多个文档索引中，请先修复索引")
    if not matches:
        return None
    category, entry = matches[0]
    validate_published(category, entry, record)
    if entry.get("sha256") != record["sha256"]:
        raise ValueError("已发布索引与上传来源的文件内容不一致")
    if entry["id"] == record["id"] and entry.get("raw_file") != record["raw_file"]:
        raise ValueError("已发布索引的原文件路径不一致")
    if entry["id"] != record["id"]:
        source = next(source for source in entry["upload_sources"] if source["import_id"] == record["id"])
        if source.get("raw_file") != record["raw_file"]:
            raise ValueError("重复上传来源的原文件路径不一致")
    return category, copy.deepcopy(entry)


def field_facts(fields):
    keys = ("name", "value", "unit", "reference_range", "observed_at", "source_page", "original_text")
    return sorted((json.dumps({key: field.get(key) for key in keys}, sort_keys=True, ensure_ascii=False)
                   for field in fields))


def upload_source(record, fields, checked_at, checked_by=None):
    result = {"import_id": record["id"], "original_name": record["original_name"],
              "raw_file": record["raw_file"], "imported_at": record.get("imported_at"),
              "document_type": record["document_type"], "title": record.get("title"),
              "document_date": record.get("document_date"), "submitted_fields": copy.deepcopy(fields),
              "checked_at": checked_at, "checked_by": copy.deepcopy(checked_by or {}),
              "duplicate_conflicts": []}
    for key in SOURCE_METADATA_KEYS:
        if key in record:
            result[key] = copy.deepcopy(record[key])
    return result


def conflicts_with(entry, category, source):
    conflicts = []
    for key, value in (("document_type", category), ("title", entry.get("title")),
                       ("document_date", entry.get("document_date"))):
        if source.get(key) is not None and source.get(key) != value:
            conflicts.append({"field": key, "canonical": value, "submitted": source[key]})
    if field_facts(entry["fields"]) != field_facts(source["submitted_fields"]):
        conflicts.append({"field": "fields", "canonical": copy.deepcopy(entry["fields"]),
                          "submitted": copy.deepcopy(source["submitted_fields"])})
    return conflicts


def reconcile_published(record, published):
    category, entry = published
    record["status"] = "confirmed"
    record["canonical_document_id"] = entry["id"]
    source = next((s for s in entry.get("upload_sources", []) if s["import_id"] == record["id"]), None)
    if entry["id"] == record["id"]:
        record.update(confirmed_at=entry["confirmed_at"], fields=copy.deepcopy(entry["fields"]), document_type=category)
        record.pop("duplicate_of", None)
        record.pop("duplicate_conflicts", None)
        record.pop("canonical_fields", None)
    else:
        if source is None:
            raise ValueError("重复上传缺少已发布来源索引")
        record.update(duplicate_of=entry["id"], confirmed_at=source["checked_at"],
                      fields=copy.deepcopy(source["submitted_fields"]),
                      duplicate_conflicts=copy.deepcopy(source["duplicate_conflicts"]),
                      canonical_fields=copy.deepcopy(entry["fields"]))
    # Both primary and duplicate receipts are recovery copies of the committed
    # source. Never retain stale edits (or invented provenance) from a pending
    # JSON file after the canonical index has already been published.
    if source is not None:
        for key in SOURCE_METADATA_KEYS:
            if key in source:
                record[key] = copy.deepcopy(source[key])
            else:
                record.pop(key, None)
    else:
        # Legacy canonical documents did not snapshot all upload metadata.
        # Restore what the index actually knows, without inventing missing facts.
        for key in SOURCE_METADATA_KEYS:
            if key in entry:
                record[key] = copy.deepcopy(entry[key])
    return record


def inherit_document_visibility(profile, canonical_id, record):
    """Carry a pending group's explicit visibility onto its chosen document ID.

    A different upload may be confirmed first. The canonical ID must then retain
    the existing group's archive/restore choice, while an explicit canonical
    choice always wins. Call only under the member profile lock.
    """
    management = profile.get("file_management")
    if management is None:
        return
    if not isinstance(management, dict) or not isinstance(management.get("items", {}), dict):
        raise ValueError("文件归档状态格式无效")
    items = management.get("items", {})
    canonical_key = "document:" + canonical_id
    if canonical_key in items:
        return
    candidates = []
    for path in stored_path("imports").glob("*.json"):
        receipt = load_json(record_path(path.stem))
        if not isinstance(receipt, dict):
            raise ValueError("上传来源记录格式无效")
        if (receipt.get("sha256") != record["sha256"]
                or receipt.get("subject_member_id") != record.get("subject_member_id")):
            continue
        validate_record(receipt, path.stem)
        key = "document:" + receipt["id"]
        if key in items:
            candidates.append((receipt.get("imported_at") or "", receipt["id"], key))
    if not candidates:
        return
    _, _, alias = min(candidates)
    previous = items[alias]
    if not isinstance(previous, dict) or type(previous.get("archived")) is not bool:
        raise ValueError("待核对文件的归档状态无效")
    if not isinstance(management.get("history", []), list):
        raise ValueError("文件整理历史格式无效")
    value = copy.deepcopy(previous)
    value.update(inherited_from=alias, inherited_at=now_iso())
    items[canonical_key] = value
    history = {"id": "file_action_" + uuid.uuid4().hex, "action": "canonicalize_document_visibility",
               "basis": "同一原文件由另一份上传来源先确认，保留既有归档或恢复选择",
               "recorded_at": now_iso(), "before": {"item_id": alias, **copy.deepcopy(previous)},
               "after": {"item_id": canonical_key, **copy.deepcopy(value)}}
    history.update(record_context())
    management.setdefault("history", []).append(history)


def review(args):
    path = record_path(args.record_id)
    def revise(record):
        validate_record(record, args.record_id)
        if record["status"] != "pending":
            raise ValueError("仅可修订待核对记录")
        profile = load_json(stored_path("user-profile.json"), default={"records": {}})
        if published_entry(profile, record):
            raise ValueError("该记录已确认入档，但导入状态尚未同步；请先重新 confirm 恢复状态，不能继续 review 修改已发布字段")
        record["fields"] = validated_fields(load_json(Path(args.fields_json)), record["pages"])
        record["reviewed_at"] = now_iso()
        if record_context():
            record["reviewed_by"] = record_context()
    return update_json(path, revise)


def confirm(args):
    if not args.reviewed:
        raise ValueError("确认前需要用户核对原文、数值、单位与日期，并传入 --reviewed")
    path = record_path(args.record_id)
    def finish(record):
        validate_record(record, args.record_id)
        if record.get("extraction_status") != "ok":
            raise ValueError("存在提取失败或缺失页面，请重新提取并核对后再入档")
        validate_pages(record["pages"], record.get("page_count"), complete=True)
        raw = verify_raw(record)
        if has_model_vision(record):
            validate_model_pages(record["pages"], record.get("page_count"), record.get("extraction_status"),
                                 inspect_model_source(raw, record["sha256"])["page_count"], complete=True)
        profile_path = stored_path("user-profile.json")
        published = published_entry(load_json(profile_path, default={"records": {}}), record)
        if published:
            # Also repair visibility for an older partial publication without
            # changing the already-committed clinical fields or upload sources.
            def preserve_visibility(profile):
                current = published_entry(profile, record)
                if current is None:
                    raise ValueError("已发布文档索引在恢复期间丢失")
                inherit_document_visibility(profile, current[1]["id"], record)
            update_json(profile_path, preserve_visibility)
            return reconcile_published(record, published)
        if record["status"] == "confirmed":
            raise ValueError("已确认导入缺少档案索引，请先恢复索引")
        fields = validated_fields(record["fields"], record["pages"])
        confirmed_at = now_iso()
        for field in fields:
            field.update(confirmation_status="confirmed", confirmed_at=confirmed_at,
                         confirmed_by="user", source_record=record["id"])
            attribute_record(field, getattr(args, "role", None))
        entry = {key: record[key] for key in ("id", "source_type", "original_name", "raw_file", "sha256")}
        entry.update(fields=fields, confirmed_at=confirmed_at, role="self", status="confirmed")
        entry.update(document_metadata(record.get("title"), record.get("document_date")))
        entry["source_pages"] = copy.deepcopy(record["pages"])
        if has_model_vision(record):
            entry.update({key: copy.deepcopy(record[key]) for key in MODEL_METADATA_KEYS})
            entry.update(page_count=record["page_count"], extraction_status=record["extraction_status"])
        # The uploader and the person who checked a document can be different.
        for key in record_context():
            if key in record:
                entry[key] = record[key]
        if record_context():
            entry["confirmed_by"] = record_context()
        source = upload_source(record, fields, confirmed_at, record_context())
        entry["upload_sources"] = [copy.deepcopy(source)]
        committed = []
        def index(profile):
            records = indexed_records(profile)
            published = published_entry(profile, record)
            if published:
                committed.append(published)
                return
            matches = [(category, item) for category, entries in records.items() for item in entries
                       if item.get("sha256") == record["sha256"]]
            if len(matches) > 1:
                raise ValueError("同一原文件已有多个主档，请先核对旧索引")
            if matches:
                category, existing = matches[0]
                validate_published(category, existing)
                if record.get("subject_member_id") != existing.get("subject_member_id"):
                    raise ValueError("原文件主档与上传来源的成员归属不一致，不能自动关联")
                if "upload_sources" not in existing:
                    original = load_json(record_path(existing["id"]))
                    if not isinstance(original, dict):
                        raise ValueError("旧文档的原上传记录缺失，不能自动补造来源")
                    existing["upload_sources"] = [upload_source(original, existing["fields"],
                        existing["confirmed_at"], existing.get("confirmed_by"))]
                source["duplicate_conflicts"] = conflicts_with(existing, category, source)
                existing["upload_sources"].append(copy.deepcopy(source))
                existing.setdefault("source_pages", copy.deepcopy(canonical_pages(existing)))
                selected = category, existing
            else:
                records.setdefault(record["document_type"], []).append(entry)
                selected = record["document_type"], entry
            profile["records"] = records
            inherit_document_visibility(profile, selected[1]["id"], record)
            profile["lastUpdated"] = confirmed_at
            summary = profile.setdefault("summary", {})
            summary["totalTypes"] = len(records)
            summary["totalRecords"] = sum(len(v) for v in records.values()) + len(profile.get("consultations", []))
            summary["latestRecord"] = selected[1]["id"]
            summary["documentCount"] = sum(len(v) for v in records.values())
            committed.append((selected[0], copy.deepcopy(selected[1])))
        update_json(profile_path, index, default={"version": "3.0", "owner": {}, "records": {}, "consultations": []})
        return reconcile_published(record, committed[0])
    return update_json(path, finish)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("stage", "review", "confirm", "show"):
        p = sub.add_parser(name)
        p.add_argument("--role", required=True, choices=["self", "proxy", "本人", "代问诊"])
        if name == "stage":
            p.add_argument("--file", required=True)
            p.add_argument("--extraction-json", required=True)
            p.add_argument("--fields-json")
            p.add_argument("--title", help="文档标题，不从姓名或文件名猜测医学含义")
            p.add_argument("--document-date", help="报告/检查实际日期 YYYY-MM-DD；未知时省略")
            p.add_argument("--request-id", help="稳定上传来源键，同一附件重试沿用")
            p.add_argument("--document-type", default="检验结果", choices=["体检报告", "检验结果", "处方", "影像报告", "病历", "其他"])
        else:
            p.add_argument("record_id")
        if name == "review":
            p.add_argument("--fields-json", required=True)
        if name == "confirm":
            p.add_argument("--reviewed", action="store_true")
    args = parser.parse_args()
    try:
        ensure_self(args.role)
        if args.command == "show":
            result = validate_record(load_json(record_path(args.record_id)), args.record_id)
        else:
            result = globals()[args.command](args)
        if args.command != "show":
            refresh_file_views()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("操作失败：" + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
