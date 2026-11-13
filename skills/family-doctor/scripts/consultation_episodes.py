"""Conservative filing of multiple reports about one health event.

These rules consolidate files; they do not diagnose disease or establish clinical
certainty. Call mutations while holding the member profile's existing file lock.
The consultation records remain the authoritative, unchanged source reports.
"""
import copy
import html
import json
import uuid
from datetime import datetime, timezone


MAX_AUTO_WINDOW_SECONDS = 48 * 60 * 60
MIN_COVERAGE = 0.8
MIN_IOU = 0.7
PRECISIONS = {"minute", "hour", "day", "unknown"}
COURSES = {"new", "ongoing", "recurrent", "unknown"}
POLARITIES = {"present", "denied", "unknown"}


def _time(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("事件时间必须是带时区的 ISO 8601 字符串")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("事件时间必须是带时区的 ISO 8601 字符串") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("事件时间必须显式包含时区，不能根据机器时区猜测")
    return parsed


def normalize_event(raw):
    """Validate event metadata; absent information remains unknown.

    start/end describe the interval when symptoms occurred, not the uncertainty
    bounds of an estimated onset. time_text preserves the original time wording.
    Date-only/coarse or absent times never qualify for automatic consolidation.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("event 必须为对象")
    symptoms = raw.get("symptoms", [])
    if not isinstance(symptoms, list):
        raise ValueError("event.symptoms 必须为数组")
    normalized = []
    for symptom in symptoms:
        if not isinstance(symptom, dict):
            raise ValueError("每项症状必须为对象")
        key = symptom.get("key")
        site = symptom.get("site", "")
        polarity = symptom.get("polarity", "unknown")
        if not isinstance(key, str) or not key.strip():
            raise ValueError("症状 key 必须为非空字符串")
        if not isinstance(site, str) or polarity not in POLARITIES:
            raise ValueError("症状部位或肯定/否认状态无效")
        normalized.append({"key": key.strip(), "site": site.strip(), "polarity": polarity})
    complete = raw.get("symptoms_complete", False)
    if not isinstance(complete, bool) or (complete and not normalized):
        raise ValueError("symptoms_complete 必须为布尔值，完整症状集合不能为空")
    precision = raw.get("precision", "unknown")
    course = raw.get("course", "unknown")
    if precision not in PRECISIONS or course not in COURSES:
        raise ValueError("事件时间精度或病程类别无效")
    start, end = raw.get("start"), raw.get("end")
    parsed_start = _time(start) if start is not None else None
    parsed_end = _time(end) if end is not None else None
    if parsed_start and parsed_end and parsed_end < parsed_start:
        raise ValueError("症状发生时间结束不能早于开始")
    time_text = raw.get("time_text", "")
    if not isinstance(time_text, str):
        raise ValueError("time_text 必须为字符串")
    result = {"symptoms": normalized, "symptoms_complete": complete,
              "start": start, "end": end, "precision": precision,
              "course": course, "time_text": time_text}
    if len(_signature(result)) != len(normalized):
        raise ValueError("症状集合包含重复项")
    return result


def _signature(event):
    return frozenset((s["key"].strip().casefold(), s["site"].strip().casefold(), s["polarity"])
                     for s in event["symptoms"])


def _interval(event):
    if event["start"] is None or event["end"] is None:
        return None
    return _time(event["start"]), _time(event["end"])


def _precise(event):
    window = _interval(event)
    return (bool(_signature(event)) and event["symptoms_complete"]
            and all(s["polarity"] == "present" and s["site"].casefold() not in {"", "unknown", "未知"}
                    for s in event["symptoms"])
            and event["precision"] in {"minute", "hour"}
            and event["course"] in {"new", "ongoing"}
            and bool(event["time_text"].strip())
            and window is not None
            and 0 < (window[1] - window[0]).total_seconds() <= MAX_AUTO_WINDOW_SECONDS)


def _relation(left, right):
    """Return (candidate, strict_match, reasons), never using reporting time."""
    a, b = normalize_event(left.get("event")), normalize_event(right.get("event"))
    sig_a, sig_b = _signature(a), _signature(b)
    if not sig_a or not sig_b:
        return False, False, []
    # Partial symptom overlap may suggest a relation, but cannot merge a whole report.
    keys_a = {s[0] for s in sig_a}
    keys_b = {s[0] for s in sig_b}
    if not keys_a.intersection(keys_b):
        return False, False, []
    ia, ib = _interval(a), _interval(b)
    if ia is None or ib is None:
        return True, False, ["发生时间不完整，需要核实是否同一次事件"]
    overlap = (min(ia[1], ib[1]) - max(ia[0], ib[0])).total_seconds()
    if overlap <= 0:
        return False, False, []
    reasons = []
    if sig_a != sig_b:
        reasons.append("症状集合、部位或肯定/否认状态不同")
    if not (_precise(a) and _precise(b)):
        reasons.append("时间精度、病程或完整性不足以自动归并")
    duration_a, duration_b = (ia[1] - ia[0]).total_seconds(), (ib[1] - ib[0]).total_seconds()
    coverage = overlap / max(duration_a, duration_b)
    iou = overlap / (duration_a + duration_b - overlap)
    if coverage < MIN_COVERAGE or iou < MIN_IOU:
        reasons.append("双方发生时间重合不足")
    return True, not reasons, reasons or ["完整症状集合一致且双方精确发生时间高度重合"]


def _reports(profile):
    result = {}
    records = profile.get("consultations", [])
    if not isinstance(records, list):
        raise ValueError("问诊来源索引无效")
    for record in records:
        if not isinstance(record, dict):
            continue  # Legacy unresolved entries are not candidates.
        report_id = record.get("id")
        if report_id is None:
            continue
        if not isinstance(report_id, str) or not report_id or report_id in result:
            raise ValueError("问诊来源编号无效或重复")
        result[report_id] = record
    return result


def _episodes(profile):
    episodes = profile.setdefault("consultation_episodes", [])
    if not isinstance(episodes, list):
        raise ValueError("健康事件索引无效")
    ids, source_ids = set(), set()
    for episode in episodes:
        if not isinstance(episode, dict) or not isinstance(episode.get("id"), str) or not episode["id"]:
            raise ValueError("健康事件编号无效")
        refs = episode.get("report_ids")
        if (episode["id"] in ids or not isinstance(refs, list) or not refs
                or any(not isinstance(item, str) or not item for item in refs)
                or len(set(refs)) != len(refs) or source_ids.intersection(refs)):
            raise ValueError("健康事件或来源引用重复/无效")
        ids.add(episode["id"])
        source_ids.update(refs)
    return episodes


def _source_records(episode, reports):
    try:
        records = [reports[report_id] for report_id in episode["report_ids"]]
    except KeyError as exc:
        raise ValueError("健康事件引用了不存在的问诊来源") from exc
    if any(record.get("subject_member_id") != episode.get("subject_member_id") for record in records):
        raise ValueError("健康事件包含其他成员的问诊来源")
    return records


def _basis(basis):
    if not isinstance(basis, str) or not basis.strip():
        raise ValueError("请记录已核实属于同一事件或需要拆分的事实依据")
    return basis.strip()


def _refresh(episode, reports):
    records = _source_records(episode, reports)
    episode["patient"] = next((r.get("patient") for r in reversed(records) if r.get("patient")), "")
    # A reviewed link may contain different symptoms. Keep the union here and
    # render source-specific observations below; never hide later source facts.
    symptom_union = {}
    for record in records:
        for symptom in normalize_event(record.get("event"))["symptoms"]:
            signature = (symptom["key"].casefold(), symptom["site"].casefold(), symptom["polarity"])
            entry = symptom_union.setdefault(signature, {**copy.deepcopy(symptom), "report_ids": []})
            entry["report_ids"].append(record["id"])
    episode["symptoms"] = list(symptom_union.values())
    episode["conflicts"] = []
    fields = [("chiefComplaint", None), ("severity", "symptoms"), ("duration", "symptoms"),
              ("history", None), ("medications", None), ("clinicianDiagnosis", None),
              ("symptoms", "event")]
    for field, parent in fields:
        distinct = {}
        for record in records:
            container = record.get(parent) if parent else record
            value = container.get(field) if isinstance(container, dict) else None
            if value is None or value == "" or value == [] or value == {}:
                continue
            key = json.dumps(value, ensure_ascii=False, sort_keys=True)
            entry = distinct.setdefault(key, {"value": copy.deepcopy(value), "report_ids": []})
            entry["report_ids"].append(record["id"])
        if len(distinct) > 1:
            episode["conflicts"].append({"field": (parent + "." if parent else "") + field,
                                          "kind": "source_difference_unresolved",
                                          "values": list(distinct.values())})
    episode["risk_flags"] = [{"report_id": r["id"], "value": r.get("riskLevel", "UNKNOWN")}
                             for r in records]
    episode["report_count"] = len(records)
    episode["has_source_differences"] = bool(episode["conflicts"])
    episode["file"] = "consultations/episodes/" + episode["id"] + ".md"
    return episode


def _new_episode(record, method, basis):
    return {"id": "episode_" + uuid.uuid4().hex,
            "subject_member_id": record.get("subject_member_id"),
            "report_ids": [record["id"]], "status": "separate", "revision": 1,
            "created_at": record.get("recordedAt"), "candidate_episode_ids": [],
            "merge_basis": [{"method": method, "basis": basis}], "auto_merge_blocked": False}


def _action(profile, kind, basis, before, after):
    actions = profile.setdefault("consultation_episode_actions", [])
    if not isinstance(actions, list):
        raise ValueError("健康事件整理历史无效")
    actions.append({"id": "action_" + uuid.uuid4().hex, "action": kind, "basis": basis,
                    "recorded_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                    "before": copy.deepcopy(before), "after": copy.deepcopy(after)})


def add_report_to_episodes(profile, record, episode_id=None, basis=None):
    """Group an already-indexed source; return its active episode.

    Explicit episode_id requires a factual basis and same known member. It records
    a reviewed relation rather than pretending a strict automatic match occurred.
    """
    reports, episodes = _reports(profile), _episodes(profile)
    if record.get("id") not in reports or reports[record["id"]] != record:
        raise ValueError("先将完整问诊来源加入 consultations 索引")
    normalize_event(record.get("event"))
    for episode in episodes:
        _source_records(episode, reports)
        if record["id"] in episode["report_ids"]:
            if episode_id is not None and episode["id"] != episode_id:
                raise ValueError("该来源已经归入其他事件，请使用事件合并或拆分操作")
            return _refresh(episode, reports)
    if episode_id is not None:
        reason = _basis(basis)
        target = next((e for e in episodes if e["id"] == episode_id), None)
        if target is None:
            raise ValueError("指定健康事件不存在")
        if not record.get("subject_member_id") or target.get("subject_member_id") != record.get("subject_member_id"):
            raise ValueError("不能将不同成员或归属不明的来源合并")
        before = copy.deepcopy(target)
        target["report_ids"].append(record["id"])
        target["status"] = "merged"
        target["revision"] = target.get("revision", 1) + 1
        target.setdefault("merge_basis", []).append({"method": "explicit_same_event", "basis": reason,
                                                     "report_id": record["id"]})
        # A manual relation may involve incomparable periods or symptom sets.
        target["auto_merge_blocked"] = True
        _refresh(target, reports)
        _action(profile, "link", reason, [before], [target])
        return target
    candidates, strict = [], []
    for episode in episodes:
        if not record.get("subject_member_id") or episode.get("subject_member_id") != record.get("subject_member_id"):
            continue
        relations = [_relation(record, old) for old in _source_records(episode, reports)]
        if any(relation[0] for relation in relations):
            candidates.append(episode)
        if (relations and all(relation[1] for relation in relations)
                and not episode.get("auto_merge_blocked")):
            strict.append(episode)
    # Requiring one candidate (not merely one strict winner) avoids ambiguity.
    if len(candidates) == 1 and len(strict) == 1:
        target = strict[0]
        target["report_ids"].append(record["id"])
        target["status"] = "merged"
        target["revision"] = target.get("revision", 1) + 1
        target.setdefault("merge_basis", []).append({"method": "strict_overlap_v1", "report_id": record["id"],
            "basis": "完整症状集合相同，精确发生时间与事件内每份来源均高度重合；此规则只整理档案，不作医学判断"})
        return _refresh(target, reports)
    target = _new_episode(record, "separate", "初次记录时未找到唯一匹配，先独立建档")
    target["candidate_episode_ids"] = [e["id"] for e in candidates]
    if candidates:
        target["status"] = "needs_review"
        for candidate in candidates:
            candidate.setdefault("candidate_episode_ids", []).append(target["id"])
    episodes.append(target)
    return _refresh(target, reports)


def merge_episodes(profile, episode_ids, basis):
    """Explicit factual reconciliation; retain every source and before snapshot."""
    reason = _basis(basis)
    if not isinstance(episode_ids, (list, tuple)) or len(episode_ids) < 2 or len(set(episode_ids)) != len(episode_ids):
        raise ValueError("合并需要至少两个不同的健康事件编号")
    reports, episodes = _reports(profile), _episodes(profile)
    selected = [next((e for e in episodes if e["id"] == item), None) for item in episode_ids]
    if any(e is None for e in selected):
        raise ValueError("指定健康事件不存在")
    subject = selected[0].get("subject_member_id")
    if not subject or any(e.get("subject_member_id") != subject for e in selected):
        raise ValueError("不能合并不同成员或归属不明的健康事件")
    for episode in selected:
        _source_records(episode, reports)
    before = copy.deepcopy(selected)
    target = selected[0]
    for episode in selected[1:]:
        target["report_ids"].extend(episode["report_ids"])
        target.setdefault("merge_basis", []).extend(copy.deepcopy(episode.get("merge_basis", [])))
    target.setdefault("merge_basis", []).append({"method": "explicit_same_event", "basis": reason})
    target["status"] = "merged"
    target["revision"] = target.get("revision", 1) + 1
    target["auto_merge_blocked"] = True
    removed = set(episode_ids[1:])
    profile["consultation_episodes"] = [e for e in episodes if e["id"] not in removed]
    for episode in profile["consultation_episodes"]:
        refs = [target["id"] if item in removed else item for item in episode.get("candidate_episode_ids", [])]
        episode["candidate_episode_ids"] = list(dict.fromkeys(item for item in refs if item != episode["id"]))
    _refresh(target, reports)
    _action(profile, "merge", reason, before, [target])
    return target


def split_report(profile, report_id, basis):
    """Move one source to a new event; never immediately auto-rejoin it."""
    reason = _basis(basis)
    reports, episodes = _reports(profile), _episodes(profile)
    source = next((e for e in episodes if report_id in e["report_ids"]), None)
    if source is None or report_id not in reports:
        raise ValueError("问诊来源或所属健康事件不存在")
    _source_records(source, reports)
    if len(source["report_ids"]) < 2:
        raise ValueError("该问诊已经是独立事件，无需拆分")
    before = copy.deepcopy(source)
    source["report_ids"].remove(report_id)
    source["status"] = "merged" if len(source["report_ids"]) > 1 else "separate"
    source["revision"] = source.get("revision", 1) + 1
    source["auto_merge_blocked"] = True
    source.setdefault("merge_basis", []).append({"method": "split", "basis": reason, "report_id": report_id})
    target = _new_episode(reports[report_id], "split", reason)
    target["auto_merge_blocked"] = True
    episodes.append(target)
    _refresh(source, reports)
    _refresh(target, reports)
    _action(profile, "split", reason, [before], [source, target])
    return target


def _display(value):
    """Treat source text as literal Markdown, including names and old filenames."""
    text = html.escape(str(value), quote=False).replace("\r", " ").replace("\n", " / ")
    for character in ("\\", "`", "*", "_", "[", "]"):
        text = text.replace(character, "\\" + character)
    return text


def _display_time(value):
    if not value:
        return "未知"
    try:
        return _time(value).isoformat(sep=" ", timespec="minutes")
    except ValueError:
        return _display(value)


def _source_name(record):
    name = record.get("reporter_name")
    if not name and record.get("role") in {"self", "本人"}:
        name = record.get("patient")
    return str(name or "提供者姓名未记录")


def render_episode(episode, reports):
    """Render a rebuildable event view; preserve every source's risk and wording."""
    if isinstance(reports, list):
        reports = _reports({"consultations": reports})
    rendered = _refresh(copy.deepcopy(episode), reports)
    sources = _source_records(rendered, reports)
    labels = {"merged": "已整理为同一事件", "needs_review": "关联待核实", "separate": "独立事件"}
    lines = ["# 健康事件整理", "", "- 姓名：" + _display(rendered.get("patient") or "未提供"),
             "- 状态：" + labels.get(rendered.get("status"), "待核实"),
             "- 来源问诊数：" + str(len(sources)),
             "- 本页为可重建的事件整理；原始来源保留，资料差异未被自动裁定。", ""]
    if rendered.get("candidate_episode_ids"):
        lines.extend(["## 可能相关的其他事件", "仍需核实是否同一次症状："])
        for index, related_id in enumerate(rendered["candidate_episode_ids"], 1):
            if isinstance(related_id, str) and related_id.startswith("episode_") and related_id[8:].isalnum():
                lines.append("- [相关事件 " + str(index) + "](" + related_id + ".md)")
        lines.append("")
    lines.extend(["## 整理依据"])
    for entry in rendered.get("merge_basis", []):
        lines.append("- " + _display(entry.get("basis", "")))
    if rendered["conflicts"]:
        lines.extend(["", "## 来源差异（保留各方说法，不自动选取一方）"])
        field_labels = {"chiefComplaint": "主诉", "symptoms.severity": "严重程度", "symptoms.duration": "持续时间",
                        "history": "既往史", "medications": "用药", "clinicianDiagnosis": "医生诊断记录",
                        "event.symptoms": "症状集合或肯定/否认状态（详见各来源）"}
        for conflict in rendered["conflicts"]:
            lines.append("- " + field_labels.get(conflict["field"], conflict["field"]) + "：")
            for value in conflict["values"]:
                names = [_display(_source_name(reports[ref])) + "（" + _display_time(reports[ref].get("recordedAt")) + "）"
                         for ref in value["report_ids"]]
                # Detailed structured symptoms are shown with Chinese labels in
                # their source sections, not as an untranslated JSON object.
                detail = "详见该来源的症状记录" if conflict["field"] == "event.symptoms" else _display(value["value"])
                lines.append("  - " + "、".join(names) + "：" + detail)
    role_labels = {"self": "本人", "本人": "本人", "family_proxy": "家庭代问诊", "proxy": "代问诊", "代问诊": "代问诊"}
    precision_labels = {"minute": "精确到分钟", "hour": "精确到小时", "day": "仅知道日期", "unknown": "未知"}
    course_labels = {"new": "本次新出现", "ongoing": "持续中", "recurrent": "再次出现", "unknown": "未知"}
    polarity_labels = {"present": "报告存在", "denied": "报告否认", "unknown": "未知"}
    risk_labels = {"UNKNOWN": "待评估", "RED": "红色风险", "ORANGE": "橙色风险", "YELLOW": "黄色风险",
                   "GREEN": "绿色风险", "待评估": "待评估", "高风险": "高风险", "中风险": "中风险", "低风险": "低风险"}
    source_labels = {"user_report": "本人陈述", "caregiver_report": "家属陈述", "family_report": "家属陈述",
                     "family_proxy": "家属陈述", "clinician_document": "医生资料", "ai_assessment": "AI 分析", "other": "其他来源"}
    symptom_labels = {"location": "部位", "character": "性质", "severity": "严重程度（0–10）", "duration": "持续时间",
                      "triggers": "诱因", "alleviates": "缓解因素", "worsens": "加重因素", "associated": "伴随症状"}
    for record in sorted(sources, key=lambda r: (str(r.get("recordedAt") or ""), r["id"])):
        event = normalize_event(record.get("event"))
        reporter = _display(_source_name(record))
        lines.extend(["", "## " + reporter + " · " + _display_time(record.get("recordedAt")),
                      "- 提供者：" + reporter, "- 问诊角色：" + role_labels.get(record.get("role"), "未记录"),
                      "- 问诊记录时间：" + _display_time(record.get("recordedAt")),
                      "- 症状发生时间：" + _display_time(event["start"]) + " 至 " + _display_time(event["end"]),
                      "- 时间原话：" + _display(event["time_text"] or "未提供"),
                      "- 时间精度：" + precision_labels[event["precision"]] + "；病程：" + course_labels[event["course"]],
                      "- 主诉：" + _display(record.get("chiefComplaint") or "未知"),
                      "- 原始风险记录：" + risk_labels.get(record.get("riskLevel"), "待评估") + "（保留原来源，未重新分诊）"])
        for symptom in event["symptoms"]:
            lines.append("- 结构化症状：" + _display(symptom["key"]) + "；部位：" + _display(symptom["site"] or "未知")
                         + "；状态：" + polarity_labels[symptom["polarity"]])
        legacy_symptoms = record.get("symptoms")
        if isinstance(legacy_symptoms, dict):
            for key, label in symptom_labels.items():
                value = legacy_symptoms.get(key)
                if value is not None and value != "":
                    lines.append("- " + label + "：" + _display(value))
        if record.get("file"):
            # Canonical files are consultations/episodes/*.md; source files are
            # consultations/*.md. Links are only generated for this trusted shape.
            path = record["file"]
            if (isinstance(path, str) and path.startswith("consultations/") and path.count("/") == 1
                    and ".." not in path and path.split("/", 1)[1].replace("_", "").replace("-", "").replace(".", "").isalnum()):
                lines.append("- [原始问诊文件](../" + path.split("/", 1)[1] + ")")
        for key, label in (("history", "既往史"), ("medications", "用药"), ("aiHypothesis", "AI 可能原因（待核实）"),
                           ("clinicianDiagnosis", "医生诊断记录"), ("medicalAdvice", "原就医建议"),
                           ("drugAdvice", "原药物建议"), ("selfCare", "自我护理"),
                           ("followup", "复诊安排"), ("observation", "观察要点")):
            if record.get(key):
                lines.append("- " + label + "：" + _display(record[key]))
        source = record.get("source")
        if isinstance(source, dict):
            lines.append("- 来源类型：" + source_labels.get(source.get("type"), "未记录") + "；引用：" + _display(source.get("reference") or "未提供"))
    return "\n".join(lines) + "\n"
