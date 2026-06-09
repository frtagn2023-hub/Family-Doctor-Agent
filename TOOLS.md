# TOOLS.md - 本地工具配置

## 技能工具

本健康助手使用的技能和工具：

### 核心技能

| 技能 | 用途 |
|-----|------|
| family-doctor | 7步问诊工作流、诊断建议、药物调整 |
| cn-web-search | 中文网页搜索（Wikipedia 等） |
| knowledge-graph | 知识图谱管理 |
| pdf-text-extract | PDF 文本提取 |
| ocr-local | 本地 OCR（图片转文字） |

### 工具使用

| 场景 | 工具 |
|-----|------|
| PDF 附件 | pdf-text-extract |
| 图片附件 | ocr-local |
| 医学知识查询 | web_fetch (Wikipedia) |
| 健康档案 | health-records/user-profile.json |

---

## 问诊脚本

位置：`skills/family-doctor/scripts/`

| 脚本 | 功能 |
|-----|------|
| welcome.py | 首次使用引导（收集基本信息） |
| add_symptom.py | 记录症状 |
| add_medication.py | 添加药物 |
| add_vital.py | 记录生命体征 |
| add_consultation.py | 创建问诊记录 |
| list_medications.py | 列出当前用药 |
| check_interactions.py | 检查药物相互作用 |
| get_vital_trends.py | 查看体征趋势 |
| generate_health_report.py | 生成就诊报告 |
| generate_emergency_card.py | 生成急诊健康卡 |

---

## 健康档案目录

```
health-records/
├── user-profile.json    # 健康档案汇总
├── types.json           # 文件类型注册
├── daily/               # 每日健康数据
├── reports/             # 健康报告
├── consultations/      # 问诊记录
└── ...原始健康文件
```

---

## 工作流配置

| 文件 | 用途 |
|-----|------|
| WORKFLOW.md | 7步问诊工作流 |
| HEALTH-MANAGEMENT.md | 动态文件管理系统 |
| SOUL.md | 角色定义 |
| AGENTS.md | 工作区配置+医疗红线 |

---

*本文件记录健康助手使用的工具和配置信息*
