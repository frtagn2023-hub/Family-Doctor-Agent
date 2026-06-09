#!/usr/bin/env python3
"""
症状记录脚本
用法: python3 add_symptom.py --symptom "头痛" --severity 6 --duration "2天"
"""

import json
import os
import sys
import argparse
from datetime import datetime
from pathlib import Path

# 路径配置
WORKSPACE = Path.home() / ".hermes" / "health-doc"
HEALTH_DIR = WORKSPACE / "health-records"
SYMPTOMS_FILE = HEALTH_DIR / "symptoms.json"

def load_symptoms():
    """加载现有症状记录"""
    if SYMPTOMS_FILE.exists():
        with open(SYMPTOMS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"version": "1.0", "symptoms": []}

def save_symptoms(data):
    """保存症状记录"""
    HEALTH_DIR.mkdir(parents=True, exist_ok=True)
    with open(SYMPTOMS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def add_symptom(args):
    """添加症状"""
    data = load_symptoms()
    
    symptom = {
        "id": f"sym_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "symptom": args.symptom,
        "severity": args.severity,
        "duration": args.duration,
        "location": args.location,
        "character": args.character,
        "triggers": args.triggers,
        "alleviates": args.alleviates,
        "worsens": args.worsens,
        "associated": args.associated,
        "notes": args.notes,
        "recorded_at": datetime.now().isoformat()
    }
    
    data["symptoms"].append(symptom)
    save_symptoms(data)
    
    print(f"✅ 已记录症状: {args.symptom}")
    print(f"   严重程度: {args.severity}/10")
    print(f"   持续时间: {args.duration}")

def list_symptoms(args):
    """列出症状记录"""
    data = load_symptoms()
    
    if not data["symptoms"]:
        print("暂无症状记录")
        return
    
    print(f"\n📋 症状记录 (共 {len(data['symptoms'])} 条)\n")
    for s in reversed(data["symptoms"]):
        print(f"  {s['recorded_at'][:10]} | {s['symptom']} | 严重程度: {s['severity']}/10 | 持续: {s.get('duration', '未记录')}")

def main():
    parser = argparse.ArgumentParser(description="症状记录管理")
    subparsers = parser.add_subparsers(dest="command", help="子命令")
    
    # add 子命令
    add_parser = subparsers.add_parser("add", help="添加症状")
    add_parser.add_argument("--symptom", required=True, help="症状名称")
    add_parser.add_argument("--severity", type=int, required=True, help="严重程度 1-10")
    add_parser.add_argument("--duration", default="未记录", help="持续时间")
    add_parser.add_argument("--location", default="未记录", help="疼痛部位")
    add_parser.add_argument("--character", default="未记录", help="性质")
    add_parser.add_argument("--triggers", default="未记录", help="诱因")
    add_parser.add_argument("--alleviates", default="未记录", help="缓解因素")
    add_parser.add_argument("--worsens", default="未记录", help="加重因素")
    add_parser.add_argument("--associated", default="无", help="伴随症状")
    add_parser.add_argument("--notes", default="", help="备注")
    
    # list 子命令
    list_parser = subparsers.add_parser("list", help="列出所有症状")
    
    args = parser.parse_args()
    
    if args.command == "add":
        add_symptom(args)
    elif args.command == "list":
        list_symptoms(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
