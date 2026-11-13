"""Validate Hermes' structured chat observations; retain no message text or quotes.

This module checks provenance, literal evidence and repeated observation counts.
It cannot verify an LLM's semantic interpretation and is not a diagnostic model.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "skills/family-doctor/scripts"))
from family_context import family_home, read_registry, resolve_context, resolve_member, safe_path
from health_store import _lock, _replace_text

DIMENSIONS = {"memory_repetition": "重复提问", "context_memory": "上下文记忆", "comprehension": "理解回应",
              "coherence": "话题连贯", "temporal_order": "时间顺序", "word_finding": "找词表达",
              "orientation": "时间地点定向", "planning": "计划组织", "daily_function": "日常功能"}
STATUS_LABELS = {"insufficient": "观察资料不足", "observing": "持续观察", "review": "建议家属复核", "urgent": "突发变化需及时关注"}
ALERT_LABELS = {"single_signal": "少量线索，继续观察", "repeated_self_signal": "本人表达在多个独立日期出现同类线索",
                "repeated_caregiver_signal": "家属在多个独立日期报告同类变化", "sudden_confusion": "对话提到突然明显意识或定向变化",
                "sudden_function_change": "对话提到突然明显日常能力变化"}
EXCLUDED = {"shared_account", "quoted_or_forwarded", "source_uncertain", "not_current"}
LIMITED = {"asr_uncertain", "missing_context", "assistant_misunderstanding", "ambiguous_prompt", "fatigue", "mood",
           "medication", "hearing_vision", "mixed_quotation"}
CONTEXT_REQUIRED = {"memory_repetition", "context_memory", "comprehension", "coherence"}
MAX_MESSAGES = 80
MAX_TEXT_BYTES = 256 * 1024
TIMEZONE = ZoneInfo("Asia/Shanghai")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,192}\Z")


def _now():
    return datetime.now(timezone.utc)


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_dump(value).encode("utf-8")).hexdigest()


def _time(value):
    if not isinstance(value, str) or "T" not in value or len(value) > 64:
        raise ValueError("时间必须包含日期、时分和时区")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError) as exc:
        raise ValueError("时间必须包含日期、时分和时区") from exc
    if parsed.tzinfo is None:
        raise ValueError("时间缺少时区，不能补造本机时间")
    return parsed.astimezone(timezone.utc)


def _keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError("结构字段缺失或包含不支持的字段")


def _enum(value, options):
    if not isinstance(value, str) or value not in options:
        raise ValueError("枚举字段无效")
    return value


def _list(value, options=None, maximum=80):
    if not isinstance(value, list) or len(value) > maximum or any(not isinstance(v, str) for v in value) or len(set(value)) != len(value):
        raise ValueError("列表必须由互不重复的文本标识组成")
    if options is not None and any(v not in options for v in value):
        raise ValueError("列表包含不支持的枚举")
    return value


def _token(value):
    if not isinstance(value, str) or not TOKEN.fullmatch(value) or value.startswith("REPLACE_"):
        raise ValueError("会话、窗口和消息必须使用真实稳定标识，不能使用占位值")
    return value


def _json_read(path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("重复 JSON 字段")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("非有限 JSON 数值")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=constant)


def _empty():
    return {"schema_version": 1, "revision": 0, "aliases": {}, "messages": {}, "records": []}


def analysis_template():
    return {"dimensions": {key: {"status": "not_assessable", "opportunities": []} for key in DIMENSIONS},
            "urgent": {"status": "none", "reason_codes": [], "evidence": [], "onset_evidence": []}}


class ChatCognitionStore:
    def __init__(self, home=None, profile=None):
        self.home = family_home(home)
        self.profile = resolve_context(self.home, profile)["profile"]

    def _registry(self):
        registry = read_registry(self.home)
        if self.profile not in registry["profiles"]:
            raise ValueError("当前 profile 未登记")
        return registry

    def _path(self):
        return safe_path(self.home, "cognition", "chat-observations.json")

    def _input(self, payload, registry, now, with_analysis):
        fields = {"schema_version", "session_id", "window_id", "member", "source_mode", "input_mode", "conditions", "messages"}
        _keys(payload, fields | ({"analysis"} if with_analysis else set()))
        if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
            raise ValueError("分析协议版本无效")
        _token(payload["session_id"])
        _token(payload["window_id"])
        try:
            subject = resolve_member(registry, payload["member"])
        except (ValueError, TypeError) as exc:
            raise ValueError("被观察成员未登记或重名，请核对成员编号") from exc
        mode = _enum(payload["source_mode"], ("self", "caregiver"))
        expected = "self" if registry["profiles"][self.profile] == subject else "caregiver"
        if mode != expected:
            raise ValueError("本人或家属来源与当前 profile 和被观察成员不一致")
        input_mode = _enum(payload["input_mode"], ("text", "voice"))
        conditions = set(_list(payload["conditions"], EXCLUDED | LIMITED))
        messages = payload["messages"]
        if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES:
            raise ValueError("一次只能分析 1 至 80 条本轮相关消息")
        by_id, aliases, digests, previous, text_bytes = {}, {}, {}, None, 0
        for message in messages:
            _keys(message, ("id", "role", "at", "text"))
            mid = _token(message["id"])
            if mid in by_id:
                raise ValueError("输入窗口包含重复消息标识")
            role = _enum(message["role"], ("user", "assistant"))
            stamp = _time(message["at"])
            if stamp > now or (previous is not None and stamp < previous):
                raise ValueError("消息时间必须按真实顺序排列且不能晚于当前时间")
            previous = stamp
            text = message["text"]
            if not isinstance(text, str) or not text.strip() or len(text) > 16000:
                raise ValueError("消息文本为空或过长")
            text_bytes += len(text.encode("utf-8"))
            if text_bytes > MAX_TEXT_BYTES:
                raise ValueError("本轮文本超过 256 KiB，请缩小相关窗口")
            # A second id for identical content at the same instant cannot inflate
            # counts. A real repeated question at a later instant stays distinct.
            fingerprint = _hash({"text": text, "role": role, "at": stamp.isoformat()})
            digest = _hash({"profile": self.profile, "fingerprint": fingerprint})
            if digest in digests:
                raise ValueError("窗口包含同一时刻同一来源的重复消息，不能视为独立表达")
            alias = _hash({"profile": self.profile, "id": mid})
            by_id[mid] = {"id": mid, "role": role, "at": stamp.isoformat(), "text": text, "digest": digest}
            aliases[alias] = digest
            digests[digest] = {"fingerprint": fingerprint, "role": role, "at": stamp.isoformat(),
                               "profile": self.profile, "member_id": subject}
        users = [m for m in by_id.values() if m["role"] == "user"]
        if not users:
            raise ValueError("没有当前用户原始表达，不能分析助手自己的内容")
        if len(users) < 2 or len(messages) < 3:
            conditions.add("missing_context")
        quality = "excluded" if conditions & EXCLUDED else "limited" if conditions else "usable"
        return {"member_id": subject, "source_mode": mode, "input_mode": input_mode, "quality": quality,
                "conditions": sorted(conditions), "messages": by_id, "aliases": aliases, "digests": digests,
                "session_key": _hash([self.profile, payload["session_id"]]),
                "window_key": _hash([self.profile, subject, payload["session_id"], payload["window_id"]]),
                "observed_at": max(m["at"] for m in users)}

    def prepare(self, payload):
        data = self._input(payload, self._registry(), _now(), False)
        return {"ok": True, "sourceMode": data["source_mode"], "quality": data["quality"],
                "conditions": data["conditions"], "messageCount": len(data["messages"]),
                "dimensions": DIMENSIONS, "analysisTemplate": analysis_template(),
                "instructions": ["由 Hermes 当前模型分析已经可见的原始多轮对话，不调用独立模型服务。",
                    "所有维度都必须显式填写。没有真实评估机会时保留 not_assessable，不得写成 no_signal。",
                    "每个机会填写 response_ids、context_ids、outcome 和 evidence；证据为 message_id 与原文精确 quote。",
                    "只引用当前说话者的原始用户消息。助手、引述、转发、他人代述不能作为本人语言样本。",
                    "前四维需要更早的上下文。排除澄清、纠错、助手误解、方言、转写等其他解释；有影响因素写 conditions。",
                    "语音输入只有转写文本，不能推测停顿、语速、音色或声学特征。",
                    "稳定历史消息标识可用 local_ 加会话、角色、真实时间与文本摘要生成；不能用随机或轮次序号补造。",
                    "急性变化须同时引用明确当前突然发生的原话和变化原话；不确定时不能声称诊断。",
                    "返回的证据仅在本轮工具内校验；保存时移除原文、引用和自由摘要。"],
                "clinicalValidation": False}

    @staticmethod
    def _evidence(items, messages, allowed):
        if not isinstance(items, list) or not 1 <= len(items) <= 16:
            raise ValueError("每项结论必须有 1 至 16 条可核对证据")
        refs = set()
        for item in items:
            _keys(item, ("message_id", "quote"))
            mid, quote = item["message_id"], item["quote"]
            if not isinstance(mid, str) or mid not in allowed or mid not in messages or messages[mid]["role"] != "user":
                raise ValueError("证据必须引用该机会的原始用户消息，不能引用助手或未知消息")
            if not isinstance(quote, str) or not quote.strip() or len(quote) > 2000 or quote not in messages[mid]["text"]:
                raise ValueError("证据引用与原始消息不一致")
            refs.add(mid)
        return refs

    def _analysis(self, analysis, data):
        _keys(analysis, ("dimensions", "urgent"))
        _keys(analysis["dimensions"], DIMENSIONS)
        messages = data["messages"]
        positions = {mid: index for index, mid in enumerate(messages)}
        result = {}
        for key, dimension in analysis["dimensions"].items():
            _keys(dimension, ("status", "opportunities"))
            status = _enum(dimension["status"], ("not_assessable", "no_signal", "possible_signal"))
            opportunities = dimension["opportunities"]
            if not isinstance(opportunities, list) or len(opportunities) > 40:
                raise ValueError("每维评估机会格式无效或过多")
            if (status == "not_assessable") != (len(opportunities) == 0):
                raise ValueError("无法评估不能附带机会，已评估结论不能缺少机会")
            seen, out = set(), []
            for opportunity in opportunities:
                _keys(opportunity, ("response_ids", "context_ids", "outcome", "evidence"))
                responses = _list(opportunity["response_ids"], messages, maximum=8)
                context = _list(opportunity["context_ids"], messages, maximum=16)
                if not responses or any(messages[mid]["role"] != "user" for mid in responses) or seen.intersection(responses):
                    raise ValueError("评估机会必须对应尚未在本维重复使用的用户回应")
                if set(responses) & set(context) or (key in CONTEXT_REQUIRED and not context):
                    raise ValueError("此维需要真实的先前上下文，且不能把同一回应作为上下文")
                if context and max(_time(messages[mid]["at"]) for mid in context) > min(_time(messages[mid]["at"]) for mid in responses):
                    raise ValueError("不能用未来消息作为先前上下文")
                if context and max(positions[mid] for mid in context) >= min(positions[mid] for mid in responses):
                    raise ValueError("上下文必须在输入消息顺序中早于对应回应，不能引用事后消息")
                outcome = _enum(opportunity["outcome"], ("no_signal", "possible_signal"))
                evidence = self._evidence(opportunity["evidence"], messages, set(responses))
                if evidence != set(responses):
                    raise ValueError("每条作为评估机会的回应都必须有对应原文证据")
                seen.update(responses)
                out.extend({"message_key": messages[mid]["digest"], "at": messages[mid]["at"], "outcome": outcome}
                           for mid in responses)
            if opportunities and ("possible_signal" if any(o["outcome"] == "possible_signal" for o in out) else "no_signal") != status:
                raise ValueError("维度结论与机会中的证据结论不一致")
            result[key] = out
        urgent = analysis["urgent"]
        _keys(urgent, ("status", "reason_codes", "evidence", "onset_evidence"))
        urgent_status = _enum(urgent["status"], ("none", "possible_acute_change"))
        codes = _list(urgent["reason_codes"], {"sudden_confusion", "sudden_function_change"}, maximum=2)
        acute = []
        if urgent_status == "none":
            if codes or urgent["evidence"] != [] or urgent["onset_evidence"] != []:
                raise ValueError("没有急性变化时不得保留急性结论或证据")
        else:
            if not codes:
                raise ValueError("急性变化缺少固定原因类型")
            users = {mid for mid, message in messages.items() if message["role"] == "user"}
            refs = self._evidence(urgent["evidence"], messages, users)
            onset = self._evidence(urgent["onset_evidence"], messages, users)
            # Literal onset anchors prevent a model from silently turning a
            # chronic/historical statement into an acute claim. Semantics still
            # depend on Hermes and need human verification.
            quotes = " ".join(item["quote"].lower() for item in urgent["onset_evidence"])
            if not any(anchor in quotes for anchor in ("突然", "刚刚", "刚才", "今天开始", "几小时前", "sudden", "just now", "started today")):
                raise ValueError("急性提示需要原文中明确的突然或刚刚发生依据")
            if data["quality"] != "excluded":
                message_keys = sorted(messages[mid]["digest"] for mid in refs | onset)
                stamp = max(messages[mid]["at"] for mid in refs | onset)
                acute = [{"code": code, "message_keys": message_keys, "at": stamp} for code in codes]
        return result, acute

    def _read(self, registry, now):
        path = self._path()
        if not path.exists():
            return _empty()
        try:
            data = _json_read(path)
            _keys(data, ("schema_version", "revision", "aliases", "messages", "records"))
            if type(data["schema_version"]) is not int or data["schema_version"] != 1 or type(data["revision"]) is not int:
                raise ValueError("Invalid version")
            if not isinstance(data["aliases"], dict) or not isinstance(data["messages"], dict) or not isinstance(data["records"], list) or data["revision"] != len(data["records"]):
                raise ValueError("Invalid index")
            for alias, key in data["aliases"].items():
                if not DIGEST.fullmatch(alias) or key not in data["messages"]:
                    raise ValueError("Invalid message alias")
            for key, msg in data["messages"].items():
                _keys(msg, ("fingerprint", "role", "at", "profile", "member_id"))
                if not DIGEST.fullmatch(key) or not DIGEST.fullmatch(msg["fingerprint"]) or msg["profile"] not in registry["profiles"] or msg["member_id"] not in registry["members"]:
                    raise ValueError("Invalid message index")
                if key != _hash({"profile": msg["profile"], "fingerprint": msg["fingerprint"]}):
                    raise ValueError("Invalid message digest")
                _enum(msg["role"], ("user", "assistant"))
                if _time(msg["at"]) > now:
                    raise ValueError("Future persisted message")
            window_keys, assessment_keys, record_ids, acute_keys = set(), set(), set(), set()
            for record in data["records"]:
                _keys(record, ("id", "window_key", "fingerprint", "session_key", "member_id", "reporter_profile", "source_mode", "input_mode", "quality", "conditions", "observed_at", "created_at", "dimensions", "urgent"))
                if not isinstance(record["id"], str) or not re.fullmatch(r"chat_[0-9a-f]{32}", record["id"]) or record["id"] in record_ids:
                    raise ValueError("Invalid record identity")
                record_ids.add(record["id"])
                if record["window_key"] in window_keys or any(not DIGEST.fullmatch(record[k]) for k in ("window_key", "fingerprint", "session_key")):
                    raise ValueError("Invalid window")
                window_keys.add(record["window_key"])
                if record["member_id"] not in registry["members"] or record["reporter_profile"] not in registry["profiles"]:
                    raise ValueError("Unknown member")
                _enum(record["source_mode"], ("self", "caregiver"))
                if (record["source_mode"] == "self") != (registry["profiles"][record["reporter_profile"]] == record["member_id"]):
                    raise ValueError("Changed provenance")
                _enum(record["input_mode"], ("text", "voice"))
                _enum(record["quality"], ("usable", "limited", "excluded"))
                conditions = set(_list(record["conditions"], EXCLUDED | LIMITED))
                expected = "excluded" if conditions & EXCLUDED else "limited" if conditions else "usable"
                if expected != record["quality"] or _time(record["observed_at"]) > now:
                    raise ValueError("Invalid quality")
                if not _time(record["observed_at"]) <= _time(record["created_at"]) <= now:
                    raise ValueError("Invalid creation time")
                _keys(record["dimensions"], DIMENSIONS)
                for domain, opportunities in record["dimensions"].items():
                    if not isinstance(opportunities, list) or (record["quality"] != "usable" and opportunities):
                        raise ValueError("Invalid opportunities")
                    for op in opportunities:
                        _keys(op, ("message_key", "at", "outcome"))
                        msg = data["messages"][op["message_key"]]
                        if msg["role"] != "user" or msg["at"] != op["at"] or msg["member_id"] != record["member_id"] or msg["profile"] != record["reporter_profile"]:
                            raise ValueError("Invalid opportunity reference")
                        if _time(op["at"]) > _time(record["observed_at"]):
                            raise ValueError("Opportunity after observed window")
                        _enum(op["outcome"], ("no_signal", "possible_signal"))
                        key = (record["member_id"], domain, op["message_key"])
                        if key in assessment_keys:
                            raise ValueError("Duplicate opportunity")
                        assessment_keys.add(key)
                if not isinstance(record["urgent"], list) or (record["quality"] == "excluded" and record["urgent"]):
                    raise ValueError("Invalid urgent evidence")
                for urgent in record["urgent"]:
                    _keys(urgent, ("code", "message_keys", "at"))
                    _enum(urgent["code"], ("sudden_confusion", "sudden_function_change"))
                    _list(urgent["message_keys"], data["messages"])
                    if not urgent["message_keys"]:
                        raise ValueError("Missing acute evidence")
                    refs = [data["messages"][key] for key in urgent["message_keys"]]
                    if any(msg["role"] != "user" or msg["member_id"] != record["member_id"] or msg["profile"] != record["reporter_profile"] for msg in refs):
                        raise ValueError("Invalid acute source")
                    if _time(urgent["at"]) != max(_time(msg["at"]) for msg in refs) or _time(urgent["at"]) > _time(record["observed_at"]):
                        raise ValueError("Invalid acute time")
                    identity = (record["member_id"], urgent["code"], tuple(sorted(urgent["message_keys"])))
                    if identity in acute_keys:
                        raise ValueError("Duplicate acute event")
                    acute_keys.add(identity)
            return data
        except (ValueError, TypeError, KeyError, OSError, OverflowError) as exc:
            raise ValueError("聊天观察资料损坏或成员关联变化，已停止读取；原资料未重置") from exc

    def record(self, payload):
        registry, now = self._registry(), _now()
        registry_revision = _hash(registry)
        incoming = self._input(payload, registry, now, True)
        dimensions, urgent = self._analysis(payload["analysis"], incoming)
        fingerprint = _hash({"member": incoming["member_id"], "mode": incoming["source_mode"], "input": incoming["input_mode"],
                             "conditions": incoming["conditions"], "aliases": incoming["aliases"], "dimensions": dimensions, "urgent": urgent})
        with _lock(self._path()):
            registry, now = self._registry(), _now()
            if _hash(registry) != registry_revision:
                raise ValueError("家庭成员登记在分析期间发生变化，请重新准备本轮观察")
            incoming = self._input(payload, registry, now, True)
            stored = self._read(registry, now)
            for record in stored["records"]:
                if record["window_key"] == incoming["window_key"]:
                    if record["fingerprint"] != fingerprint:
                        raise ValueError("相同分析窗口已有不同结果，请核对来源；不会覆盖已保存观察")
                    return {"ok": True, "duplicate": True, "newOpportunities": 0, "newUrgent": 0, "quality": record["quality"], "revision": stored["revision"]}
            for alias, key in incoming["aliases"].items():
                if alias in stored["aliases"] and stored["aliases"][alias] != key:
                    raise ValueError("同一消息标识的内容、角色或时间已变化，已停止记录")
            for key, msg in incoming["digests"].items():
                if key in stored["messages"] and stored["messages"][key] != msg:
                    raise ValueError("同一消息被关联到不同成员，已停止记录")
            seen = {(r["member_id"], domain, op["message_key"]): op["outcome"]
                    for r in stored["records"] for domain, ops in r["dimensions"].items() for op in ops}
            for domain, opportunities in dimensions.items():
                for opportunity in opportunities:
                    identity = (incoming["member_id"], domain, opportunity["message_key"])
                    if identity in seen and seen[identity] != opportunity["outcome"]:
                        raise ValueError("同一消息在同一维度已有不同结论，请复核原观察；不会把冲突结果作为重复提交忽略")
            # A limited/excluded attempt does not reserve an assessment key.
            # A later complete window can assess that same actual response.
            additions = {domain: [op for op in ops if incoming["quality"] == "usable" and (incoming["member_id"], domain, op["message_key"]) not in seen] for domain, ops in dimensions.items()}
            seen_urgent = {(r["member_id"], u["code"], tuple(u["message_keys"])) for r in stored["records"] for u in r["urgent"]}
            acute = [u for u in urgent if (incoming["member_id"], u["code"], tuple(u["message_keys"])) not in seen_urgent]
            count = sum(len(ops) for ops in additions.values())
            new_user_messages = any(msg["role"] == "user" and key not in stored["messages"] for key, msg in incoming["digests"].items())
            if not count and not acute and not new_user_messages:
                # Even a content-identical retry can introduce a second stable
                # source id. Remember that alias so its later reuse with changed
                # text/time cannot bypass the immutable-message check. Index-only
                # writes do not add observations or change the record revision.
                if any(alias not in stored["aliases"] for alias in incoming["aliases"]):
                    stored["messages"].update(incoming["digests"])
                    stored["aliases"].update(incoming["aliases"])
                    _replace_text(self._path(), json.dumps(stored, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
                return {"ok": True, "duplicate": True, "newOpportunities": 0, "newUrgent": 0, "quality": incoming["quality"], "revision": stored["revision"]}
            saved = {key: incoming[key] for key in ("window_key", "session_key", "member_id", "source_mode", "input_mode", "quality", "conditions", "observed_at")}
            saved.update(id="chat_" + uuid.uuid4().hex, fingerprint=fingerprint, reporter_profile=self.profile,
                         created_at=now.isoformat(), dimensions=additions, urgent=acute)
            stored["messages"].update(incoming["digests"])
            stored["aliases"].update(incoming["aliases"])
            stored["records"].append(saved)
            stored["revision"] += 1
            _replace_text(self._path(), json.dumps(stored, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
            return {"ok": True, "duplicate": False, "newOpportunities": count, "newUrgent": len(acute),
                    "quality": incoming["quality"], "conditions": incoming["conditions"], "revision": stored["revision"],
                    "clinicalValidation": False, "reminderChannel": "web"}

    def snapshot(self, days=30):
        if type(days) is not int or not 1 <= days <= 366:
            raise ValueError("统计窗口必须是 1 至 366 天")
        registry, now = self._registry(), _now()
        stored = self._read(registry, now)
        today = now.astimezone(TIMEZONE).date()
        first = today - timedelta(days=days - 1)
        reference_start = first - timedelta(days=90)
        def day(stamp):
            return _time(stamp).astimezone(TIMEZONE).date()
        members = []
        for member_id, member in registry["members"].items():
            all_records = [r for r in stored["records"] if r["member_id"] == member_id]
            recent = [r for r in all_records if first <= day(r["observed_at"]) <= today]
            public = {"status": "insufficient", "label": STATUS_LABELS["insufficient"], "lastAnalyzedAt": max((r["created_at"] for r in all_records), default=None),
                      "self": {}, "caregiver": {}, "quality": {q: sum(r["quality"] == q for r in recent) for q in ("usable", "limited", "excluded")},
                      "dimensions": [], "trend": [], "alerts": [], "clinicalValidation": False}
            trend = {(first + timedelta(days=i)).isoformat(): {"date": (first + timedelta(days=i)).isoformat(), "opportunities": 0, "selfSignals": 0, "caregiverSignals": 0} for i in range(days)}
            # Opportunities are dated from their actual messages, not a later
            # window's final message or the time the analysis was recorded.
            valid = [(r, domain, op) for r in all_records if r["quality"] == "usable" for domain, ops in r["dimensions"].items() for op in ops if first <= day(op["at"]) <= today]
            for mode in ("self", "caregiver"):
                relevant = [(r, d, op) for r, d, op in valid if r["source_mode"] == mode]
                opportunities = {op["message_key"] for r, d, op in relevant}
                observation_days = {day(op["at"]) for r, d, op in relevant}
                signal_days = {day(op["at"]) for r, d, op in relevant if op["outcome"] == "possible_signal"}
                public[mode] = {"windowCount": len({r["id"] for r, d, op in relevant}), "observationDays": len(observation_days), "opportunities": len(opportunities), "signalDays": len(signal_days)}
                for date_key in trend:
                    ops = [op for r, d, op in relevant if day(op["at"]).isoformat() == date_key]
                    trend[date_key]["selfSignals" if mode == "self" else "caregiverSignals"] = len({op["message_key"] for op in ops if op["outcome"] == "possible_signal"})
                    if mode == "self":
                        trend[date_key]["opportunities"] = len({op["message_key"] for op in ops})
            for domain, label in DIMENSIONS.items():
                own = [(r, op) for r, d, op in valid if d == domain and r["source_mode"] == "self"]
                proxy = [(r, op) for r, d, op in valid if d == domain and r["source_mode"] == "caregiver"]
                signals = [op for r, op in own if op["outcome"] == "possible_signal"]
                signal_days = {day(op["at"]) for op in signals}
                proxy_days = {day(op["at"]) for r, op in proxy if op["outcome"] == "possible_signal"}
                reference = {"available": False, "days": 0, "opportunities": 0, "signalCount": 0, "comparison": "unavailable"}
                modes = {r["input_mode"] for r, op in own}
                if len(modes) == 1:
                    historical = [op for r in all_records if r["quality"] == "usable" and r["source_mode"] == "self" and r["input_mode"] in modes for op in r["dimensions"][domain] if reference_start <= day(op["at"]) < first]
                    history_days = {day(op["at"]) for op in historical}
                    if len(history_days) >= 6 and len({day(op["at"]) for r, op in own}) >= 3:
                        history_signals = sum(op["outcome"] == "possible_signal" for op in historical)
                        reference = {"available": True, "days": len(history_days), "opportunities": len(historical), "signalCount": history_signals,
                                     "comparison": "more_signals" if len(signals) * len(historical) > history_signals * len(own) else "no_increase"}
                public["dimensions"].append({"key": domain, "label": label, "status": "possible_signal" if signals else "no_signal" if own else "not_assessable",
                    "opportunities": len(own), "signalCount": len(signals), "signalDays": len(signal_days), "caregiverSignalDays": len(proxy_days), "reference": reference})
                for source_mode, dates in (("self", signal_days), ("caregiver", proxy_days)):
                    if not dates:
                        continue
                    code = ("repeated_self_signal" if source_mode == "self" else "repeated_caregiver_signal") if len(dates) >= 3 else "single_signal"
                    public["alerts"].append({"id": _hash([member_id, domain, source_mode, code]), "level": "review" if len(dates) >= 3 else "info", "code": code,
                        "label": ALERT_LABELS[code], "domain": domain, "sourceMode": source_mode, "days": len(dates), "firstSeen": min(dates).isoformat(), "lastSeen": max(dates).isoformat()})
            acute = {}
            for record in all_records:
                for alert in record["urgent"]:
                    if first <= day(alert["at"]) <= today:
                        acute.setdefault(alert["code"], set()).add(day(alert["at"]))
            for code, dates in acute.items():
                public["alerts"].append({"id": _hash([member_id, code]), "level": "urgent", "code": code, "label": ALERT_LABELS[code], "domain": None,
                    "sourceMode": None, "days": len(dates), "firstSeen": min(dates).isoformat(), "lastSeen": max(dates).isoformat()})
            status = "urgent" if acute else "review" if any(a["level"] == "review" for a in public["alerts"]) else "observing" if valid else "insufficient"
            public.update(status=status, label=STATUS_LABELS[status], trend=list(trend.values()))
            public["alerts"].sort(key=lambda a: ({"urgent": 0, "review": 1, "info": 2}[a["level"]], a["code"]))
            members.append({"id": member_id, "name": member["name"], "chatAnalysis": public})
        return {"meta": {"analysisMode": "hermes_structured", "clinicalValidation": False, "modelEnabled": False, "reminderChannel": "web"}, "members": members}
