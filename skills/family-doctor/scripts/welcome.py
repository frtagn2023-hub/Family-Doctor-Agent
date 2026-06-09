#!/usr/bin/env python3
"""
首次使用引导脚本
功能：
1. 收集用户基础信息（姓名、性别、年龄）
2. 初始化 USER.md
3. 初始化 user-profile.json
4. 介绍功能
"""

import json
import os
from datetime import datetime
from pathlib import Path

WORKSPACE = Path.home() / ".hermes" / "health-doc"
USER_MD = WORKSPACE / "USER.md"
USER_PROFILE = WORKSPACE / "health-records" / "user-profile.json"

def welcome_message():
    """欢迎消息"""
    print("=" * 50)
    print("👨‍⚕️  欢迎使用 AI 家庭私人医生！")
    print("=" * 50)
    print()
    print("我，你的 AI 家庭私人医生，可以帮你：")
    print("  🩺 分析症状，给出诊断建议")
    print("  💊 提供药物调整建议")
    print("  📋 管理健康档案")
    print("  🏥 生成就诊健康报告")
    print()
    print("为了更好地为你服务，让我先了解一下你的基本信息")
    print()

def collect_user_info():
    """收集用户信息"""
    print("-" * 30)
    print("📝 请填写以下信息：")
    print("-" * 30)
    
    info = {}
    
    # 姓名
    while True:
        name = input("👤 请输入你的名字：").strip()
        if name:
            info["name"] = name
            break
        print("⚠️ 请输入名字")
    
    # 性别
    while True:
        print("\n⚧️ 请选择性别：")
        print("  1. 男")
        print("  2. 女")
        print("  3. 其他")
        print("  4. 不愿透露")
        gender_choice = input("请输入选项（1-4）：").strip()
        
        if gender_choice == "1":
            info["gender"] = "男"
            break
        elif gender_choice == "2":
            info["gender"] = "女"
            break
        elif gender_choice == "3":
            info["gender"] = "其他"
            break
        elif gender_choice == "4":
            info["gender"] = ""
            break
        print("⚠️ 请输入 1-4 之间的数字")
    
    # 年龄
    while True:
        age = input("\n🎂 请输入你的年龄：").strip()
        if age.isdigit() and 0 < int(age) < 150:
            info["age"] = age
            break
        elif age == "":
            print("⚠️ 请输入年龄（数字）")
        else:
            print("⚠️ 请输入有效的年龄（数字）")
    
    # 称呼
    nickname = input("\n📛 你希望我怎么称呼你？[直接回车使用名字]：").strip()
    info["nickname"] = nickname if nickname else info["name"]
    
    return info

def save_to_user_md(info):
    """保存到 USER.md"""
    content = f"""# USER.md - About Your Human

- **Name:** {info['name']}
- **What to call them:** {info['nickname']}
- **Pronouns:** {info.get('gender', '')}
- **Timezone:** Asia/Shanghai
- **Notes:**

## Context

你的 AI 家庭私人医生档案。
"""
    
    with open(USER_MD, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"\n✅ 已保存到 USER.md")

def save_to_user_profile(info):
    """保存到 user-profile.json"""
    # 确保目录存在
    USER_PROFILE.parent.mkdir(parents=True, exist_ok=True)
    
    # 读取现有文件或创建新的
    if USER_PROFILE.exists():
        with open(USER_PROFILE, 'r', encoding='utf-8') as f:
            profile = json.load(f)
    else:
        profile = {
            "version": "2.0",
            "lastUpdated": datetime.now().isoformat(),
            "owner": {},
            "summary": {
                "totalRecords": 0,
                "totalTypes": 0,
                "latestRecord": None,
                "latestConsultation": None,
                "notableFindings": []
            },
            "records": {},
            "typeRegistry": {},
            "consultations": []
        }
    
    # 更新 owner 信息
    profile["owner"]["name"] = info["name"]
    profile["owner"]["gender"] = info.get("gender", "")
    profile["owner"]["birthDate"] = ""
    profile["owner"]["age"] = info.get("age", "")
    profile["owner"]["nickname"] = info.get("nickname", "")
    profile["owner"]["allergies"] = []
    profile["owner"]["chronicConditions"] = []
    profile["owner"]["medications"] = []
    profile["owner"]["familyHistory"] = []
    profile["owner"]["emergencyContact"] = {
        "name": "",
        "relationship": "",
        "phone": ""
    }
    
    profile["lastUpdated"] = datetime.now().isoformat()
    
    with open(USER_PROFILE, 'w', encoding='utf-8') as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 已保存到 user-profile.json")

def show_features():
    """展示功能"""
    print("\n" + "=" * 50)
    print("📖 功能介绍")
    print("=" * 50)
    print("""
你可以这样使用我：

🏥 问诊咨询
   "我最近头痛"
   "肚子疼怎么办"

📄 上传健康档案
   上传体检报告、检查单，我会帮你分析

📊 每日健康数据
   上传 Apple Health 数据，我可以帮你分析趋势

💊 药物管理
   告诉我你正在服用的药物，我会帮你记录

🩺 生命体征
   记录你的血压、血糖等数据

📋 健康报告
   告诉我"生成健康报告"，我会整理你的健康信息

🔍 医学查询
   问我任何健康相关问题，我会搜索权威资料

⚠️ 注意事项
   - 我不是医生，不能替代专业诊断
   - 紧急情况请立即就医
   - 所有建议仅供参考
""")
    print("=" * 50)
    print("\n👨‍⚕️ 准备就绪！有什么可以帮你的？")
    print()

def main():
    # 检查是否已初始化
    if USER_MD.exists():
        with open(USER_MD, 'r', encoding='utf-8') as f:
            content = f.read()
            if "**Name:**" in content and "未填写" not in content:
                # 检查是否有实际内容
                for line in content.split('\n'):
                    if line.startswith('- **Name:**') and line.split(':')[1].strip():
                        print("你已经完成初始化设置了！")
                        print("直接开始使用吧～")
                        return
    
    # 引导流程
    welcome_message()
    info = collect_user_info()
    save_to_user_md(info)
    save_to_user_profile(info)
    show_features()

if __name__ == "__main__":
    main()
