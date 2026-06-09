#!/usr/bin/env python3
"""
药物管理脚本
用法: 
  python3 add_medication.py --name "厄贝沙坦" --dose "150mg" --freq "qd"
  python3 list_medications.py
  python3 check_interactions.py
"""

import json
import os
import sys
import argparse
from datetime import datetime
from pathlib import Path

WORKSPACE = Path.home() / ".hermes" / "health-doc"
HEALTH_DIR = WORKSPACE / "health-records"
MEDICATIONS_FILE = HEALTH_DIR / "medications.json"

# 常见药物相互作用（简化版）
DRUG_INTERACTIONS = {
    ("阿司匹林", "华法林"): "增加出血风险",
    ("阿司匹林", "布洛芬"): "降低阿司匹林心脏保护作用",
    ("厄贝沙坦", "钾补充剂"): "可能引起高钾血症",
    ("地平类", "地尔硫卓"): "可能引起低血压",
    ("他汀类", "红霉素"): "增加肌病风险",
    (" Metformin", "酒精"): "增加乳酸酸中毒风险"
}

def load_medications():
    """加载药物记录"""
    if MEDICATIONS_FILE.exists():
        with open(MEDICATIONS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"version": "1.0", "medications": []}

def save_medications(data):
    """保存药物记录"""
    HEALTH_DIR.mkdir(parents=True, exist_ok=True)
    with open(MEDICATIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def add_medication(args):
    """添加药物"""
    data = load_medications()
    
    med = {
        "id": f"med_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "name": args.name,
        "dose": args.dose,
        "frequency": args.freq,
        "purpose": args.purpose,
        "start_date": args.start_date or datetime.now().strftime("%Y-%m-%d"),
        "prescribed_by": args.prescriber,
        "notes": args.notes,
        "recorded_at": datetime.now().isoformat()
    }
    
    data["medications"].append(med)
    save_medications(data)
    
    print(f"✅ 已添加药物: {args.name} {args.dose} {args.freq}")

def list_medications(args):
    """列出药物"""
    data = load_medications()
    
    if not data["medications"]:
        print("暂无药物记录")
        return
    
    active = [m for m in data["medications"] if m.get("status") != "停用"]
    
    print(f"\n💊 当前用药 (共 {len(active)} 种)\n")
    for m in active:
        print(f"  • {m['name']} {m['dose']} {m['frequency']}")
        if m.get('purpose'):
            print(f"    用途: {m['purpose']}")
        if m.get('prescribed_by'):
            print(f"    开药: {m['prescribed_by']}")
        print()

def check_interactions(args):
    """检查药物相互作用"""
    data = load_medications()
    meds = [m["name"] for m in data["medications"] if m.get("status") != "停用"]
    
    if len(meds) < 2:
        print("当前药物少于2种，无需检查相互作用")
        return
    
    print("\n🔍 药物相互作用检查\n")
    
    found = False
    for i, med1 in enumerate(meds):
        for med2 in meds[i+1:]:
            for (d1, d2), interaction in DRUG_INTERACTIONS.items():
                if (d1 in med1 and d2 in med2) or (d2 in med1 and d1 in med2):
                    print(f"  ⚠️ {med1} + {med2}")
                    print(f"     {interaction}")
                    print()
                    found = True
    
    if not found:
        print("  ✅ 未发现已知相互作用")
    
    print("\n⚠️ 建议：用药调整前请咨询医生或药师")

def main():
    parser = argparse.ArgumentParser(description="药物管理")
    subparsers = parser.add_subparsers(dest="command", help="子命令")
    
    # add
    add_parser = subparsers.add_parser("add", help="添加药物")
    add_parser.add_argument("--name", required=True, help="药物名称")
    add_parser.add_argument("--dose", required=True, help="剂量")
    add_parser.add_argument("--freq", required=True, help="频率 (如 qd, bid)")
    add_parser.add_argument("--purpose", default="", help="用途")
    add_parser.add_argument("--start-date", help="开始日期")
    add_parser.add_argument("--prescriber", default="", help="开药医生")
    add_parser.add_argument("--notes", default="", help="备注")
    
    # list
    subparsers.add_parser("list", help="列出药物")
    
    # check
    subparsers.add_parser("check", help="检查相互作用")
    
    args = parser.parse_args()
    
    if args.command == "add":
        add_medication(args)
    elif args.command == "list":
        list_medications(args)
    elif args.command == "check":
        check_interactions(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
