#!/usr/bin/env python3
"""
问诊记录脚本
用法: python3 add_consultation.py --patient "张三" --chief-complaint "头痛"
"""

import json
import os
import sys
import argparse
from datetime import datetime
from pathlib import Path

WORKSPACE = Path.home() / ".hermes" / "health-doc"
HEALTH_DIR = WORKSPACE / "health-records"
CONSULTATIONS_DIR = HEALTH_DIR / "consultations"
USER_PROFILE = HEALTH_DIR / "user-profile.json"

def load_user_profile():
    """加载用户画像"""
    if USER_PROFILE.exists():
        with open(USER_PROFILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"version": "2.0", "consultations": []}

def save_user_profile(data):
    """保存用户画像"""
    with open(USER_PROFILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def add_consultation(args):
    """创建问诊记录"""
    CONSULTATIONS_DIR.mkdir(parents=True, exist_ok=True)
    
    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"{date_str}-{args.chief_complaint[:10]}-{args.patient}.md"
    filepath = CONSULTATIONS_DIR / filename
    
    content = f"""# 问诊记录 - {date_str}

## 患者信息
- 姓名: {args.patient}
- 年龄: {args.age or '未记录'}
- 性别: {args.gender or '未记录'}
- 问诊角色: {args.role or '本人'}

## 主诉
{args.chief_complaint}

## 症状采集
- 部位: {args.location or '未记录'}
- 性质: {args.character or '未记录'}
- 严重程度: {args.severity or '未记录'}/10
- 持续时间: {args.duration or '未记录'}
- 诱因: {args.triggers or '未记录'}
- 缓解因素: {args.alleviates or '未记录'}
- 加重因素: {args.worsens or '未记录'}
- 伴随症状: {args.associated or '无'}

## 既往史
{args.history or '无特殊'}

## 用药史
{args.medications or '无'}

## 初步诊断
{args.diagnosis or '待确认'}

## 风险等级
{args.risk_level or '🟡 低风险'}

## 建议
### 就医建议
{args.medical_advice or '可暂观察'}

### 药物建议
{args.drug_advice or '无'}

### 自我护理
{args.self_care or '无'}

## 随访
- 复诊时间: {args.followup or '必要时'}
- 观察要点: {args.observation or '如症状加重请就医'}

---
记录时间: {datetime.now().isoformat()}
"""
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    
    # 更新 user-profile.json
    profile = load_user_profile()
    if "consultations" not in profile:
        profile["consultations"] = []
    
    profile["consultations"].append({
        "id": f"cons_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "date": date_str,
        "patient": args.patient,
        "chiefComplaint": args.chief_complaint,
        "file": str(filepath),
        "diagnosis": args.diagnosis,
        "riskLevel": args.risk_level,
        "recordedAt": datetime.now().isoformat()
    })
    
    # 更新 summary
    profile["summary"] = profile.get("summary", {})
    profile["summary"]["latestConsultation"] = date_str
    profile["summary"]["totalRecords"] = profile["summary"].get("totalRecords", 0) + 1
    
    save_user_profile(profile)
    
    print(f"✅ 问诊记录已保存: {filename}")
    print(f"   患者: {args.patient}")
    print(f"   主诉: {args.chief_complaint}")

def list_consultations(args):
    """列出问诊记录"""
    CONSULTATIONS_DIR.mkdir(parents=True, exist_ok=True)
    
    files = sorted(CONSULTATIONS_DIR.glob("*.md"), reverse=True)
    
    if not files:
        print("暂无问诊记录")
        return
    
    print(f"\n📋 问诊记录 (共 {len(files)} 条)\n")
    for f in files:
        # 读取文件前几行获取信息
        with open(f, 'r', encoding='utf-8') as file:
            lines = file.readlines()
            patient = ""
            chief = ""
            for line in lines:
                if line.startswith("- 姓名:"):
                    patient = line.split(":")[1].strip()
                if line.startswith("## 主诉"):
                    break
        
        date = f.name[:10]
        print(f"  {date} | {patient} | {f.name[11:-3]}")

def main():
    parser = argparse.ArgumentParser(description="问诊记录管理")
    subparsers = parser.add_subparsers(dest="command", help="子命令")
    
    # add
    add_parser = subparsers.add_parser("add", help="添加问诊记录")
    add_parser.add_argument("--patient", required=True, help="患者姓名")
    add_parser.add_argument("--age", help="年龄")
    add_parser.add_argument("--gender", help="性别")
    add_parser.add_argument("--role", default="本人", help="问诊角色")
    add_parser.add_argument("--chief-complaint", required=True, help="主诉")
    add_parser.add_argument("--location", help="部位")
    add_parser.add_argument("--character", help="性质")
    add_parser.add_argument("--severity", help="严重程度")
    add_parser.add_argument("--duration", help="持续时间")
    add_parser.add_argument("--triggers", help="诱因")
    add_parser.add_argument("--alleviates", help="缓解因素")
    add_parser.add_argument("--worsens", help="加重因素")
    add_parser.add_argument("--associated", help="伴随症状")
    add_parser.add_argument("--history", help="既往史")
    add_parser.add_argument("--medications", help="用药史")
    add_parser.add_argument("--diagnosis", help="初步诊断")
    add_parser.add_argument("--risk-level", help="风险等级")
    add_parser.add_argument("--medical-advice", help="就医建议")
    add_parser.add_argument("--drug-advice", help="药物建议")
    add_parser.add_argument("--self-care", help="自我护理")
    add_parser.add_argument("--followup", help="随访时间")
    add_parser.add_argument("--observation", help="观察要点")
    
    # list
    subparsers.add_parser("list", help="列出问诊记录")
    
    args = parser.parse_args()
    
    if args.command == "add":
        add_consultation(args)
    elif args.command == "list":
        list_consultations(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
