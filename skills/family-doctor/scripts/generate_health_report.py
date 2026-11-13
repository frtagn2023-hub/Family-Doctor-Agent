#!/usr/bin/env python3
"""Generate personal Markdown/PDF summaries with explicit uncertainty."""
import argparse
import html
import os
import re
import sys
import tempfile
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from health_store import (USER_PROFILE, MEDICATIONS_FILE, VITALS_FILE, SYMPTOMS_FILE,
                          REPORTS_DIR, CONSULTATIONS_DIR, ensure_self, load_json, normalize_medications,
                          normalize_vitals, atomic_write_text, record_context, refresh_file_views)
from add_consultation import is_own_consultation
from consultation_episodes import render_episode

UNKNOWN = "未知／未记录，不能视为无"
VITAL_LABELS = {"blood_pressure": "血压", "blood_sugar": "血糖", "weight": "体重",
                "temperature": "体温", "heart_rate": "心率", "spo2": "血氧饱和度"}
SOURCE_LABELS = {"user_report": "本人自述", "caregiver_report": "家属代述", "document": "提供的文档", "clinician_document": "医生文档",
                 "ai_assessment": "AI 初步分析", "other": "其他来源"}
CONFIRMATION_LABELS = {"unconfirmed": "未确认", "confirmed": "已确认", "user_confirmed": "本人已确认",
                       "reporter_confirmed": "信息提供者已核对",
                       "user_reported": "本人自述，未核实", "document_recorded": "按文档记录", "pending": "待核对",
                       "clinician_confirmed": "记录为医生确认", "unknown": "确认状态未知"}
RISK_LABELS = {"UNKNOWN": "待评估", "RED": "高风险", "ORANGE": "中风险", "YELLOW": "低风险",
               "GREEN": "绿色标记（不代表安全）", "待评估": "待评估", "高风险": "高风险", "中风险": "中风险",
               "低风险": "低风险", "🔴 高风险": "高风险", "🟠 中风险": "中风险", "🟡 低风险": "低风险"}


def code_label(value, labels, default):
    if value is None or value == "":
        return default
    return labels.get(str(value), f"未识别（{value}）")


def confirmation_text(value):
    return code_label(value, CONFIRMATION_LABELS, "未确认")


def report_timezone():
    """Use an explicit IANA zone when configured, otherwise the system local zone."""
    configured = os.environ.get("FAMILY_DOCTOR_TIMEZONE")
    if configured:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(configured)
        except (ImportError, KeyError, ValueError) as exc:
            raise ValueError("FAMILY_DOCTOR_TIMEZONE 需为有效 IANA 时区；此选项需要 Python 3.9+ 的 zoneinfo 和时区数据") from exc
    return datetime.now().astimezone().tzinfo


def display_time(value):
    """Format presentation only; preserve date-only precision and stored timestamps."""
    if not value:
        return "未知"
    if value in ("未知", "日期未知", "未提供", "未记录"):
        return str(value)
    try:
        if isinstance(value, str) and len(value) == 10:
            return date.fromisoformat(value).isoformat()
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        tz = report_timezone()
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        parsed = parsed.astimezone(tz)
        offset = parsed.strftime("%z")
        return parsed.strftime("%Y-%m-%d %H:%M") + f" UTC{offset[:3]}:{offset[3:]}"
    except (TypeError, ValueError):
        return f"未识别时间（{value}）"


def source_text(source):
    if not source:
        return "来源未记录"
    if isinstance(source, str):
        return code_label(source, SOURCE_LABELS, "来源未记录") if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", source) else source
    if isinstance(source, dict):
        pieces = []
        if source.get("type"):
            pieces.append(code_label(source["type"], SOURCE_LABELS, "来源类型未知"))
        if source.get("reference"):
            pieces.append(str(source["reference"]))
        if source.get("date"):
            pieces.append("日期：" + display_time(source["date"]))
        if source.get("page"):
            pieces.append(f"第 {source['page']} 页")
        return "；".join(pieces) or "来源未记录"
    return "来源未记录"


def fact_text(fact):
    if isinstance(fact, dict):
        value = next((fact[key] for key in ("value", "name", "allergen", "condition", "text") if fact.get(key)), "内容未记录")
        reaction = f"；反应：{fact['reaction']}" if fact.get("reaction") else ""
        dates = "".join(f"；{label}：{display_time(fact[key])}" for key, label in (("recorded_at", "记录时间"), ("confirmed_at", "确认时间")) if fact.get(key))
        return f"{value}{reaction}（来源：{source_text(fact.get('source'))}；确认状态：{confirmation_text(fact.get('confirmation_status'))}{dates}）"
    return f"{fact}（来源未记录；确认状态：未确认）"


def format_history(owner, key):
    status = owner.get("factStatus", {}).get(key, "unknown")
    if isinstance(status, dict):
        status = status.get("status", "unknown")
    denied = status in ("denied", "confirmed_none", "explicitly_denied")
    values = owner.get(key)
    if values:
        if not isinstance(values, list):
            values = [values]
        text = "；".join(fact_text(value) for value in values)
        return text + ("（与明确否认状态冲突，需核对）" if denied else "")
    if denied:
        evidence = owner.get("factStatusEvidence", {}).get(key, {})
        if isinstance(evidence, dict) and evidence.get("report_mode") == "proxy":
            return "家属 {} 明确表示无（代述，未视为本人确认）".format(evidence.get("reporter_name") or "未记录姓名")
        return "本人已明确否认（以档案确认时点为准）"
    if status == "not_asked":
        return "未询问（不能视为无）"
    if status == "present":
        return "标记为存在，但详情未记录，需核对"
    return UNKNOWN


def markdown_to_html(markdown_text):
    """Render a small escaped Markdown subset; links and raw HTML stay literal."""
    output = ['<!doctype html><html><head><meta charset="utf-8"></head><body>']
    in_list = False
    for raw in markdown_text.splitlines():
        line = raw.strip()
        escaped = html.escape(line, quote=True)
        escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
        if line.startswith("- "):
            if not in_list:
                output.append("<ul>")
                in_list = True
            output.append("<li>" + escaped[2:] + "</li>")
            continue
        if in_list:
            output.append("</ul>")
            in_list = False
        if not line:
            continue
        heading = re.match(r"^(#{1,4}) ", line)
        if heading:
            level = len(heading.group(1))
            output.append(f"<h{level}>{escaped[level + 1:]}</h{level}>")
        elif line == "---":
            output.append("<hr>")
        else:
            output.append("<p>" + escaped + "</p>")
    if in_list:
        output.append("</ul>")
    output.append("</body></html>")
    return "\n".join(output)


def deny_resource(url, *args, **kwargs):
    raise ValueError("报告 PDF 禁止读取网络或本地文件资源")


def generate_pdf(markdown_content, output_path):
    """Render to a private temporary file, then publish only a valid PDF."""
    try:
        from weasyprint import HTML, CSS
    except (ImportError, OSError) as exc:
        raise RuntimeError("PDF 依赖不可用，请安装 weasyprint 及系统字体/渲染依赖") from exc
    output_path = Path(output_path).expanduser()
    for directory in reversed([output_path.parent, *output_path.parent.parents]):
        directory.mkdir(mode=0o700, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".report-", suffix=".pdf", dir=output_path.parent)
    os.close(handle)
    try:
        css = CSS(string="""
            @page { size: A4; margin: 2cm; }
            body { font-family: "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif;
                   font-size: 11pt; line-height: 1.6; overflow-wrap: anywhere; }
            h1 { font-size: 20pt; } h2 { font-size: 15pt; margin-top: 18pt; }
            h3 { font-size: 13pt; } li { margin-bottom: 4pt; }
            h1, h2, h3, h4 { break-after: avoid; }
            li { break-inside: avoid; }
        """, url_fetcher=deny_resource)
        HTML(string=markdown_to_html(markdown_content), url_fetcher=deny_resource).write_pdf(temporary, stylesheets=[css])
        with open(temporary, "rb") as result:
            if result.read(5) != b"%PDF-":
                raise RuntimeError("渲染器未生成有效 PDF，输出未发布")
        os.replace(temporary, output_path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return output_path


def record_date(record, *keys, tz=None):
    tz = tz or report_timezone()
    for key in keys:
        value = record.get(key)
        if not value:
            continue
        try:
            if len(str(value)) == 10:
                return date.fromisoformat(value)
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=tz)
            return parsed.astimezone(tz).date()
        except (ValueError, TypeError):
            continue
    return None


def get_recent_data(days=30, role="self", profile=None, today=None):
    ensure_self(role)
    if days < 1:
        raise ValueError("近期天数必须大于 0")
    tz = report_timezone()
    today = today or datetime.now(tz).date()
    first = today - timedelta(days=days - 1)
    def recent(record, *keys):
        measured = record_date(record, *keys, tz=tz)
        return measured is not None and first <= measured <= today
    vitals = normalize_vitals(load_json(VITALS_FILE, default={"vitals": []}))["vitals"]
    symptoms = load_json(SYMPTOMS_FILE, default={"symptoms": []}).get("symptoms", [])
    profile = load_json(USER_PROFILE, default={}) if profile is None else profile
    consultations = profile.get("consultations", [])
    own = [c for c in consultations if is_own_consultation(c, profile.get("owner", {}))]
    selected = [c for c in own if recent(c, "date", "recordedAt")]
    selected_ids = {c.get("id") for c in selected}
    own_ids = {c.get("id") for c in own}
    episodes = [e for e in profile.get("consultation_episodes", [])
                if set(e.get("report_ids", [])) <= own_ids
                and selected_ids.intersection(e.get("report_ids", []))]
    return {
        "vitals": [v for v in vitals if recent(v, "measured_at", "recorded_at")],
        "symptoms": [s for s in symptoms if recent(s, "observed_at", "measured_at", "recorded_at")],
        "consultations": selected,
        "episodes": episodes,
        "all_own_consultations": own,
        "excluded_consultations": len(consultations) - len(own),
    }


def format_vitals(vitals_list):
    if not vitals_list:
        return "无近期记录（不代表指标正常）"
    lines = []
    for record in sorted(vitals_list, key=lambda item: str(item.get("measured_at") or item.get("recorded_at") or ""), reverse=True):
        value = record.get("value", "未知")
        if record.get("type") == "blood_pressure":
            value = f"{record.get('systolic', '未知')}/{record.get('diastolic', '未知')}"
        stamp = display_time(record.get("measured_at") or record.get("recorded_at"))
        unit = record.get("unit") or "单位未知，不能直接比较"
        label = code_label(record.get("type"), VITAL_LABELS, "未知指标")
        lines.append(f"- {stamp} | {label}：{value} {unit}（来源：{source_text(record.get('source'))}；确认状态：{confirmation_text(record.get('confirmation_status'))}）")
    return "\n".join(lines)


def format_medications(meds_list):
    if not meds_list:
        return "暂无已记录的在用药物（不代表未服药）"
    return "\n".join(f"- {med.get('name', '名称未知')} {med.get('dose') or '剂量未知'} {med.get('frequency') or '频次未知'}（来源：{source_text(med.get('source'))}；确认状态：{confirmation_text(med.get('confirmation_status'))}）" for med in meds_list)


def build_report(args, output_dir=None):
    ensure_self(getattr(args, "role", "self"))
    profile = load_json(USER_PROFILE, default={})
    owner = profile.get("owner", {})
    days = args.recent_days
    recent = get_recent_data(days, args.role, profile)
    meds = normalize_medications(load_json(MEDICATIONS_FILE, default={"medications": []}))["medications"]
    active = [m for m in meds if m["status"] == "active"]
    local_now = datetime.now(report_timezone())
    context = record_context(args.role)
    title_name = context.get("subject_name", "本人")
    lines = [f"# {title_name}就诊健康简报" if args.brief else f"# {title_name}就诊健康报告", "", f"生成时间：{display_time(local_now)}"]
    if args.for_specialist:
        lines.append(f"就诊科室：{args.for_specialist}")
    lines.extend(["", "## 基本信息", f"- 姓名：{owner.get('name') or '未提供'}", f"- 性别：{owner.get('gender') or '未知'}"])
    age = owner.get("age")
    try:
        birth = date.fromisoformat(owner.get("birthDate", ""))
        today = local_now.date()
        if birth <= today:
            age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
    except (TypeError, ValueError):
        pass
    lines.append(f"- 年龄：{str(age) + ' 岁' if age is not None and age != '' else '未知'}")
    lines.append(f"- 血型：{owner.get('bloodType') or '未知'}（未独立核验）")
    for label, key in (("过敏史", "allergies"), ("既往病史", "chronicConditions"), ("家族史", "familyHistory")):
        lines.append(f"- {label}：{format_history(owner, key)}")
    lines.extend(["", "## 当前用药", format_medications(active)])
    if owner.get("medications"):
        lines.extend(["", "## 档案内历史用药（当前状态未核实）", format_history(owner, "medications")])
    lines.extend(["", f"## 近期生命体征（含今天的近 {days} 个自然日，时区：{local_now.tzinfo}）", format_vitals(recent["vitals"])])
    lines.extend(["", "## 近期症状"])
    for symptom in recent["symptoms"]:
        lines.append(f"- {display_time(symptom.get('observed_at') or symptom.get('recorded_at'))}：{symptom.get('symptom', '未记录')}；严重程度：{symptom.get('severity') if symptom.get('severity') is not None else '未知'}；来源：{source_text(symptom.get('source'))}；确认状态：{confirmation_text(symptom.get('confirmation_status'))}")
    if not recent["symptoms"]:
        lines.append("无近期记录（不代表没有症状）")
    lines.extend(["", "## 近期问诊与健康事件"])
    grouped = set()
    if recent["episodes"]:
        lines.append("按最近补充问诊筛选；同一事件的早期来源一并保留。关联待核实的条目不等于已确认的独立发作。")
        for episode in recent["episodes"]:
            grouped.update(episode["report_ids"])
            content = render_episode(episode, recent["all_own_consultations"])
            def relocate_link(match):
                relative = match.group(1)
                target = (CONSULTATIONS_DIR / relative[3:]) if relative.startswith("../") else (CONSULTATIONS_DIR / "episodes" / relative)
                destination = os.path.relpath(target.resolve(), Path(output_dir or REPORTS_DIR).resolve())
                return "](" + quote(destination, safe="/.") + ")"
            content = re.sub(r"\]\((\.\./cons_[0-9a-f]{32}\.md|episode_[0-9a-f]{32}\.md)\)", relocate_link, content)
            from document_views import render_document_links
            content += render_document_links(profile, episode, output_dir or REPORTS_DIR)
            lines.extend(["", *[("##" + line) if line.startswith("#") else line for line in content.splitlines()]])
    for consultation in recent["consultations"]:
        if consultation.get("id") in grouped:
            continue
        lines.append(f"- {display_time(consultation.get('date'))}：{consultation.get('chiefComplaint', '主诉未知')}")
        hypothesis = consultation.get("aiHypothesis") or consultation.get("diagnosis")
        if hypothesis:
            lines.append(f"  - AI 提出的可能原因（待核实，不是医生诊断）：{hypothesis}")
        if consultation.get("clinicianDiagnosis"):
            lines.append(f"  - 医生诊断（按资料记录，未独立核验）：{consultation['clinicianDiagnosis']}")
        risk = code_label(consultation.get('riskLevel'), RISK_LABELS, '待评估')
        lines.append(f"  - 风险状态：{risk}；来源：{source_text(consultation.get('source'))}；确认状态：{confirmation_text(consultation.get('confirmation_status'))}")
    if not recent["consultations"]:
        lines.append("无近期可确认属于该成员的问诊记录")
    if recent["excluded_consultations"]:
        lines.append(f"有 {recent['excluded_consultations']} 条记录因档案归属未确认而未纳入。")
    documents = profile.get("records", {})
    confirmed_fields = []
    if isinstance(documents, dict):
        for entries in documents.values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if entry.get("status") != "confirmed" or entry.get("role") != "self":
                    continue
                differences = [source for source in entry.get("upload_sources", []) if source.get("duplicate_conflicts")]
                if differences:
                    confirmed_fields.append("- 资料 {} 有 {} 份重复上传存在转录或分类差异；以下保留原已核对字段，未用后续内容覆盖。".format(
                        entry.get("title") or entry.get("original_name") or "未命名", len(differences)))
                for field in entry.get("fields", []):
                    if field.get("confirmation_status") == "confirmed":
                        value = field.get("value", "未知")
                        numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
                        unit = field.get("unit") or ("单位未知" if numeric else "")
                        measurement = f"{value} {unit}".rstrip()
                        reference = f"；参考范围：{field['reference_range']}" if field.get("reference_range") else ""
                        confirmed_fields.append(f"- {field.get('name', '字段名未知')}：{measurement}{reference}；来源：{entry.get('original_name', '文件名未知')} 第 {field.get('source_page', '未知')} 页；观察日期：{display_time(field.get('observed_at'))}；资料确认时间：{display_time(field.get('confirmed_at') or entry.get('confirmed_at'))}；核对者：{field.get('reporter_name') or '用户'}")
    if confirmed_fields:
        lines.extend(["", "## 已核对的文档摘录（未独立核验医学结论）", *confirmed_fields])
    contact = owner.get("emergencyContact", {})
    lines.extend(["", "## 紧急联系人", f"- {contact.get('name') or '未记录'}；{contact.get('relationship') or '关系未知'}；{contact.get('phone') or '电话未知'}"])
    if args.reason:
        lines.extend(["", "## 本次就诊原因", args.reason])
    if not args.brief and profile.get("summary", {}).get("notableFindings"):
        lines.extend(["", "## 待核实的健康摘要"])
        lines.extend("- " + fact_text(finding) for finding in profile["summary"]["notableFindings"])
    lines.extend(["", "---", "本报告整理已记录资料，未独立核验。AI 提出的可能原因不等同于医生诊断；缺失记录不等同于没有问题。", ""])
    return "\n".join(lines)


def output_paths(args):
    requested = Path(args.output).expanduser() if args.output else None
    wants_pdf = args.pdf or args.pdf_only or (requested is not None and requested.suffix.lower() == ".pdf")
    wants_md = not args.pdf_only and not (requested is not None and requested.suffix.lower() == ".pdf" and not args.pdf)
    if requested is None:
        stem = "report-" + uuid.uuid4().hex if args.keep_history else "latest"
        base = REPORTS_DIR / stem
    else:
        if requested.suffix.lower() not in (".md", ".pdf"):
            raise ValueError("输出扩展名应为 .md 或 .pdf")
        base = requested.with_suffix("")
    return (Path(str(base) + ".md") if wants_md else None, Path(str(base) + ".pdf") if wants_pdf else None)


def generate_report(args):
    ensure_self(getattr(args, "role", "self"))
    md_path, pdf_path = output_paths(args)
    content = build_report(args, (md_path or pdf_path).parent)
    if md_path is not None:
        atomic_write_text(md_path, content)
        print(f"Markdown 报告已保存：{md_path}")
    if pdf_path is not None:
        generate_pdf(content, pdf_path)
        print(f"PDF 报告已保存：{pdf_path}")
    refresh_file_views(include_episodes=False)
    return str(md_path or pdf_path)


def build_parser():
    parser = argparse.ArgumentParser(description="生成本人就诊健康报告")
    parser.add_argument("--role", default="self")
    parser.add_argument("--brief", action="store_true")
    parser.add_argument("--output", "-o")
    parser.add_argument("--pdf", action="store_true", help="生成 Markdown 和 PDF")
    parser.add_argument("--pdf-only", action="store_true", help="只生成 PDF")
    parser.add_argument("--recent-days", type=int, default=30)
    parser.add_argument("--for-specialist")
    parser.add_argument("--reason")
    parser.add_argument("--keep-history", action="store_true")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        ensure_self(args.role)
        generate_report(args)
        return 0
    except Exception as exc:
        print(f"报告导出失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
