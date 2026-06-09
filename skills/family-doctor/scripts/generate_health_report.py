#!/usr/bin/env python3
"""
健康报告生成脚本
用法: 
  python3 generate_health_report.py                    # 生成报告（默认只保留最新一份）
  python3 generate_health_report.py --pdf               # 同时生成 PDF
  python3 generate_health_report.py --pdf-only          # 只生成 PDF
  python3 generate_health_report.py --keep-history     # 保留历史版本
  python3 generate_health_report.py --brief           # 生成简洁版
  python3 generate_health_report.py --output ~/Desktop/report.md
  python3 generate_health_report.py --for-specialist 心内科  # 专科版
"""

import json
import os
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path

# 路径配置
WORKSPACE = Path.home() / ".hermes" / "health-doc"
HEALTH_DIR = WORKSPACE / "health-records"
REPORTS_DIR = HEALTH_DIR / "reports"  # 健康报告目录
USER_PROFILE = HEALTH_DIR / "user-profile.json"
MEDICATIONS_FILE = HEALTH_DIR / "medications.json"
VITALS_FILE = HEALTH_DIR / "vitals.json"
SYMPTOMS_FILE = HEALTH_DIR / "symptoms.json"

# 默认只保留最新报告的路径
LATEST_REPORT = REPORTS_DIR / "latest.md"


def load_json(filepath):
    """加载 JSON 文件"""
    if filepath.exists():
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def generate_pdf(markdown_content, output_path):
    """将 Markdown 报告转换为 PDF"""
    try:
        from weasyprint import HTML, CSS
        
        # 简单的 Markdown 转 HTML（基础支持）
        html_content = markdown_to_html(markdown_content)
        
        # 中文字体 CSS
        css = CSS(string="""
            @page {
                size: A4;
                margin: 2cm;
            }
            body {
                font-family: "PingFang SC", "Microsoft YaHei", "SimHei", sans-serif;
                font-size: 12pt;
                line-height: 1.6;
                color: #333;
            }
            h1 {
                font-size: 24pt;
                color: #1a5f7a;
                text-align: center;
                margin-bottom: 20pt;
            }
            h2 {
                font-size: 16pt;
                color: #2c3e50;
                border-bottom: 1px solid #ddd;
                padding-bottom: 5pt;
                margin-top: 15pt;
            }
            h3 {
                font-size: 14pt;
                color: #34495e;
            }
            ul {
                margin-left: 20pt;
            }
            li {
                margin-bottom: 5pt;
            }
            .footer {
                font-size: 9pt;
                color: #999;
                text-align: center;
                margin-top: 30pt;
                border-top: 1px solid #eee;
                padding-top: 10pt;
            }
            .info-label {
                font-weight: bold;
                color: #1a5f7a;
            }
            table {
                width: 100%;
                border-collapse: collapse;
                margin: 10pt 0;
            }
            th, td {
                border: 1px solid #ddd;
                padding: 8pt;
                text-align: left;
            }
            th {
                background-color: #f5f5f5;
            }
        """)
        
        # 生成 PDF
        HTML(string=html_content).write_pdf(output_path, stylesheets=[css])
        print(f"✅ PDF 报告已生成: {output_path}")
        return True
        
    except ImportError:
        print("⚠️ weasyprint 未安装或缺少依赖")
        print("   方案1: brew install pango (macOS)")
        print("   方案2: pip install weasyprint")
        print("   也可使用 --no-pdf 跳过 PDF 生成")
        return False
    except Exception as e:
        print(f"⚠️ PDF 生成失败: {e}")
        return False


def markdown_to_html(markdown_text):
    """简单的 Markdown 转 HTML（基础支持）"""
    import re
    
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
    </head>
    <body>
    """
    
    lines = markdown_text.split('\n')
    in_list = False
    
    for line in lines:
        line = line.strip()
        if not line:
            if in_list:
                html += "</ul>\n"
                in_list = False
            continue
        
        # 标题
        if line.startswith('# '):
            if in_list:
                html += "</ul>\n"
                in_list = False
            html += f"<h1>{line[2:]}</h1>\n"
        elif line.startswith('## '):
            if in_list:
                html += "</ul>\n"
                in_list = False
            html += f"<h2>{line[3:]}</h2>\n"
        elif line.startswith('### '):
            if in_list:
                html += "</ul>\n"
                in_list = False
            html += f"<h3>{line[4:]}</h3>\n"
        # 列表
        elif line.startswith('- '):
            if not in_list:
                html += "<ul>\n"
                in_list = True
            # 处理粗体
            content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', line[2:])
            html += f"<li>{content}</li>\n"
        # 段落
        else:
            if in_list:
                html += "</ul>\n"
                in_list = False
            # 处理粗体
            content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', line)
            html += f"<p>{content}</p>\n"
    
    if in_list:
        html += "</ul>\n"
    
    html += """
    <div class="footer">
        <p>本报告由 AI 家庭医生生成，仅供参考。</p>
        <p>建议就诊时携带既往检查资料和用药清单。</p>
    </div>
    </body>
    </html>
    """
    
    return html
    """加载 JSON 文件"""
    if filepath.exists():
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def get_recent_data(days=30):
    """获取近期数据"""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    recent = {"vitals": [], "symptoms": [], "consultations": []}
    
    # 近期生命体征
    vitals = load_json(VITALS_FILE)
    if vitals and "vitals" in vitals:
        recent["vitals"] = [v for v in vitals["vitals"] if v.get("recorded_at", "") >= cutoff]
    
    # 近期症状
    symptoms = load_json(SYMPTOMS_FILE)
    if symptoms and "symptoms" in symptoms:
        recent["symptoms"] = [s for s in symptoms["symptoms"] if s.get("recorded_at", "") >= cutoff]
    
    # 近期问诊
    profile = load_json(USER_PROFILE)
    if profile and "consultations" in profile:
        recent["consultations"] = [c for c in profile["consultations"] if c.get("date", "") >= cutoff]
    
    return recent

def format_vitals(vitals_list):
    """格式化生命体征"""
    if not vitals_list:
        return "无记录"
    
    # 按类型分组
    by_type = {}
    for v in vitals_list:
        t = v.get("type", "other")
        if t not in by_type:
            by_type[t] = []
        by_type[t].append(v)
    
    lines = []
    for vtype, records in by_type.items():
        lines.append(f"\n#### {vtype.replace('_', ' ').title()}")
        for r in sorted(records, key=lambda x: x.get("recorded_at", ""), reverse=True)[:5]:
            date = r.get("recorded_at", "")[:10]
            val = r.get("value", "")
            if vtype == "blood_pressure":
                val = f"{r.get('systolic', '')}/{r.get('diastolic', '')} mmHg"
            lines.append(f"- {date}: {val}")
    
    return "\n".join(lines) if lines else "无记录"

def format_medications(meds_list):
    """格式化用药情况"""
    if not meds_list:
        return "无记录"
    
    lines = []
    for m in meds_list:
        name = m.get("name", "")
        dose = m.get("dose", "")
        freq = m.get("frequency", "")
        purpose = m.get("purpose", "")
        
        line = f"- {name} {dose} {freq}"
        if purpose:
            line += f" （{purpose}）"
        lines.append(line)
    
    return "\n".join(lines)

def generate_report(args):
    """生成健康报告"""
    # 加载用户画像
    profile = load_json(USER_PROFILE)
    owner = profile.get("owner", {}) if profile else {}
    
    # 加载近期数据
    recent_days = args.recent_days or 30
    recent = get_recent_data(recent_days)
    
    # 加载药物
    medications = load_json(MEDICATIONS_FILE)
    meds_list = medications.get("medications", []) if medications else []
    
    # 构建报告
    report = []
    
    # === 标题 ===
    if args.brief:
        report.append("# 📋 就诊健康简报\n")
    else:
        report.append("# 🏥 就诊健康报告\n")
    
    report.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    if args.for_specialist:
        report.append(f"**就诊科室**: {args.for_specialist}")
    report.append("")
    
    # === 基本信息 ===
    report.append("## 👤 基本信息\n")
    name = owner.get("name", "未记录") or "未记录"
    birth = owner.get("birthDate", "")
    gender = owner.get("gender", "未记录") or "未记录"
    blood = owner.get("bloodType", "") or "未记录"
    
    # 计算年龄
    age = ""
    if birth:
        try:
            birth_date = datetime.strptime(birth, "%Y-%m-%d")
            age = (datetime.now() - birth_date).days // 365
            age = f"{age}岁"
        except:
            pass
    
    report.append(f"- **姓名**: {name}")
    if age:
        report.append(f"- **年龄**: {age}")
    if gender and gender != "未记录":
        report.append(f"- **性别**: {gender}")
    if blood and blood != "未记录":
        report.append(f"- **血型**: {blood}")
    
    # 过敏史
    allergies = owner.get("allergies", [])
    if allergies:
        report.append(f"- **过敏史**: {', '.join(allergies)}")
    else:
        report.append(f"- **过敏史**: 无")
    
    # 既往病史
    chronic = owner.get("chronicConditions", [])
    if chronic:
        report.append(f"- **既往病史**: {', '.join(chronic)}")
    else:
        report.append(f"- **既往病史**: 无")
    
    report.append("")
    
    # === 用药情况 ===
    report.append("## 💊 当前用药\n")
    if meds_list:
        active = [m for m in meds_list if m.get("status") != "停用"]
        report.append(format_medications(active))
    else:
        report.append("无记录")
    report.append("")
    
    # === 近期生命体征 ===
    report.append(f"## 📊 近期生命体征（近{recent_days}天）\n")
    report.append(format_vitals(recent["vitals"]))
    report.append("")
    
    # === 近期症状 ===
    if recent["symptoms"]:
        report.append(f"## 🤒 近期症状（近{recent_days}天）\n")
        for s in sorted(recent["symptoms"], key=lambda x: x.get("recorded_at", ""), reverse=True):
            date = s.get("recorded_at", "")[:10]
            symptom = s.get("symptom", "")
            severity = s.get("severity", "")
            duration = s.get("duration", "")
            location = s.get("location", "")
            
            report.append(f"- **{date}**: {symptom}")
            if severity:
                report.append(f"  - 严重程度: {severity}/10")
            if duration and duration != "未记录":
                report.append(f"  - 持续时间: {duration}")
            if location and location != "未记录":
                report.append(f"  - 部位: {location}")
        report.append("")
    
    # === 近期就诊记录 ===
    if recent["consultations"]:
        report.append(f"## 🏥 近期就诊（近{recent_days}天）\n")
        for c in sorted(recent["consultations"], key=lambda x: x.get("date", ""), reverse=True):
            date = c.get("date", "")
            chief = c.get("chiefComplaint", "")
            diag = c.get("diagnosis", "")
            
            report.append(f"- **{date}**: {chief}")
            if diag and diag != "待确认":
                report.append(f"  - 诊断: {diag}")
        report.append("")
    
    # === 家族史 ===
    family = owner.get("familyHistory", [])
    if family:
        report.append("## 👨‍👩‍👧 家族史\n")
        for f in family:
            report.append(f"- {f}")
        report.append("")
    
    # === 急诊联系人 ===
    emergency = owner.get("emergencyContact", {})
    if emergency.get("name"):
        report.append("## 🚨 急诊联系人\n")
        report.append(f"- **姓名**: {emergency.get('name', '')}")
        report.append(f"- **关系**: {emergency.get('relationship', '')}")
        report.append(f"- **电话**: {emergency.get('phone', '')}")
        report.append("")
    
    # === 就诊原因 ===
    if args.reason:
        report.append("## 📝 本次就诊原因\n")
        report.append(args.reason)
        report.append("")
    
    # === 简洁版不显示以下内容 ===
    if not args.brief:
        # === 重要发现 ===
        if profile and profile.get("summary", {}).get("notableFindings"):
            report.append("## ⚠️ 重要健康发现\n")
            for finding in profile["summary"]["notableFindings"]:
                report.append(f"- {finding}")
            report.append("")
        
        # === 需要咨询医生的问题 ===
        report.append("## ❓ 希望咨询医生的问题\n")
        report.append("（请根据您的具体情况填写）\n")
        report.append("")
    
    # === 页脚 ===
    report.append("---\n")
    report.append("*本报告由 AI 家庭医生生成，仅供参考。*")
    report.append("*建议就诊时携带既往检查资料和用药清单。*")
    
    # 输出
    report_text = "\n".join(report)
    
    # 决定保存路径
    save_path = None
    
    if args.output:
        # 用户指定了输出路径
        save_path = Path(args.output).expanduser()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, 'w', encoding='utf-8') as f:
            f.write(report_text)
        print(f"✅ 报告已保存到: {save_path}")
        print()
        print(report_text)
    else:
        # 默认行为：只保存最新一份到固定位置
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        
        if args.keep_history:
            # 用户要求保留历史版本
            timestamp = datetime.now().strftime("%Y%m%d-%H%M")
            save_path = REPORTS_DIR / f"report-{timestamp}.md"
            with open(save_path, 'w', encoding='utf-8') as f:
                f.write(report_text)
            print(f"✅ 报告已保存到: {REPORTS_DIR}/")
            print(f"   历史版本: {save_path.name}")
        else:
            # 默认：只保留最新一份（覆盖）
            with open(LATEST_REPORT, 'w', encoding='utf-8') as f:
                f.write(report_text)
            print(f"✅ 报告已更新（只保留最新一份）")
            print(f"   保存位置: {LATEST_REPORT}")
        
        print()
        print(report_text)
    
    # 返回报告路径供后续使用
    return str(save_path) if save_path else None

def main():
    parser = argparse.ArgumentParser(description="生成就诊健康报告")
    parser.add_argument("--brief", action="store_true", help="简洁版报告")
    parser.add_argument("--output", "-o", help="输出文件路径 (.md 或 .pdf)")
    parser.add_argument("--pdf", action="store_true", help="同时生成 PDF")
    parser.add_argument("--pdf-only", action="store_true", help="只生成 PDF")
    parser.add_argument("--recent-days", type=int, default=30, help="近期数据天数（默认30天）")
    parser.add_argument("--for-specialist", help="就诊科室（如：心内科）")
    parser.add_argument("--reason", help="本次就诊原因")
    parser.add_argument("--keep-history", action="store_true", help="保留历史版本（默认只保留最新一份）")
    
    args = parser.parse_args()
    
    # 生成 Markdown 报告
    md_path = generate_report(args)
    
    # 生成 PDF
    if args.pdf or args.pdf_only or (args.output and args.output.endswith('.pdf')):
        # 确定 PDF 输出路径
        if args.output:
            if args.output.endswith('.pdf'):
                pdf_path = args.output
            else:
                pdf_path = args.output.replace('.md', '.pdf')
        else:
            pdf_path = str(REPORTS_DIR / f"latest.pdf")
        
        # 读取生成的 Markdown 内容
        if md_path:
            with open(md_path, 'r', encoding='utf-8') as f:
                md_content = f.read()
            generate_pdf(md_content, pdf_path)
        elif args.pdf_only:
            # 如果没有生成 Markdown，直接使用默认内容生成 PDF
            # 这里可以添加一个获取默认内容的方法
            print("⚠️ 先生成 Markdown 报告后再生成 PDF")


if __name__ == "__main__":
    main()
