#!/usr/bin/env python3
"""
生命体征记录脚本
用法:
  python3 add_vital.py --type blood_pressure --systolic 130 --diastolic 85
  python3 get_vital_trends.py --type blood_pressure --days 30
"""

import json
import os
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path

WORKSPACE = Path.home() / ".hermes" / "health-doc"
HEALTH_DIR = WORKSPACE / "health-records"
VITALS_FILE = HEALTH_DIR / "vitals.json"

def load_vitals():
    """加载生命体征记录"""
    if VITALS_FILE.exists():
        with open(VITALS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"version": "1.0", "vitals": []}

def save_vitals(data):
    """保存生命体征记录"""
    HEALTH_DIR.mkdir(parents=True, exist_ok=True)
    with open(VITALS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def add_vital(args):
    """添加生命体征"""
    data = load_vitals()
    
    vital = {
        "id": f"vital_{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "type": args.type,
        "recorded_at": datetime.now().isoformat(),
    }
    
    if args.type == "blood_pressure":
        vital["systolic"] = args.systolic
        vital["diastolic"] = args.diastolic
        vital["value"] = f"{args.systolic}/{args.diastolic}"
    elif args.type == "heart_rate":
        vital["value"] = args.value
    elif args.type == "temperature":
        vital["value"] = args.value
    elif args.type == "blood_sugar":
        vital["value"] = args.value
        vital["fasting"] = args.fasting
    elif args.type == "weight":
        vital["value"] = args.value
    else:
        vital["value"] = args.value
    
    vital["notes"] = args.notes or ""
    
    data["vitals"].append(vital)
    save_vitals(data)
    
    print(f"✅ 已记录: {args.type} = {vital['value']}")

def get_vital_trends(args):
    """查看趋势"""
    data = load_vitals()
    
    days = args.days or 30
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    
    relevant = [v for v in data["vitals"] 
                if v["type"] == args.type and v["recorded_at"] >= cutoff]
    
    if not relevant:
        print(f"最近 {days} 天无 {args.type} 记录")
        return
    
    values = []
    print(f"\n📊 {args.type} 趋势 (最近 {days} 天)\n")
    
    for v in sorted(relevant, key=lambda x: x["recorded_at"]):
        date = v["recorded_at"][:10]
        val = v.get("value", "")
        values.append(v.get("systolic", 0) or v.get("value", 0))
        
        # 血压显示舒张压
        if args.type == "blood_pressure":
            val = f"{v.get('systolic', '')}/{v.get('diastolic', '')}"
        
        print(f"  {date} | {val}")
    
    if len(values) > 1:
        avg = sum(values) / len(values)
        print(f"\n  平均值: {avg:.1f}")

def main():
    parser = argparse.ArgumentParser(description="生命体征管理")
    subparsers = parser.add_subparsers(dest="command", help="子命令")
    
    # add
    add_parser = subparsers.add_parser("add", help="添加体征")
    add_parser.add_argument("--type", required=True, 
                          choices=["blood_pressure", "heart_rate", "temperature", "blood_sugar", "weight", "spo2"],
                          help="体征类型")
    add_parser.add_argument("--systolic", type=int, help="收缩压")
    add_parser.add_argument("--diastolic", type=int, help="舒张压")
    add_parser.add_argument("--value", type=float, help="数值")
    add_parser.add_argument("--fasting", action="store_true", help="空腹")
    add_parser.add_argument("--notes", default="", help="备注")
    
    # trends
    trends_parser = subparsers.add_parser("trends", help="查看趋势")
    trends_parser.add_argument("--type", required=True,
                             choices=["blood_pressure", "heart_rate", "temperature", "blood_sugar", "weight", "spo2"],
                             help="体征类型")
    trends_parser.add_argument("--days", type=int, default=30, help="天数")
    
    args = parser.parse_args()
    
    if args.command == "add":
        add_vital(args)
    elif args.command == "trends":
        get_vital_trends(args)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
