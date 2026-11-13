"""Validated observation import and descriptive statistics, never a diagnostic model."""
import copy
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "skills" / "family-doctor" / "scripts"))
from family_context import family_home, read_registry, resolve_context, resolve_member, safe_path, validate_registry
from health_store import _lock, _replace_text

MAX_CONTENT_BYTES = 1024 * 1024
MAX_RECORDS = 1000
FIELDS = ("source_record_id", "member", "reporter_profile", "observed_at", "source_type", "quality", "signals", "confounders", "event_key")
SIGNALS = {"repeated_question": "重复提问", "disorientation": "时间地点混淆", "topic_discontinuity": "话题衔接变化",
           "word_finding": "找词困难", "daily_function_change": "日常能力变化"}
SOURCES = ("self_expression", "caregiver_observation", "clinician_record")
QUALITIES = ("usable", "limited", "excluded")
CONFOUNDERS = ("sleep", "mood", "medication", "hearing_vision", "transcription", "other", "unknown")
TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z")
SHANGHAI = ZoneInfo("Asia/Shanghai")


class ImportValidationError(ValueError):
    def __init__(self, preview):
        super().__init__("导入校验未通过，未保存任何记录")
        self.preview = preview


class RevisionConflict(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc)


def _json(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(data):
    return hashlib.sha256(_json(data).encode("utf-8")).hexdigest()


def _error(row, field, code, message):
    return {"row": row, "field": field, "code": code, "message": message}


def _reject_constant(value):
    raise ValueError("JSON 数值无效")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 字段重复")
        result[key] = value
    return result


def _instant(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("时间必须为带时区的 ISO 8601 时间")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("时间必须为带时区的 ISO 8601 时间") from exc
    if parsed.tzinfo is None or "T" not in value:
        raise ValueError("时间必须包含时分和时区，不能用日期或本机时区补齐")
    return parsed.astimezone(timezone.utc)


def _token(value, optional=False):
    if optional and value in (None, ""):
        return None
    if not isinstance(value, str) or not TOKEN.fullmatch(value) or value.startswith("REPLACE_"):
        raise ValueError("必须填写稳定来源编号，仅支持字母、数字及 . _ : -，不能保留模板占位值")
    return value


def parse_content(content, format="json"):
    if not isinstance(content, str) or not content.strip():
        raise ValueError("导入文件不能为空")
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise ValueError("导入文件不得超过 1 MiB")
    content = content.lstrip("\ufeff")
    if format == "json":
        data = json.loads(content, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
        if (not isinstance(data, dict) or set(data) != {"schema_version", "records"}
                or type(data["schema_version"]) is not int or data["schema_version"] != 1):
            raise ValueError("JSON 顶层只能包含 schema_version: 1 和 records 数组")
        records = data["records"]
    elif format == "csv":
        reader = csv.DictReader(io.StringIO(content), strict=True)
        headers = reader.fieldnames
        if (not headers or len(set(headers)) != len(headers) or set(headers) - set(FIELDS)
                or not set(FIELDS[:-1]) <= set(headers)):
            raise ValueError("CSV 表头缺失、重复或含不支持的字段，请使用模板")
        records = []
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("CSV 行列数量与表头不一致")
            for field in ("signals", "confounders"):
                row[field] = [part.strip() for part in row[field].split("|")] if row[field].strip() else []
            records.append(row)
            if len(records) > MAX_RECORDS:
                raise ValueError("单次导入不得超过 1000 条记录")
    else:
        raise ValueError("仅支持 json 或 csv")
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_RECORDS:
        raise ValueError("records 必须包含 1 至 1000 条记录")
    return records


def _normal_record(row, registry, now, strict_profile=None):
    if not isinstance(row, dict) or set(row) - set(FIELDS) or not set(FIELDS[:-1]) <= set(row):
        raise ValueError("字段缺失或含不支持的字段；不得上传聊天原文、摘要、分数或诊断")
    result = {}
    for field in ("source_record_id", "event_key"):
        try:
            result[field] = _token(row.get(field), optional=field == "event_key")
        except ValueError as exc:
            raise FieldError(field, str(exc)) from exc
    try:
        member_id = resolve_member(registry, row["member"])
    except (ValueError, TypeError) as exc:
        raise FieldError("member", "成员未登记或姓名重名；请核对姓名，重名时使用成员编号") from exc
    profile = row["reporter_profile"]
    if not isinstance(profile, str) or profile not in registry["profiles"]:
        raise FieldError("reporter_profile", "提供者 profile 未登记")
    if strict_profile is not None and profile != strict_profile:
        raise FieldError("reporter_profile", "Hermes 单来源导入只能提交当前 profile 的观察，历史批量导入请显式使用 --batch")
    reporter_id = registry["profiles"][profile]
    if row["source_type"] not in SOURCES:
        raise FieldError("source_type", "观察来源类型无效")
    if row["source_type"] == "self_expression" and member_id != reporter_id:
        raise FieldError("source_type", "本人表达必须由该成员本人 profile 提供")
    if row["source_type"] == "caregiver_observation" and member_id == reporter_id:
        raise FieldError("source_type", "家属观察的提供者必须与被观察成员不同")
    if row["quality"] not in QUALITIES:
        raise FieldError("quality", "资料质量标记无效")
    try:
        observed = _instant(row["observed_at"])
    except (ValueError, OverflowError) as exc:
        raise FieldError("observed_at", "实际观察时间必须包含时分和时区") from exc
    if observed > now:
        raise FieldError("observed_at", "观察时间不能晚于当前时间")
    for field, allowed in (("signals", SIGNALS), ("confounders", CONFOUNDERS)):
        values = row[field]
        if (not isinstance(values, list) or any(not isinstance(v, str) or v not in allowed for v in values)
                or len(set(values)) != len(values)):
            raise FieldError(field, "只接受模板约定的枚举数组，不能重复或填写自由文本")
        result[field] = sorted(values)
    result.update(member_id=member_id, reporter_profile=profile, reporter_member_id=reporter_id,
                  observed_at=observed.isoformat(), source_type=row["source_type"], quality=row["quality"])
    return result


class FieldError(ValueError):
    def __init__(self, field, message):
        super().__init__(message)
        self.field = field


def _key(record):
    return (record["member_id"], record["reporter_profile"], record["source_record_id"])


def _event(record):
    return (record["member_id"], "event", record["event_key"]) if record["event_key"] else (record["member_id"], "source", *_key(record)[1:])


def _empty_store():
    return {"schema_version": 1, "revision": 0, "records": [], "imports": []}


def template_json():
    return {"schema_version": 1, "records": [{"source_record_id": "REPLACE_SOURCE_ID", "member": "填写已登记姓名",
        "reporter_profile": "填写已登记profile", "observed_at": "YYYY-MM-DDTHH:MM:SS+08:00",
        "source_type": "self_expression", "quality": "usable", "signals": [], "confounders": [], "event_key": ""}]}


def template_csv():
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    row = template_json()["records"][0]
    row.update(signals="", confounders="")
    writer.writerow(row)
    return output.getvalue()


class CognitionStore:
    def __init__(self, home=None, profile=None):
        self.home = family_home(home)
        self.profile = resolve_context(self.home, profile)["profile"]
        self.memory_registry = None

    @classmethod
    def demo(cls, registry, profile):
        validate_registry(registry)
        if profile not in registry["profiles"]:
            raise ValueError("演示提供者未登记")
        obj = cls.__new__(cls)
        obj.home, obj.profile = None, profile
        obj.memory_registry = copy.deepcopy(registry)
        return obj

    def template(self, format="json"):
        if format == "json":
            return template_json()
        if format == "csv":
            return template_csv()
        raise ValueError("仅支持 json 或 csv")

    def _registry(self):
        registry = self.memory_registry if self.memory_registry is not None else read_registry(self.home)
        if self.profile not in registry["profiles"]:
            raise ValueError("当前导入者 profile 已不在家庭登记中")
        return registry

    def _path(self):
        return safe_path(self.home, "cognition", "observations.json")

    def _read(self, registry, now):
        if self.memory_registry is not None:
            return _empty_store()
        try:
            path = self._path()
            data = (json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant,
                               object_pairs_hook=_unique_object) if path.exists() else _empty_store())
            if (not isinstance(data, dict) or set(data) != {"schema_version", "revision", "records", "imports"}
                    or type(data["schema_version"]) is not int or data["schema_version"] != 1
                    or type(data["revision"]) is not int or data["revision"] < 0
                    or not isinstance(data["records"], list) or not isinstance(data["imports"], list)):
                raise ValueError("Invalid cognition store")
            keys, event_times = set(), {}
            for record in data["records"]:
                if not isinstance(record, dict) or set(record) != {"source_record_id", "event_key", "signals", "confounders", "member_id", "reporter_profile", "reporter_member_id", "observed_at", "source_type", "quality"}:
                    raise ValueError("Invalid cognition record")
                converted = {k: record[k] for k in FIELDS if k != "member"}
                converted["member"] = record["member_id"]
                normalized = _normal_record(converted, registry, now)
                if normalized != record or _key(record) in keys:
                    raise ValueError("Invalid cognition provenance")
                keys.add(_key(record))
                event = _event(record)
                if event in event_times and event_times[event] != record["observed_at"]:
                    raise ValueError("Invalid cognition event")
                event_times[event] = record["observed_at"]
            audit_fields = {"id", "imported_at", "ingested_by_profile", "ingested_by_member_id", "new", "duplicates", "digest"}
            for batch in data["imports"]:
                if (not isinstance(batch, dict) or set(batch) != audit_fields
                        or not isinstance(batch["id"], str) or not re.fullmatch("import_[0-9a-f]{32}", batch["id"])
                        or type(batch["new"]) is not int or batch["new"] <= 0
                        or type(batch["duplicates"]) is not int or batch["duplicates"] < 0
                        or not isinstance(batch["digest"], str) or not re.fullmatch("[0-9a-f]{64}", batch["digest"])
                        or batch["ingested_by_profile"] not in registry["profiles"]
                        or registry["profiles"][batch["ingested_by_profile"]] != batch["ingested_by_member_id"]):
                    raise ValueError("Invalid import audit")
                _instant(batch["imported_at"])
            if (data["revision"] != len(data["imports"])
                    or len({batch["id"] for batch in data["imports"]}) != len(data["imports"])
                    or sum(batch["new"] for batch in data["imports"]) != len(data["records"])):
                raise ValueError("Invalid import revision")
            return data
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ValueError("认知观察资料损坏或成员关联已变化，请核对原资料；未重置数据") from exc

    def _preview(self, content, format, registry, stored, now, strict_reporter):
        result = {"ok": False, "revision": stored["revision"], "digest": None, "total": 0, "new": 0,
                  "duplicates": 0, "errors": [], "warnings": [], "members": []}
        try:
            inputs = parse_content(content, format)
        except (ValueError, TypeError, csv.Error, UnicodeError) as exc:
            # JSON parser exception text can include payload fragments; use a fixed message.
            result["errors"].append(_error(0, "file", "invalid_file", "文件格式、大小或字段不符合导入模板，请检查 JSON/CSV；没有保存资料"))
            return result, []
        result["total"] = len(inputs)
        normalized, new_records = [], []
        by_key = {_key(r): r for r in stored["records"]}
        event_times = {_event(r): r["observed_at"] for r in stored["records"]}
        members = {}
        for position, row in enumerate(inputs, 1):
            try:
                record = _normal_record(row, registry, now, self.profile if strict_reporter else None)
                key, event = _key(record), _event(record)
                if key in by_key and by_key[key] != record:
                    raise FieldError("source_record_id", "同一来源编号已有不同内容，不能覆盖；整批未保存")
                if event in event_times and event_times[event] != record["observed_at"]:
                    raise FieldError("event_key", "同一事件编号的实际观察时间不一致；请核对是否同次观察，不要合并不同时间的行为")
                member_id = record["member_id"]
                count = members.setdefault(member_id, {"id": member_id, "name": registry["members"][member_id]["name"], "new": 0, "duplicates": 0})
                if key in by_key:
                    result["duplicates"] += 1
                    count["duplicates"] += 1
                else:
                    by_key[key] = record
                    event_times[event] = record["observed_at"]
                    new_records.append(record)
                    result["new"] += 1
                    count["new"] += 1
                normalized.append(record)
            except (ValueError, TypeError) as exc:
                field = getattr(exc, "field", "record")
                message = str(exc) if isinstance(exc, (FieldError,)) else "记录字段不符合模板，不接受原文、自由文本或多余字段"
                result["errors"].append(_error(position, field, "invalid_record", message))
        result["members"] = list(members.values())
        result["ok"] = not result["errors"]
        result["digest"] = _hash({"records": normalized, "registry": registry, "ingested_by": self.profile})
        if any(r["quality"] != "usable" for r in normalized):
            result["warnings"].append("质量有限或排除的记录会保留，但不计入有效观察和变化趋势。")
        if any(r["confounders"] for r in normalized):
            result["warnings"].append("部分记录包含影响表达的背景因素；变化需要核实，不能据此诊断痴呆。")
        if result["duplicates"]:
            result["warnings"].append("重复来源会跳过，保留已有记录，不增加统计次数。")
        return result, new_records

    def preview(self, content, format="json", strict_reporter=False):
        registry, now = self._registry(), _now()
        result, _ = self._preview(content, format, registry, self._read(registry, now), now, strict_reporter)
        return result

    def commit(self, content, format="json", expected_revision=None, strict_reporter=False, expected_digest=None):
        if self.memory_registry is not None:
            raise ValueError("演示模式不保存资料")
        if type(expected_revision) is not int or expected_revision < 0:
            raise RevisionConflict("请先预览导入内容")
        # The sole mutation is one file replacement while holding this lock.
        with _lock(self._path()):
            registry, now = self._registry(), _now()
            stored = self._read(registry, now)
            if expected_revision != stored["revision"]:
                raise RevisionConflict("预览后资料已更新，请重新预览")
            preview, new_records = self._preview(content, format, registry, stored, now, strict_reporter)
            if expected_digest is not None and expected_digest != preview["digest"]:
                raise RevisionConflict("预览后家庭登记或导入内容已变化，请重新预览")
            if not preview["ok"]:
                raise ImportValidationError(preview)
            if not new_records:
                return dict(preview, batchId=None, importedAt=None)
            batch_id, imported_at = "import_" + uuid.uuid4().hex, now.isoformat()
            stored["records"].extend(new_records)
            stored["revision"] += 1
            stored["imports"].append({"id": batch_id, "imported_at": imported_at, "ingested_by_profile": self.profile,
                "ingested_by_member_id": registry["profiles"][self.profile], "new": preview["new"],
                "duplicates": preview["duplicates"], "digest": preview["digest"]})
            _replace_text(self._path(), json.dumps(stored, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
            return dict(preview, batchId=batch_id, revision=stored["revision"], importedAt=imported_at)

    def snapshot(self, days=30):
        if type(days) is not int or not 1 <= days <= 366:
            raise ValueError("统计窗口必须是 1 至 366 天")
        registry, now = self._registry(), _now()
        stored = self._read(registry, now)
        today = now.astimezone(SHANGHAI).date()
        first = today - timedelta(days=days - 1)
        dates = [(first + timedelta(days=n)).isoformat() for n in range(days)]
        result = {"meta": {"familyName": registry["name"], "generatedAt": now.isoformat(), "windowDays": days,
                           "windowStart": dates[0], "windowEnd": dates[-1], "modelEnabled": False},
                  "members": [], "revision": stored["revision"],
                  "imports": [{"id": r["id"], "importedAt": r["imported_at"], "new": r["new"], "duplicates": r["duplicates"]}
                              for r in reversed(stored["imports"][-20:])]}
        for member_id, member in registry["members"].items():
            records = [r for r in stored["records"] if r["member_id"] == member_id
                       and first <= _instant(r["observed_at"]).astimezone(SHANGHAI).date() <= today]
            usable = [r for r in records if r["quality"] == "usable"]
            events = {}
            for record in usable:
                events.setdefault(_event(record), []).append(record)
            trend = {day: {"date": day, "observations": 0, "signals": 0} for day in dates}
            domains = {key: 0 for key in SIGNALS}
            language_samples = 0
            for sources in events.values():
                day = _instant(sources[0]["observed_at"]).astimezone(SHANGHAI).date().isoformat()
                signals = set(value for source in sources for value in source["signals"])
                trend[day]["observations"] += 1
                trend[day]["signals"] += int(bool(signals))
                language_samples += int(any(s["source_type"] == "self_expression" for s in sources))
                for signal in signals:
                    domains[signal] += 1
            signal_count = sum(t["signals"] for t in trend.values())
            status = "needs_review" if signal_count else "recorded" if usable else "not_assessed"
            result["members"].append({"id": member_id, "name": member["name"], "status": status,
                "label": {"needs_review": "待核实变化", "recorded": "已记录观察", "not_assessed": "尚未评估"}[status],
                "validSamples": len(usable), "eventCount": len(events), "signalCount": signal_count,
                "languageSamples": language_samples, "qualityCounts": {q: sum(r["quality"] == q for r in records) for q in QUALITIES},
                "sourceCounts": {s: sum(r["source_type"] == s for r in usable) for s in SOURCES},
                "lastObservedAt": max((r["observed_at"] for r in records), default=None), "trend": list(trend.values()),
                "domains": [{"key": key, "label": label, "count": domains[key]} for key, label in SIGNALS.items()]})
        result["totals"] = {"members": len(result["members"]),
            "needsReview": sum(m["status"] == "needs_review" for m in result["members"]),
            "notAssessed": sum(m["status"] == "not_assessed" for m in result["members"]),
            "validSamples": sum(m["validSamples"] for m in result["members"]),
            "eventCount": sum(m["eventCount"] for m in result["members"]),
            "signalCount": sum(m["signalCount"] for m in result["members"])}
        return result
