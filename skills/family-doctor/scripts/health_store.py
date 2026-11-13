"""Shared, local-only storage for the owner's health records (Python 3.8+).

FAMILY_DOCTOR_HOME overrides the workspace root. New directories/files use
0700/0600; existing directories are not recursively chmod'ed. Every replacement
keeps the previous file in a private .backups directory. No corrupt JSON is reset.
"""
import copy
import json
import math
import os
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

WORKSPACE = Path(os.environ.get("FAMILY_DOCTOR_HOME", str(Path(__file__).resolve().parents[3]))).expanduser().resolve()
HEALTH_DIR = WORKSPACE / "health-records"
USER_PROFILE = HEALTH_DIR / "user-profile.json"
MEDICATIONS_FILE = HEALTH_DIR / "medications.json"
VITALS_FILE = HEALTH_DIR / "vitals.json"
SYMPTOMS_FILE = HEALTH_DIR / "symptoms.json"
CONSULTATIONS_DIR = HEALTH_DIR / "consultations"
REPORTS_DIR = HEALTH_DIR / "reports"


def ensure_self(role):
    """Validate the selected subject before reads/writes (legacy name retained).

    Household mode routes through family.py. This is an attribution check, not
    an authorization system: every configured family profile is locally trusted.
    """
    if os.environ.get("FAMILY_DOCTOR_FAMILY_HOME") or os.environ.get("FAMILY_DOCTOR_MEMBER_ID"):
        from family_context import current_context, validate_role
        context = current_context(workspace=WORKSPACE)
        validate_role(role, context)
        if context and USER_PROFILE.exists():
            profile = load_json(USER_PROFILE)
            if (not isinstance(profile, dict) or not isinstance(profile.get("owner"), dict)
                    or profile["owner"].get("member_id") != context["subject_member_id"]):
                raise ValueError("档案成员编号与家庭路由不一致，不能自动覆盖或混入其他成员资料")
        return
    if str(role).strip().casefold() not in {"self", "本人"}:
        raise PermissionError("代问诊禁止读取或写入本人健康档案；仅本人（self / 本人）可访问。")


def record_context(role=None):
    """Keep the speaker and the subject separate in every household record."""
    if not os.environ.get("FAMILY_DOCTOR_FAMILY_HOME"):
        return {}
    from family_context import current_context, provenance, validate_role
    context = current_context(workspace=WORKSPACE)
    if role is not None:
        validate_role(role, context)
    return provenance(role)


def attribute_record(record, role=None):
    """Attach source attribution without changing the clinical content."""
    context = record_context(role)
    record.update(context)
    source = record.get("source")
    if context.get("report_mode") == "proxy" and isinstance(source, dict) and source.get("type") == "user_report":
        source["type"] = "caregiver_report"
        if source.get("reference") == "用户自述/手动录入":
            source["reference"] = context["reporter_name"] + "代述/手动录入"
    if context and record.get("confirmed_by") == "user":
        record["confirmed_by_member_id"] = context["reporter_member_id"]
    if context.get("report_mode") == "proxy" and record.get("confirmation_status") == "user_confirmed":
        record["confirmation_status"] = "reporter_confirmed"
    return record


def refresh_file_views(include_episodes=True):
    """Rebuild views after a committed mutation, never while holding its lock.

    A display failure cannot roll back health records or make a successful
    submission look like it needs to be submitted again. Warnings use stderr so
    structured tool results stay machine-readable.
    """
    warnings = []
    if include_episodes and USER_PROFILE.exists():
        try:
            from add_consultation import rebuild_artifacts
            rebuild_artifacts()
        except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
            warnings.append("事件展示文件：" + str(exc))
    try:
        from file_catalog import rebuild_catalog
        rebuild_catalog()
    except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
        warnings.append("文件目录：" + str(exc))
    if warnings:
        print("资料已保存，部分展示尚未更新；可用 file_manager rebuild 恢复。" + "；".join(warnings), file=sys.stderr)
    return warnings


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def parse_time(value):
    """Parse ISO time; legacy naive values use the local timezone explicitly."""
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("时间必须为 ISO 8601 格式，例如 2026-11-11T08:00:00+08:00") from exc
    return result.astimezone() if result.tzinfo is None else result


def _private_dir(path):
    path = Path(path)
    if not path.exists():
        _private_dir(path.parent)
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
    if not path.is_dir():
        raise ValueError("存储目录无效: {}".format(path))


def _check_path(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("拒绝通过符号链接写入健康档案: {}".format(path))
    return path


def load_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return copy.deepcopy(default)
    try:
        with path.open("r", encoding="utf-8") as handle:
            def reject_constant(value):
                raise ValueError("非标准 JSON 数值: {}".format(value))
            return json.load(handle, parse_constant=reject_constant)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("档案 JSON 损坏，已停止操作，原文件未改动: {}".format(path)) from exc


@contextmanager
def _lock(path):
    path = _check_path(path)
    _private_dir(path.parent)
    lock_path = path.with_name(path.name + ".lock")
    _check_path(lock_path)
    descriptor = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.chmod(lock_path, 0o600)
        if os.name == "nt":
            import msvcrt
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _replace_text(path, text):
    path = _check_path(path)
    _private_dir(path.parent)
    # A fresh backup for every changed file; never rotate away health history.
    if path.exists():
        backup_dir = path.parent / ".backups"
        _private_dir(backup_dir)
        os.chmod(backup_dir, 0o700)
        backup = backup_dir / (path.name + "." + uuid.uuid4().hex + ".bak")
        descriptor = os.open(str(backup), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(path.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        if os.name != "nt":
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_text(path, content):
    with _lock(path):
        _replace_text(path, content)


def atomic_write_json(path, data):
    text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with _lock(path):
        # Refuse overwriting malformed existing data even when replacing it all.
        load_json(path)
        _replace_text(path, text)
    return data


def update_json(path, updater, default=None):
    with _lock(path):
        current = load_json(path, default)
        replacement = updater(current)
        if replacement is None:
            replacement = current
        text = json.dumps(replacement, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        _replace_text(path, text)
        return replacement


def normalize_medications(data):
    data = copy.deepcopy(data)
    if not isinstance(data, dict) or not isinstance(data.get("medications"), list):
        raise ValueError("药物档案结构无效，medications 必须是列表")
    meds = data.setdefault("medications", [])
    for med in meds:
        if not isinstance(med, dict) or not isinstance(med.get("name"), str) or not med["name"].strip():
            raise ValueError("药物记录必须包含有效 name")
        med["dose"] = med.get("dose", med.get("dosage", ""))
        med.setdefault("frequency", "")
        status = med.get("status")
        if status is None:
            if "ongoing" in med and not isinstance(med["ongoing"], bool):
                raise ValueError("旧版 ongoing 状态必须为布尔值")
            status = "active" if med.get("ongoing", True) else "stopped"
        if not isinstance(status, str):
            raise ValueError("药物状态必须为字符串")
        status = {"停用": "stopped", "在用": "active", "服用中": "active"}.get(status, status)
        if status not in {"active", "stopped"}:
            raise ValueError("未知药物状态: {}".format(status))
        med["status"] = status
        med.pop("dosage", None)
        med.pop("ongoing", None)
    data["version"] = "3.0"
    return data


VITAL_TYPES = {"bp": "blood_pressure", "glucose": "blood_sugar"}
DEFAULT_UNITS = {"blood_pressure": "mmHg", "blood_sugar": "mmol/L", "weight": "kg", "temperature": "°C", "heart_rate": "bpm", "spo2": "%"}
ALLOWED_UNITS = {"blood_pressure": {"mmHg"}, "blood_sugar": {"mmol/L", "mg/dL"}, "weight": {"kg", "lb"}, "temperature": {"°C", "°F"}, "heart_rate": {"bpm"}, "spo2": {"%"}}


def normalize_vitals(data):
    data = copy.deepcopy(data)
    if not isinstance(data, dict) or "vitals" not in data:
        raise ValueError("体征档案必须为对象")
    values = data.get("vitals", [])
    if isinstance(values, dict):
        converted = []
        for kind, records in values.items():
            if not isinstance(records, list):
                raise ValueError("旧版体征分类必须为列表")
            for record in records:
                if not isinstance(record, dict):
                    raise ValueError("体征记录必须为对象")
                converted.append(dict(record, type=VITAL_TYPES.get(kind, kind)))
        values = converted
    if not isinstance(values, list):
        raise ValueError("体征 vitals 必须为列表")
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("体征记录必须为对象")
        value["type"] = VITAL_TYPES.get(value.get("type"), value.get("type"))
        value.setdefault("recorded_at", value.get("timestamp"))
        value.setdefault("measured_at", value.get("timestamp") or value.get("recorded_at"))
        # An absent legacy unit remains unknown; never infer mmol/L vs mg/dL.
        value.setdefault("unit", None)
        if value.get("unit") == "lbs":
            value["unit"] = "lb"
        if "fasting" not in value and value.get("context"):
            value["fasting"] = value["context"] == "fasting"
        value.pop("timestamp", None)
    data["vitals"] = values
    data["version"] = "3.0"
    return data


def finite_number(value, name, minimum=0, inclusive=False):
    if isinstance(value, bool):
        raise ValueError("{} 必须为数值".format(name))
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError("{} 必须为数值".format(name)) from exc
    if not math.isfinite(result) or (result < minimum if inclusive else result <= minimum):
        raise ValueError("{} 必须为有限数值，且 {} {}".format(name, ">=" if inclusive else ">", minimum))
    return result
