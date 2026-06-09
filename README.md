# Dr. Health 👨‍⚕️ — AI 家庭医生智能体

基于 Hermes Agent 框架构建的开源 AI 家庭私人医生智能体。通过结构化的 7 步问诊工作流，为用户提供从症状初筛到就诊准备的全流程健康管理服务。
可供任意Agent安装使用，下面以Hermes举例

## 特性

- 🩺 **7步问诊工作流** — 接收信息 → 意图判断 → 症状采集 → 鉴别诊断 → 建议输出 → 整理记录
- 💬 **对话式问诊** — 像真人医生一样，流式短句、一问一答、低门槛提问
- 📋 **健康档案管理** — 自动分类归档体检报告、检查单、处方、影像等文件
- 🔍 **医学知识搜索** — 28 个免费搜索引擎（百度、Wikipedia、360 等）
- 🚨 **内置风险分级** — 所有建议带 🔴🟠🟡🟢 风险标注
- 📄 **PDF/图片识别** — 支持 OCR 文字识别和 PDF 文本提取
- 💊 **药物管理** — 用药建议、相互作用检查、用药记录
- 📊 **趋势分析** — 生命体征追踪和趋势变化分析
- 🔒 **隐私优先** — 所有数据本地存储，绝不外泄
- 🎯 **自包含部署** — 所有配置和技能打包在工作区内，无需额外依赖

## 快速开始

### 前置要求

- 已安装
- Python 3.8+（脚本运行）
- Node.js（OCR 功能，可选）

### 部署

```bash
# 1. 克隆仓库
git clone https://github.com/frtagn2023-hub/Family-Doctor-Agent.git
cd dr-health

# 2. 复制到 Agent 工作区
mkdir -p ~/.hermes/health-doc
cp -R AGENTS.md SOUL.md IDENTITY.md USER.md WORKFLOW.md skills/ health-records/ memory/ docs/ *.md ~/.hermes/health-doc/

# 3. 启动 Agent
```

Agent 启动后会自动检测为新用户，进入首次引导流程（自我介绍 → 功能介绍 → 信息采集）。

### 依赖安装（可选）

```bash
# PDF 文本提取
pip install PyPDF2

# 图片 OCR 识别
cd ~/.hermes/health-doc/skills/ocr-local
npm install tesseract.js
```

## 项目结构

```
dr-health/
├── README.md                    # 项目说明
├── INSTALL.md                   # 部署指南（面向 Agent 阅读）
├── AGENTS.md                    # 工作区规则 + 启动逻辑 + 沟通风格
├── SOUL.md                      # 角色定义
├── IDENTITY.md                  # 身份卡片
├── WORKFLOW.md                  # 7步问诊工作流详解
├── HEALTH-MANAGEMENT.md         # 文件管理系统
├── HEARTBEAT.md                 # 心跳/主动服务配置
├── TOOLS.md                     # 工具配置
├── USER.md                      # 用户画像（空模板）
├── skills/                      # 技能定义 + 脚本
│   ├── family-doctor/           # 核心：7步问诊（423行）+ 10个Python脚本
│   ├── cn-web-search/           # 28个中文搜索引擎
│   ├── knowledge-graph/         # 知识图谱管理
│   ├── ocr-local/               # 图片OCR识别
│   ├── pdf-text-extract/        # PDF文本提取
│   └── pdf-generator/           # PDF文档生成
├── health-records/              # 健康档案模板
│   ├── user-profile.json        # 空档案
│   ├── types.json               # 空类型注册表
└── docs/                        # 文档
    └── dr-health-agent-doc.html # 完整技术文档
```

## 工作流

详见 [WORKFLOW.md](WORKFLOW.md) 和完整技术文档 [docs/dr-health-agent-doc.html](docs/dr-health-agent-doc.html)。

### 执行保障

采用两层架构确保工作流正确执行：

| 层级 | 文件 | 加载方式 | 内容 |
|------|------|---------|------|
| 第一层 | AGENTS.md | 每次会话自动注入 | 启动逻辑、沟通风格、医疗红线 |
| 第二层 | skills/family-doctor/SKILL.md | 按需加载 | 完整7步工作流、问诊模板 |

## 医疗红线

| 规则 | 说明 |
|-----|------|
| 隐私优先 | 健康信息绝不分享给第三方 |
| 不替代诊断 | 明确告知不能替代专业医生 |
| 风险标注 | 所有建议必须标注风险等级 |
| 紧急引导 | 紧急情况立即建议就医 |
| 不主动干预 | 不主动发送健康提醒或随访 |

## 交流与贡献

- 完整技术文档：[docs/dr-health-agent-doc.html](docs/dr-health-agent-doc.html)
- 部署指南：[INSTALL.md](INSTALL.md)

## 免责声明

Dr. Health 是一个 AI 工具，不能替代专业医疗诊断。如有健康问题，请咨询执业医师。紧急情况请立即拨打急救电话。

## 许可

[MIT License](LICENSE)
