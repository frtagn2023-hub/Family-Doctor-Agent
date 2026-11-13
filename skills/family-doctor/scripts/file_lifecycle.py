"""Reversible file organization and selected-event export for one member.

Archiving changes file-list visibility only. It never retracts clinical facts,
deletes source bytes, or removes evidence from medical reports. Document links
are anchored to original consultation IDs so later merge/split remains traceable.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid
import zipfile

from health_store import (HEALTH_DIR, USER_PROFILE, _lock, ensure_self, load_json,
                          now_iso, record_context, update_json)

MAX_ORIGINAL_BYTES = 50 * 1024 * 1024


def _reason(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise ValueError("请提供不超过 4000 字符的整理依据")
    return value.strip()


def _bare(value, kind):
    if not isinstance(value, str):
        raise ValueError("资料编号必须是文本")
    prefix = kind + ":"
    result = value[len(prefix):] if value.startswith(prefix) else value
    pattern = r"episode_[0-9a-f]{32}" if kind == "episode" else r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
    if not re.fullmatch(pattern, result):
        raise ValueError("无效的" + ("事件" if kind == "episode" else "文档") + "编号")
    return result


def _profile(profile):
    if not isinstance(profile, dict) or not isinstance(profile.get("owner"), dict):
        raise ValueError("成员档案不存在或结构无效")
    return profile


def validate_file_management(profile):
    """Validate organization metadata without modifying or repairing it."""
    _profile(profile)
    data = profile.get("file_management", {"items": {}, "links": [], "history": []})
    if (not isinstance(data, dict) or not isinstance(data.get("items", {}), dict)
            or not isinstance(data.get("links", []), list) or not isinstance(data.get("history", []), list)):
        raise ValueError("文件管理索引结构无效")
    for key, value in data.get("items", {}).items():
        if not isinstance(key, str) or ":" not in key or not isinstance(value, dict) or type(value.get("archived")) is not bool:
            raise ValueError("文件归档状态无效")
    ids = set()
    for link in data.get("links", []):
        if (not isinstance(link, dict) or not isinstance(link.get("id"), str)
                or link["id"] in ids or type(link.get("active")) is not bool):
            raise ValueError("文档关联记录无效或重复")
        ids.add(link["id"])
        _bare(link.get("document_id"), "document")
        refs = link.get("report_ids")
        if (not isinstance(refs, list) or not refs or len(set(refs)) != len(refs)
                or any(not isinstance(ref, str) or not re.fullmatch(r"cons_[0-9a-f]{32}", ref) for ref in refs)):
            raise ValueError("文档关联缺少有效的问诊来源编号")
        _reason(link.get("basis"))
        if link["active"]:
            _document(profile, link["document_id"])
            sources = profile.get("consultations", [])
            for ref in refs:
                matches = [r for r in sources if isinstance(r, dict) and r.get("id") == ref]
                if len(matches) != 1:
                    raise ValueError("活动文档关联引用了不存在或重复的问诊来源")
                _subject(profile, matches[0])
    return data


def _management(profile):
    validate_file_management(profile)
    value = profile.setdefault("file_management", {})
    value.setdefault("items", {})
    value.setdefault("links", [])
    value.setdefault("history", [])
    return value


def is_archived(profile, item_id):
    return validate_file_management(profile).get("items", {}).get(item_id, {}).get("archived", False)


def sync_episode_file_states(profile, before_episodes, operation, context=None):
    """Keep visibility consistent when event relationships change under a lock.

    Pure profile mutation: no filesystem or routing reads. The caller may supply
    already-resolved attribution in context. A merge is archived only if every
    contributing event was archived; splits inherit their original visibility;
    a fresh source reactivates an archived event so follow-up is visible.
    """
    if operation not in {"add", "merge", "split"}:
        raise ValueError("未知的事件文件整理操作")
    if isinstance(before_episodes, dict):
        before_episodes = list(before_episodes.values())
    if not isinstance(before_episodes, list):
        raise ValueError("整理前事件快照无效")
    data = _management(profile)
    previous_states = copy.deepcopy(data["items"])
    before = []
    for episode in before_episodes:
        if not isinstance(episode, dict) or not isinstance(episode.get("report_ids"), list):
            raise ValueError("整理前事件结构无效")
        _bare(episode.get("id"), "episode")
        before.append((episode, set(episode["report_ids"])))
    current = profile.get("consultation_episodes", [])
    if not isinstance(current, list):
        raise ValueError("当前事件索引无效")
    changes = []
    for episode in current:
        if not isinstance(episode, dict) or not isinstance(episode.get("report_ids"), list):
            raise ValueError("当前事件结构无效")
        _bare(episode.get("id"), "episode")
        refs = set(episode["report_ids"])
        contributors = [(old, old_refs) for old, old_refs in before
                        if refs.intersection(old_refs) or old["id"] == episode["id"]]
        if not contributors:
            continue
        inherited_archived = all(previous_states.get("episode:" + old["id"], {}).get("archived", False)
                                 for old, _ in contributors)
        old_refs = set().union(*(source_refs for _, source_refs in contributors))
        new_refs = sorted(refs - old_refs)
        archived = inherited_archived and not (operation == "add" and new_refs)
        key = "episode:" + episode["id"]
        previous = data["items"].get(key)
        if previous is None and not archived:
            continue
        if previous is not None and previous.get("archived") == archived:
            continue
        reason = ("新增问诊来源，自动恢复到活动文件列表" if operation == "add" and new_refs else
                  "合并后按所有来源事件的可见性整理" if operation == "merge" else
                  "拆分后继承原事件的可见性")
        timestamp = now_iso()
        state = {"archived": archived, "visibility_only": True, "updated_at": timestamp,
                 "reason": reason, **(context or {})}
        state["archived_at" if archived else "restored_at"] = timestamp
        data["items"][key] = state
        action = {"id": "file_action_" + uuid.uuid4().hex,
                  "action": "sync_episode_visibility", "operation": operation,
                  "item_id": key, "recorded_at": timestamp, "basis": reason,
                  "contributing_episode_ids": [old["id"] for old, _ in contributors],
                  "new_report_ids": new_refs, "before": copy.deepcopy(previous),
                  "after": copy.deepcopy(state), **(context or {})}
        data["history"].append(action)
        changes.append(action)
    return changes


def _subject(profile, record):
    member = profile["owner"].get("member_id")
    if record.get("subject_member_id") != member:
        raise ValueError("资料与所选成员不一致或归属未明确，不能自动关联")


def _document(profile, document_id):
    document_id = _bare(document_id, "document")
    records = _profile(profile).get("records", {})
    if not isinstance(records, dict):
        raise ValueError("文档索引结构无效")
    matches = []
    for entries in records.values():
        if not isinstance(entries, list):
            raise ValueError("文档分类结构无效")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("文档记录结构无效")
            aliases = {entry.get("id")}
            for upload in entry.get("upload_sources", []):
                if isinstance(upload, dict):
                    aliases.add(upload.get("import_id"))
            if document_id in aliases:
                matches.append(entry)
    if len(matches) != 1 or matches[0].get("status") != "confirmed":
        raise ValueError("未找到唯一的已核对文档，请先完成资料核对")
    _subject(profile, matches[0])
    return matches[0]


def _episode(profile, episode_id):
    episode_id = _bare(episode_id, "episode")
    episodes = _profile(profile).get("consultation_episodes", [])
    if not isinstance(episodes, list):
        raise ValueError("事件索引无效")
    matches = [entry for entry in episodes if isinstance(entry, dict) and entry.get("id") == episode_id]
    if len(matches) != 1:
        raise ValueError("未找到唯一的当前事件，可能已经重新合并或拆分")
    episode = matches[0]
    _subject(profile, episode)
    refs = episode.get("report_ids")
    if not isinstance(refs, list) or not refs or len(set(refs)) != len(refs):
        raise ValueError("事件来源引用无效")
    records = profile.get("consultations", [])
    for ref in refs:
        sources = [r for r in records if isinstance(r, dict) and r.get("id") == ref]
        if len(sources) != 1:
            raise ValueError("事件引用的问诊来源不存在或重复")
        _subject(profile, sources[0])
    return episode


def _history(profile, action, before, after, reason, role):
    entry = {"id": "file_action_" + uuid.uuid4().hex, "action": action,
             "basis": reason, "recorded_at": now_iso(),
             "before": copy.deepcopy(before), "after": copy.deepcopy(after)}
    entry.update(record_context(role))
    _management(profile)["history"].append(entry)


def _archive(item_id, reason, archived, role):
    ensure_self(role)
    reason = _reason(reason)
    outcome = {}
    def mutate(profile):
        # Import lazily: catalog reads link information from this module.
        from file_catalog import get_entry
        entry = get_entry(item_id, profile=profile)
        canonical = entry["id"]
        data = _management(profile)
        previous = data["items"].get(canonical)
        if previous is not None and previous.get("archived") == archived:
            outcome.update(item_id=canonical, **previous)
            return
        value = {"archived": archived, "updated_at": now_iso(), "reason": reason,
                 "visibility_only": True, **record_context(role)}
        value["archived_at" if archived else "restored_at"] = value["updated_at"]
        data["items"][canonical] = value
        _history(profile, "archive" if archived else "restore", previous,
                 {"item_id": canonical, **value}, reason, role)
        outcome.update(item_id=canonical, **value)
    update_json(USER_PROFILE, mutate)
    return outcome


def archive_item(item_id, reason, role="self"):
    """Hide an item from the default file list; all clinical data remains active."""
    return _archive(item_id, reason, True, role)


def restore_item(item_id, reason, role="self"):
    return _archive(item_id, reason, False, role)


def link_document(document_id, episode_id, basis, role="self"):
    ensure_self(role)
    basis = _reason(basis)
    outcome = {}
    def mutate(profile):
        document, episode = _document(profile, document_id), _episode(profile, episode_id)
        data = _management(profile)
        covered = {ref for link in data["links"] if link["active"] and link["document_id"] == document["id"]
                   for ref in link["report_ids"]}
        missing = [ref for ref in episode["report_ids"] if ref not in covered]
        if not missing:
            outcome.update(document_id=document["id"], episode_id=episode["id"], already_linked=True)
            return
        link = {"id": "link_" + uuid.uuid4().hex, "document_id": document["id"],
                "report_ids": missing, "episode_id_at_link": episode["id"], "basis": basis,
                "active": True, "created_at": now_iso(), **record_context(role)}
        data["links"].append(link)
        _history(profile, "link", None, link, basis, role)
        outcome.update(copy.deepcopy(link), episode_id=episode["id"], already_linked=False)
    update_json(USER_PROFILE, mutate)
    return outcome


def unlink_document(document_id, episode_id, basis, role="self"):
    ensure_self(role)
    basis = _reason(basis)
    outcome = {}
    def mutate(profile):
        document, episode = _document(profile, document_id), _episode(profile, episode_id)
        refs = set(episode["report_ids"])
        changed = []
        for link in _management(profile)["links"]:
            if not link["active"] or link["document_id"] != document["id"] or not refs.intersection(link["report_ids"]):
                continue
            before = copy.deepcopy(link)
            remaining = [ref for ref in link["report_ids"] if ref not in refs]
            if remaining:
                link["report_ids"] = remaining
            else:
                link["active"] = False  # Retain original refs as historical evidence.
            link.update(updated_at=now_iso(), unlink_basis=basis)
            _history(profile, "unlink", before, link, basis, role)
            changed.append(link["id"])
        outcome.update(document_id=document["id"], episode_id=episode["id"], unlinked=len(changed))
    update_json(USER_PROFILE, mutate)
    return outcome


def documents_for_episode(profile, episode):
    """Return copies of linked confirmed documents, including archived evidence.

    Each result retains the canonical document fields and adds file_item_id,
    archived, link_basis, link_bases and linked_report_ids. Links follow source
    IDs through merges/splits, never an approximate date or retired event ID.
    """
    _profile(profile)
    episode = _episode(profile, episode["id"] if isinstance(episode, dict) else episode)
    refs = set(episode["report_ids"])
    grouped = {}
    for link in validate_file_management(profile).get("links", []):
        overlap = refs.intersection(link["report_ids"])
        if not link["active"] or not overlap:
            continue
        document = _document(profile, link["document_id"])
        result = grouped.setdefault(document["id"], dict(copy.deepcopy(document),
            file_item_id="document:" + document["id"], link_bases=[], linked_report_ids=[]))
        result["linked_report_ids"] = sorted(set(result["linked_report_ids"]) | overlap)
        if link["basis"] not in result["link_bases"]:
            result["link_bases"].append(link["basis"])
    for result in grouped.values():
        aliases = [result["id"]] + [u.get("import_id") for u in result.get("upload_sources", []) if isinstance(u, dict)]
        states = validate_file_management(profile).get("items", {})
        state = next((states["document:" + key] for key in aliases if key and "document:" + key in states), {})
        result["archived"] = state.get("archived", False)
        result["link_basis"] = "；".join(result["link_bases"])
    return list(grouped.values())


def _stored_file(relative, prefix):
    if not isinstance(relative, str) or "\\" in relative:
        raise ValueError("资料文件路径无效")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != prefix:
        raise ValueError("资料文件必须位于指定的成员目录内")
    current = HEALTH_DIR
    for part in (None,) + path.parts:
        current = current if part is None else current / part
        if current.is_symlink():
            raise ValueError("拒绝通过符号链接读取导出资料")
    if not current.is_file():
        raise ValueError("导出所需原文件缺失，请先恢复：" + relative)
    return current


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _original_bytes(document):
    source = _stored_file(document.get("raw_file"), "raw")
    if source.stat().st_size > MAX_ORIGINAL_BYTES:
        raise ValueError("原文件超过已支持的 50 MiB 范围")
    data = source.read_bytes()
    if len(data) > MAX_ORIGINAL_BYTES or hashlib.sha256(data).hexdigest() != document.get("sha256"):
        raise ValueError("原文件校验失败")
    return source, data


def check_integrity(profile=None):
    """Read-only diagnostics for links, originals and consultation references.

    No repairs, metadata inference or scans outside the selected member tree.
    Archive state does not suppress checks. Source Markdown has no historical
    stored hash, so it is checked for presence/path safety, not claimed unchanged.
    """
    profile = load_json(USER_PROFILE) if profile is None else profile
    issues = []
    try:
        _profile(profile)
    except ValueError as exc:
        return [str(exc)]
    try:
        validate_file_management(profile)
    except (ValueError, KeyError, TypeError) as exc:
        issues.append("文件管理关联：" + str(exc))
    records = profile.get("records", {})
    if not isinstance(records, dict):
        issues.append("文档分类索引结构无效")
        records = {}
    hashes = {}
    for entries in records.values():
        if not isinstance(entries, list):
            issues.append("文档分类内容不是列表")
            continue
        for entry in entries:
            identifier = entry.get("id", "未知") if isinstance(entry, dict) else "未知"
            try:
                if not isinstance(entry, dict) or entry.get("status") != "confirmed":
                    raise ValueError("文档索引含未确认或无效条目")
                _document(profile, identifier)
                _original_bytes(entry)
                digest = entry["sha256"]
                if digest in hashes and hashes[digest] != identifier:
                    issues.append("同一原文件有多个正式文档条目：" + hashes[digest] + "、" + identifier)
                hashes[digest] = identifier
            except (OSError, ValueError, KeyError, TypeError) as exc:
                issues.append("文档 " + str(identifier) + "：" + str(exc))
    sources = profile.get("consultations", [])
    if not isinstance(sources, list):
        issues.append("问诊来源索引结构无效")
        sources = []
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("id"), str):
            issues.append("存在缺少编号的问诊来源，需核对旧资料")
            continue
        # Preserve unresolved legacy records; do not invent ownership/file names.
        identifier = source["id"]
        if not re.fullmatch(r"cons_[0-9a-f]{32}", identifier):
            continue
        try:
            _subject(profile, source)
            expected = "consultations/" + identifier + ".md"
            if source.get("file") != expected:
                raise ValueError("问诊来源路径与编号不一致")
            _stored_file(expected, "consultations")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            issues.append("问诊来源 " + identifier + "：" + str(exc))
    episodes = profile.get("consultation_episodes", [])
    if not isinstance(episodes, list):
        issues.append("健康事件索引结构无效")
        episodes = []
    for episode in episodes:
        identifier = episode.get("id", "未知") if isinstance(episode, dict) else "未知"
        try:
            _episode(profile, identifier)
        except (ValueError, KeyError, TypeError) as exc:
            issues.append("健康事件 " + str(identifier) + "：" + str(exc))
    return issues


def export_episode(episode_id, output, role="self"):
    """Export only one selected episode and its explicit source/document links.

    Sources must exist and linked documents must match their original hash. The
    output is an ordinary private local ZIP; no upload, encryption or full-family
    backup is implied. Missing/corrupt inputs never publish a partial archive.
    """
    ensure_self(role)
    requested = Path(output).expanduser()
    if requested.is_symlink():
        raise ValueError("导出目标不能为符号链接")
    destination = requested.resolve()
    forbidden = [HEALTH_DIR.resolve()]
    if os.environ.get("FAMILY_DOCTOR_FAMILY_HOME"):
        forbidden.append(Path(os.environ["FAMILY_DOCTOR_FAMILY_HOME"]).expanduser().resolve())
    if any(destination == root or root in destination.parents for root in forbidden):
        raise ValueError("事件导出文件应位于健康资料目录及共享家庭目录之外")
    if destination.exists():
        raise ValueError("导出目标已存在，请使用新文件名")
    from consultation_episodes import render_episode
    with _lock(USER_PROFILE):
        profile = _profile(load_json(USER_PROFILE))
        episode = _episode(profile, episode_id)
        documents = documents_for_episode(profile, episode)
        reports = {r["id"]: r for r in profile.get("consultations", []) if isinstance(r, dict) and r.get("id") in episode["report_ids"]}
        payloads = {"episode.json": _json_bytes(episode)}
        for ref in episode["report_ids"]:
            report = reports[ref]
            expected = "consultations/" + ref + ".md"
            if report.get("file") != expected:
                raise ValueError("问诊来源文件路径与来源编号不一致")
            payloads[expected] = _stored_file(expected, "consultations").read_bytes()
            payloads["consultations/" + ref + ".json"] = _json_bytes(report)
        document_paths = []
        for document in documents:
            identifier = _bare(document["id"], "document")
            source, data = _original_bytes(document)
            extension = source.suffix.lower()
            if not re.fullmatch(r"\.[a-z0-9]{1,8}", extension):
                raise ValueError("关联文档文件扩展名无效")
            path = "documents/" + identifier + "/original" + extension
            payloads[path] = data
            payloads["documents/" + identifier + "/metadata.json"] = _json_bytes(document)
            document_paths.append((document, path))
        rendered = render_episode(episode, list(reports.values()))
        rendered = re.sub(r"\]\(\.\./(cons_[0-9a-f]{32}\.md)\)", r"](consultations/\1)", rendered)
        rendered = re.sub(r"\[([^\]]+)\]\(episode_[0-9a-f]{32}\.md\)", r"\1（未包含在本次导出）", rendered)
        if document_paths:
            rendered += "\n## 关联文档\n\n"
            from document_views import literal
            for document, path in document_paths:
                title = document.get("title") or document.get("original_name") or "检查资料"
                rendered += "- [{}]({})（资料日期：{}）{}\n".format(literal(title), path,
                    literal(document.get("document_date") or "未知"), "（已归档，仍保留为依据）" if document["archived"] else "")
                rendered += "  - 关联依据：" + literal(document.get("link_basis") or "未记录") + "\n"
        payloads["event.md"] = rendered.encode("utf-8")
        manifest = {"schema_version": 1, "created_at": now_iso(),
                    "member": {"id": profile["owner"].get("member_id"), "name": profile["owner"].get("name")},
                    "episode_id": episode["id"], "source_count": len(reports), "document_count": len(documents),
                    "scope": "selected_episode_and_explicitly_linked_documents", "encrypted": False,
                    "files": [{"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
                              for name, data in sorted(payloads.items())],
                    "manifest_note": "哈希清单覆盖全部资料文件，不包含清单自身；不是完整家庭备份。"}
        payloads["manifest.json"] = _json_bytes(manifest)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".event-export-", dir=destination.parent)
        os.close(descriptor)
        try:
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, data in sorted(payloads.items()):
                    archive.writestr(name, data)
            with open(temporary, "rb") as handle:
                os.fsync(handle.fileno())
            os.link(temporary, destination)  # Exclusive publication; never overwrite.
        finally:
            os.unlink(temporary)
    return {"output": str(destination), "member_name": manifest["member"]["name"],
            "episode_id": episode["id"], "source_count": len(reports),
            "document_count": len(documents), "file_count": len(payloads), "encrypted": False}
