# Dr. Health — Hermes 家庭健康助手

通过 Hermes 和飞书管理一个家庭的健康资料：按姓名关联本人及家属代问诊、整理重复记录和检查文件，并从自然交流中形成有依据的认知观察，供家人在本机 Web 页面查看。

**适用于同一台机器上的一个家庭。每位家人保留独立 Hermes profile，共用家庭资料目录；只开源代码、空模板和合成示例，不公开家庭登记、健康资料或对话。**

只把明确登记的健康 profile 关联到家庭，不自动扫描全部 Hermes 用户。每次记录区分“谁提供信息”和“资料属于谁”。旧个人模式仍兼容；未登记者的临时代问诊不落盘。

[本次更新说明](CHANGELOG.md) · [安装与迁移](INSTALL.md) · [Hermes 家庭接入](docs/hermes-family.md) · [数据与隐私](PRIVACY.md)

## 已实现的能力

| 方向 | 当前能力 |
|---|---|
| 家庭与来源 | 登记成员姓名和 profile，区分信息提供者与被咨询成员。家属代问写入目标成员档案；未登记者的临时代问只在当前对话处理。 |
| 重复问诊整理 | 比较症状实际发生时间、时间精度、部位/侧别、持续或复发情况，保留各次来源并整理事件主档；支持核实后合并、拆分和重建。 |
| 家庭文件管理 | 按资料实际日期检索，关联检查文件与事件，归档、恢复、按事件导出。相同原文件保留一个正式条目和多次提供来源，识别差异不静默覆盖。 |
| 报告附件识别 | 纯文字 PDF 可直接提取文本；扫描件和图片在当前模型具备视觉能力且已收到目标原图/完整页面时优先由模型转录，能力或材料不足时以本地 OCR 补充。 |
| 聊天认知观察 | 使用 Hermes 当前模型逐维检查真实回应机会、先前上下文和用户原句；本人表达与家属观察分开，保存去原文结果，重叠窗口不重复计数。 |
| 认知 Web 页面 | 按姓名展示观察覆盖、独立天数、九维机会与线索、早期参考及复核提示；认知提醒仅在 Web，不主动推送飞书。 |
| 认知观察结构化导入 | JSON/CSV 模板、严格字段与来源校验、预览纠错、重复提交去重、整批保存；这不是报告附件上传入口，不接收聊天原文或自由分析报告。 |
| 基础健康工具 | 成员档案、症状/用药/体征、问诊阶段、附件核对、就诊摘要及急诊卡；读写使用文件锁、原子保存和变更备份。 |

家庭内不增加邀请审批、授权页面或 Web 账号。成员绑定用于防止串档，不构成同机用户之间的身份认证或访问隔离；本项目不作为面向不互信用户的公众服务。

## 能力边界

本仓库不包含 LLM 服务或聊天界面。自然语言角色识别、医学判断、网页搜索和提醒调度依赖宿主；代码中的状态检查不能替代临床验证。药物规则是有限演示库，不提供安全合用保证，不是完整药学数据库。

本地工具处理文件不等于宿主模型在本地推理。当前模型继续使用 Hermes 原有服务；飞书、宿主、提供者的上下文、工具历史和日志仍可能保留相关内容。插件指令不能硬性保证宿主流式输出、工具进度或日志绝不显示分析，实际部署需核验。完整数据范围见 [PRIVACY.md](PRIVACY.md)。

**认知观察不等于痴呆诊断或患病概率。** 当前模型按九维规则分析自然对话，本地工具核验来源、上下文与精确引用，Web 展示资料覆盖、趋势和需核实的变化；不主动发送飞书认知风险提醒。可选插件需要按现有部署明确启用，并不表示已安装到实际 profile；原有急性危险信号的当轮安全回应继续有效。详见 [聊天分析流程](docs/chat-analysis.md) 和 [医学边界](docs/cognitive-observation.md)。

## 认知统计与结构化观察导入

```sh
# 只使用合成示例，不读取真实家庭资料
python3 web_server.py --demo --port 8765

# 接入已登记的家庭，查看认知统计并导入观察
python3 web_server.py --home /实际家庭资料目录 --profile 已登记profile --port 8765
```

打开同一台电脑上的 `http://127.0.0.1:8765`。服务只监听本机；两种模式分别启动，端口被占用时换一个端口。界面只显示成员姓名、认知观察统计、九维观察、早期参考与 Web 复核提示；其他健康资料页面已移除。聊天分析在 Hermes 当前模型中进行，Web 不展示原文或分析长文。页面使用本地资源，不将健康内容保存到浏览器本地存储；现有折线属于手工补充观察，聊天按日聚合尚未绘制为页面折线。

这里的 JSON/CSV 仅用于认知观察元数据，检查报告、扫描件和图片走下文的附件识别与核对流程。认知观察导入为：**下载模板 → 填写真实观察 → 预览新增/重复/错误 → 修正后重新预览 → 整批保存**。演示模式只支持预览，不能提交；真实资料读取失败不会回退到演示数据。

- 只允许已登记成员、提供者 profile、带时区的实际观察时间，以及约定的来源、质量、信号和影响因素枚举；拒绝原文、自由摘要、疾病分数与多余字段。
- 稳定来源键相同且内容相同的重试跳过；内容变化则拒绝整批，错误行不会被悄悄丢弃后继续导入其他行。
- 只有明确属于同一成员、同一实际时刻的同一次观察，才共享 `event_key`；相似文字或上传时间接近不能作为合并依据。
- 预览后文件、家庭登记或资料版本变化，需要重新预览；损坏资料停止操作，不用空模板重置。

Hermes 可通过 `cognition.py validate/import/stats` 使用同一观察格式；默认校验提供者为当前 profile，历史混合来源批次才显式使用 `--batch`。手工观察与聊天分析使用独立协议，不能相加为一个疾病分数。模板、字段、CLI、大小限制和纠错见[导入说明](docs/web-dashboard.md)。

## Hermes 家庭模式快速开始

Python 3.9+；核心管理工具和 Web 页面不需要第三方 Python 库。PDF/OCR 为可选依赖，Hermes、飞书接入和模型配置沿用自己的部署。各 profile 必须能访问同一台机器上的共享目录，保持各自的 HERMES_HOME 不变。保留完整仓库，不要只复制 `SKILL.md`。

```sh
git clone https://github.com/frtagn2023-hub/Family-Doctor-Agent.git
cd Family-Doctor-Agent

# 家庭资料放在代码目录之外
export FAMILY_DOCTOR_FAMILY_HOME="$HOME/.local/share/family-doctor-household"
export FAMILY_DOCTOR_TIMEZONE="Asia/Shanghai"
python3 family.py init --name '我的家庭'

# 以下姓名与 profile 均为示例，替换为实际配置
python3 family.py add-member --name '张明' --profile father
python3 family.py add-member --name '张悦' --profile daughter
python3 family.py members

# 本人查看文件目录
python3 family.py --profile father run file_manager -- list
# 女儿查看父亲资料：目标成员与提供者分别记录
python3 family.py --profile daughter --member '张明' run file_manager -- list
```

按 [Hermes 家庭接入](docs/hermes-family.md)让每个健康 profile 加载仓库中的 `skills/`，传入相同家庭目录，并核对当前 profile 已登记。使用 HERMES_SESSION_PROFILE 或各 profile 的 FAMILY_DOCTOR_PROFILE 后，日常可省略 --profile。各人的会话、记忆和飞书配置保持独立，不自动迁移旧档案，也不从昵称或“我爸”等称呼猜测成员归属。

文件总览与整理见 [家庭文件管理](docs/family-files.md)：

```sh
python3 family.py --profile daughter files --rebuild
python3 family.py --profile daughter --member '张明' run file_manager -- list
python3 family.py --profile daughter --member '张明' run file_manager -- list --kind document --status pending
```

家庭档案工具通过 `family.py run 工具名 -- 参数` 执行；包装器会选择成员目录并传入正确角色，不要强行传 --role self 覆盖代问诊。可选依赖见 [INSTALL.md](INSTALL.md)。

## 启用九维聊天观察

按 [插件安装说明](integrations/hermes/family-doctor-cognition/README.md)，在每个需要观察的 profile 中放置并启用 `family-doctor-cognition`，配置代码与家庭目录，允许相关工具，然后重启原有网关。**克隆仓库、启动 Web 或加载技能，不等于已经启用回合插件。**

插件在飞书自然交流中为当前模型提供有限窗口和模板，使用 `family_cognition_prepare`、`family_cognition_record` 保存结果；不读取历史聊天库、不启动另一个模型服务，也不保证每轮都会执行工具。首次交流、切换被观察成员或输入方式后，需要重新积累上下文。

九个维度为：**重复提问、上下文记忆、理解回应、话题连贯、时间顺序、找词表达、时间地点定向、计划组织、日常功能**。每项都要有真实可判断机会和精确用户原句，前四维还需核对先前上下文；没有足够机会就保留不可判断。本人表达与家属报告分别统计，家属的措辞不能作为老人本人的语言样本。转写、引用、共用账号、疲劳、情绪、药物、听视力及助手误解等限制均需保留。

近 30 天同一维度、同一来源类别在至少三个独立日期出现有效线索时，Web 提示家属核实。早期参考要求当前窗口之前 90 天的本人同输入方式资料，在同维度具备至少六个历史独立可评估日期和三个近期独立可评估日期；当前混合输入方式时不比较。这些是工程规则，不是临床筛查或疾病概率。资料不足、未记录线索或比例未增加都不等于正常。

插件记录的是首次接收观察时间，不是平台发送或症状发生时间。家属跨日复述同一旧事件不能算成多次新发生；重试、重叠窗口和只换来源编号也不能增加计数。明确的急性变化及起病证据单独处理，不等待普通累计门槛。

认知复核提示只在 Web，不主动通过飞书发布风险通知或分析报告；直接询问功能时如实解释，正常急症的当轮安全协助继续有效。不进行秘密测试、诱导错误或诊断宣告。执行步骤、质量与反例见 [聊天分析流程](docs/chat-analysis.md)和[九维规则](skills/family-doctor/references/chat-cognition-rubric.md)。手动接入可使用根目录 `cognition.py chat-prepare/chat-record/chat-stats`，前两项从 stdin 接收 JSON，避免主动另存原文文件。

## 问诊与文件整理

两位家人描述同一症状时，先保留各自来源，再比较**实际发作时间及其重合度**、症状、部位和复发情况。同一天咨询可能是不同发作，不同日期咨询也可能描述同一次事件。时间宽泛、只有部分症状重合或存在多个候选时保留待核实，不能为了合并编造精度。详见[重复问诊与事件整理](docs/family-events.md)。

纯文字 PDF 可先提取文本。扫描件、报告照片或 PDF 中的图片页，只有当前模型具备视觉能力且本轮确实收到目标原图/完整页面时，才优先让它按页转录；只有文件路径、缩略预览或已抽取文字不满足这一条件。无视觉能力、无法取得图片，或某些页面/字段识别受限时，使用本地 OCR 补充。能力与材料可用性由宿主和当前输入核实，项目不承诺自动探测。用户要求完全离线时，使用已配置的本地能力，不为模型优先而将资料发送至云端。

识别后保留原件、页码与对应文字，先暂存，再核对日期、数值、单位等关键字段后入档；看不清或未覆盖的部分保持待核对。模型转录不等于影像诊断，也不保证内容无遗漏。检查日期与上传日期分开；相同原文件去重时保留多次提供者，不同扫描或不同识别值不按相似度自动覆盖。这种确认用于转录准确性，不是家庭访问授权。归档只改变目录显示，不删除事实。详见[附件识别规范](skills/family-doctor/references/attachment-recognition.md)、[家庭文件管理](docs/family-files.md)与[数据约定](skills/family-doctor/references/data-model.md)。

## 兼容个人模式

不配置家庭环境时，仍可单独使用：

```sh
export FAMILY_DOCTOR_HOME="$HOME/.local/share/family-doctor-personal"
python3 doctor.py init --role self
python3 doctor.py check --role self
```

个人目录不能由多位家人共用，非本人临时代问不持久化。家庭模式通过 `family.py run` 路由，不混用个人目录。迁移前保留旧目录与备份，核对每份资料主人；兼容行为见 [INSTALL.md](INSTALL.md)。

## 数据与隐私

家庭资料应放在仓库之外，主要位置如下：

```text
家庭资料目录/
├── family.json                     姓名与 profile 关联
├── members/成员编号/health-records/  成员资料、原文件及来源
└── cognition/
    ├── observations.json           手工导入观察
    └── chat-observations.json       去原文聊天观察
```

聊天工具在内存中校验证据，观察库只保存来源/时间元数据、枚举、计数和哈希，不保存消息原文、引用或自由分析长文。插件内存窗口有数量、大小和闲置时限，重启即丢弃。Web 只返回聚合信息，不提供对话、逐条来源内容、原始 profile 或健康文件路径。

这不改变飞书、Hermes 和模型提供者各自的数据处理范围，不能把插件原文不落盘理解为整个系统不留对话；哈希去重也不是匿名化。文件权限、家庭成员绑定和本机页面不等于加密或账号隔离。

`doctor.py export/erase` 仅覆盖所选成员健康目录，不自动覆盖家庭级认知库、外部导出文件或宿主记录。完整家庭备份需包含登记文件、成员目录与认知目录；具体范围见 [PRIVACY.md](PRIVACY.md)。仓库忽略常见运行时数据，但自定义目录仍须核对，不能强制提交真实资料或凭据。

## 文档入口

| 文档 | 用途 |
|---|---|
| [本次更新说明](CHANGELOG.md) | 更新范围、行为变化与升级注意事项 |
| [安装与迁移](INSTALL.md) | 可选依赖、旧数据兼容和迁移 |
| [Hermes 家庭接入](docs/hermes-family.md) | 多 profile、环境传递和飞书路由 |
| [插件说明](integrations/hermes/family-doctor-cognition/README.md) | 逐 profile 启用、工具配置与合成联调 |
| [唯一工作流程](WORKFLOW.md) | 问诊、资料核对与聊天观察边界 |
| [认知统计与结构化观察导入](docs/web-dashboard.md) | Web 使用、认知观察 JSON/CSV 格式和纠错 |
| [附件识别规范](skills/family-doctor/references/attachment-recognition.md) | 模型视觉识别优先条件、OCR 后备与字段核对 |
| [认知观察说明](docs/cognitive-observation.md) | 统计口径、医学依据和限制 |
| [资料管理](HEALTH-MANAGEMENT.md) | 本地存储、附件与维护 |

## 验证

所有测试使用合成数据和临时数据工作区，不访问真实档案。

```sh
python3 -m unittest discover -s tests -v
npm ci --ignore-scripts --prefix skills/ocr-local
npm test --prefix skills/ocr-local
node --test tests/test_web_frontend.cjs
```

可选依赖未安装时，部分真实解析测试会跳过；安装 `requirements.txt` 后可验证实际 PDF 解析。PDF 报告还需要系统渲染依赖，见安装文档。现有测试不构成医疗有效性或中文 OCR 准确率验证。

## 项目结构

```text
AGENTS.md / WORKFLOW.md      宿主规则和唯一问诊流程
PRIVACY.md / INSTALL.md      数据边界和可复现安装
family.py                   单家庭登记与按成员路由
web_server.py / web/         本机认知统计与导入页面
cognition.py / cognition_store.py  结构化观察导入、去重与聚合
chat_cognition.py           聊天证据校验、去原文保存和 Web 提示规则
integrations/hermes/        可选回合插件与受限内存窗口
docs/web-dashboard.md        统计页面、导入格式与数据处理流程
docs/cognitive-observation.md 认知观察口径与医学依据
docs/chat-analysis.md        聊天分析流程、输入协议与实际边界
docs/hermes-family.md        Hermes profile / 飞书接入
docs/family-events.md        症状时间、重复问诊整理及恢复
docs/family-files.md         文件目录、重复上传、关联、归档和导出
skills/family-doctor/        成员档案、问诊、资料核对和报告工具
skills/pdf-text-extract/     模型逐页转录准备/校验、PDF 文本提取与 OCR 后备
skills/ocr-local/            图片 OCR 与锁定依赖
skills/cn-web-search/        面向医学来源的检索规范
skills/knowledge-graph/      保留的通用工具，非健康档案默认存储
skills/pdf-generator/        PDF 参考资料，实际报告出口在 family-doctor
templates/                  空模板，不含个人资料
tests/                      合成数据回归测试
```

运行时 health-records、仓库根目录下的 family.json 与 members 被 Git 忽略。仍应将整个家庭资料目录放在仓库之外。模板与健康档案分离；已有旧版数据的兼容说明见 [INSTALL.md](INSTALL.md)。

采用 [MIT License](LICENSE)。
