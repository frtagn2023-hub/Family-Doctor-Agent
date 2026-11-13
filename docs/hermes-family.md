# Hermes 与飞书的单家庭接入

适用场景：一台机器、一个家庭、每位家人已有独立 Hermes profile，通过飞书交流。所有明确登记的健康 profile 使用同一个家庭资料库，显示成员姓名；家庭内没有邀请审批、授权页面或新增 Web 账号。未登记的 profile 不会自动加入家庭。

## 1. 保留 Hermes profile，共享健康资料目录

Hermes profile 的配置、会话、记忆和网关状态仍各自独立。`HERMES_HOME` 是 Hermes 的 profile 目录，不是家庭资料目录，也不必与工具工作目录相同。不要把家人的 `HERMES_HOME` 指向同一个位置。[Hermes 官方 profiles 说明](https://hermes-agent.nousresearch.com/docs/user-guide/profiles)

本项目使用两个不同层级的目录：

| 设置 | 用途 |
|---|---|
| `FAMILY_DOCTOR_FAMILY_HOME` | 所有家庭健康 profile 共用的家庭根目录，包含 `family.json` 和 `members/` |
| `FAMILY_DOCTOR_HOME` | 一次工具调用的成员工作区，由 `family.py run` 自动设置为家庭根目录下的 `members/成员编号` |

家庭模式不要给所有原始脚本配置同一个 `FAMILY_DOCTOR_HOME`，也不要在各 profile 中固定 `FAMILY_DOCTOR_MEMBER_ID`。通过 `family.py run` 选择成员，由包装器设置成员目录、提供者、被咨询者与本人／代问诊角色。原始脚本独立使用的旧个人模式见 [安装说明](../INSTALL.md)。

建议目录如下，真实资料放在 Git 仓库外：

```text
/absolute/path/Family-Doctor-Agent/       完整代码仓库
  family.py
  WORKFLOW.md
  skills/
/absolute/path/family-doctor-household/  家庭私人资料目录
  family.json                          姓名、稳定成员编号与 profile 关联
  members/
    m_.../health-records/               一位成员的健康资料
    m_.../health-records/               另一位成员的健康资料
```

成员编号用于保持文件关联，界面和报告显示姓名。同名成员需要按编号选择；不会猜测归属。家庭目录支持同机多进程文件锁，不是跨机器同步服务。

## 2. 让每个 profile 加载完整仓库内的技能

保留完整 Git checkout，不要仅复制 `SKILL.md` 或 `skills/family-doctor/`：本技能还依赖仓库根目录的 `family.py`、`WORKFLOW.md`、模板以及兄弟目录中的 PDF/OCR 工具。

Hermes 支持 `skills.external_dirs`。在每个健康 profile 自己的 `config.yaml` 中合并下列配置，替换为本机真实的绝对路径；保留该文件已有设置：

```yaml
skills:
  external_dirs:
    - /absolute/path/Family-Doctor-Agent/skills

terminal:
  backend: local
  env_passthrough:
    - FAMILY_DOCTOR_FAMILY_HOME
    - FAMILY_DOCTOR_TIMEZONE
    - FAMILY_DOCTOR_PROFILE
```

`external_dirs` 指向完整仓库中的 `skills`，因此技能仍从原目录加载。Hermes 加载技能时会提供技能所在目录；相对链接按该目录解析，工具使用解析后的绝对路径，例如 `/absolute/path/Family-Doctor-Agent/family.py`，不能假设当前工作目录就是仓库根目录。

若 profile 本地已安装旧版同名 `family-doctor`，它可能遮蔽外部目录版本。先通过 `hermes -p PROFILE skills list` 和加载技能时显示的路径核对实际版本，再按现有安装方式处理旧副本，不要直接覆盖个人 profile。外部技能目录不会自动成为只读目录。安装方式与优先级见 [Hermes 外部技能目录](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills#external-skill-directories)。

本接入示例使用本机 `local` 工具后端。容器只挂载技能子目录时，根级脚本和家庭资料不一定可见；若已有容器部署，需要另行挂载完整仓库及家庭目录，并使用容器内路径，不能直接照搬主机路径。

## 3. 设置共享目录与当前 profile

在每个健康 profile 自己的 `.env` 中设置相同的家庭根目录和时区。以下路径只是占位示例，需替换为实际绝对路径，不把健康资料写进 `.env`：

```dotenv
FAMILY_DOCTOR_FAMILY_HOME=/absolute/path/family-doctor-household
FAMILY_DOCTOR_TIMEZONE=Asia/Shanghai
```

包装器选择当前 profile 的优先级为：

1. 本次调用显式传入的 `--profile`。
2. 环境变量 `FAMILY_DOCTOR_PROFILE`。
3. Hermes 当前会话提供的 `HERMES_SESSION_PROFILE`。

当前 Hermes 网关能够提供会话 profile。优先使用它，并核对其值已在家庭登记中关联；不要从飞书昵称、工作目录或 `USER.md` 中猜测。若所用 Hermes 版本未向工具传递该字段，可以在专属于一个人的 profile `.env` 中设置 `FAMILY_DOCTOR_PROFILE=father`，或者在该 profile 的工具调用中显式传 `--profile father`。这里的 `father` 必须替换为已登记的准确 profile 名称。

`FAMILY_DOCTOR_PROFILE` 优先于会话字段，所以不能把父亲的固定值复制到女儿的 profile，也不要在所有 Hermes 进程共用的 shell 初始化文件中固定某一个成员的 profile。显式 `--profile` 同样必须来自已核对的配置，不能从用户消息里的自称直接覆盖。

多 profile 网关的环境值按各自作用域传递。将自定义变量列入 `terminal.env_passthrough`，并在每个 profile 的工具内检查实际值；只在启动默认网关前执行一次 `export` 不足以证明所有 profile 都已正确配置。参考 [Hermes 终端配置](https://hermes-agent.nousresearch.com/docs/user-guide/configuration#terminal-backend-configuration) 和 [环境变量传递实现](https://github.com/NousResearch/hermes-agent/blob/main/tools/env_passthrough.py)。

不要修改 Hermes 原有的 `HERMES_HOME`、飞书密钥或网关接入方式来实现家庭资料共享。本项目不会自动修改这些配置。

## 4. 建立家庭和成员关联

以下姓名、profile 和路径均为示例。已有代码 checkout 不需要重新克隆；登记前核对名字对应的实际 profile。全局 `--home`、`--profile` 和 `--member` 放在子命令之前；`add-member`、`bind-profile` 自身的参数按示例传入。

```sh
export FAMILY_DOCTOR_FAMILY_HOME=/absolute/path/family-doctor-household
export FAMILY_DOCTOR_TIMEZONE=Asia/Shanghai
FAMILY_DOCTOR_CODE=/absolute/path/Family-Doctor-Agent

python3 "$FAMILY_DOCTOR_CODE/family.py" init --name '我的家庭'
python3 "$FAMILY_DOCTOR_CODE/family.py" add-member --name '张明' --profile father
python3 "$FAMILY_DOCTOR_CODE/family.py" add-member --name '张悦' --profile daughter
python3 "$FAMILY_DOCTOR_CODE/family.py" members
```

老人暂时没有自己的 profile 时，可以只建立成员档案，家人通过 `--member` 代问。以后再将新 profile 关联到这份既有档案，不重复建人：

```sh
python3 "$FAMILY_DOCTOR_CODE/family.py" add-member --name '李芳'
python3 "$FAMILY_DOCTOR_CODE/family.py" bind-profile --member '李芳' --profile mother
```

同一个 profile 不能自动重新绑定到另一个人。已有旧个人资料也不会因登记而自动搬迁；先核对文件归属、保留原目录，再做明确迁移，不能按文件名或相同姓名批量归档。

## 5. 本人、家庭代问与临时代问

```sh
# 张明本人：默认选择 father 关联的成员
python3 "$FAMILY_DOCTOR_CODE/family.py" --profile father run add_consultation -- add \
  --chief-complaint '本次主诉' --request-id 'synthetic-self-message:complaint-1'

# 张悦为张明代问：资料属于张明，提供者仍为张悦
python3 "$FAMILY_DOCTOR_CODE/family.py" --profile daughter --member '张明' run add_consultation -- add \
  --chief-complaint '补充张明的情况' --request-id 'synthetic-proxy-message:complaint-1'

# 查看张明按事件组织的问诊，必要时展开来源
python3 "$FAMILY_DOCTOR_CODE/family.py" --profile daughter --member '张明' run add_consultation -- list --sources
```

不要强制追加 `--role self` 覆盖代问角色。上面的 `synthetic-...` 仅是演示键，真实调用应使用实际稳定消息键。未登记朋友的临时代问只使用当前对话信息，不选择任意家人的档案存储。

本人和家属描述相同症状时，先分别保存来源，再核对症状实际发生时间。聊天时间接近不等于发生时间相同；来源合并后也不能重复统计为多次发作。具体的事件字段、自动归并条件、事实核实、手工合并与拆分见 [重复问诊与健康事件整理](family-events.md)。

文件管理沿用同一入口和成员选择。`family.py --profile father files --rebuild` 按姓名生成全家文件总览；`family.py --profile daughter --member '张明' run file_manager -- list` 查看目标成员文件。检查原件重复上传保留一份正式文档和多条来源，资料可关联事件、归档恢复或按事件导出，详见 [家庭文件管理](family-files.md)。由 Hermes 使用 JSON 里的内部定位编号操作，向家人回复姓名、资料标题与处理结果。

## 6. 认知观察导入

认知统计只接收约定的结构化观察，使用独立的 `cognition.py`，不扫描飞书聊天，也不根据一般问诊数量自动生成认知信号。先按[导入格式](web-dashboard.md)准备 JSON/CSV，再调用：

```sh
python3 "$FAMILY_DOCTOR_CODE/cognition.py" --profile father validate \
  --file /private/observations.json --format json
python3 "$FAMILY_DOCTOR_CODE/cognition.py" --profile father import \
  --file /private/observations.json --format json
```

家庭目录沿用 FAMILY_DOCTOR_FAMILY_HOME。当前 profile 是导入操作来源，默认要求每条 `reporter_profile` 与它一致。家属代述时，`member` 指向被观察家人，`reporter_profile` 仍是家属，并使用 `caregiver_observation`。本人来源必须使用 `self_expression` 且成员归属一致；家属说话方式和 AI 摘要不能冒充老人本人表达。

`source_record_id` 使用实际稳定来源编号，重试保持不变。同成员、同提供者、同来源编号的内容发生变化会报冲突，不通过生成新编号掩盖修改。本人和家属只有明确描述同一实际时刻的同一次观察时才共享 `event_key`；发生在另一时刻的再次提问应使用独立事件。

validate 用于预先检查，import 会再次校验并整批写入；命令退出失败时不能声称统计已更新。整理明确的历史跨 profile 来源时可使用 `--batch`，每个提供者仍须已登记。Web 则采用上传、预览、修正错误、提交的流程；两种入口写入同一认知观察库。这条导入路径不会自行分析聊天；自然交流分析使用下述独立窗口与工具，不能将相同观察再次导入后相加成患病分数。

## 7. 聊天观察与 Web 提示

聊天分析使用 Hermes 当前模型按九维规则处理有限原始上下文，核心校验引用、来源和计数，只保存去原文结果。入口为 `cognition.py chat-prepare/chat-record/chat-stats`（前两项从 stdin 接收 JSON），协议见 [聊天观察流程](chat-analysis.md)。不新建模型服务，也不批量读取原有会话。

推荐按 [可选插件说明](../integrations/hermes/family-doctor-cognition/README.md) 在已登记 profile 中启用 `family-doctor-cognition`。它注册 `pre_llm_call/post_llm_call`，在进程内暂存启用后的用户消息和助手上下文，提供 `family_cognition_prepare/record` 两个受限工具。每轮只注入固定流程，让当前模型完成分析，不要求老人配合隐藏测试。prepare 返回窗口和全部维度模板；record 只回保存回执。插件读取当前 ContextVar 路由，票据不能跨 profile 或回合使用。

不需要导入旧聊天时间。窗口使用首次接收观察时间且重试不变，明确不是平台发送或症状发生时间；128 会话、24 条/240 KiB、一小时闲置失效，重启丢弃。切换目标成员、本人/家属或输入方式后从本轮重新积累上下文，不复用上一段表达。实际 profile 尚未安装启用前不能称为已自动运行；模型未执行工具、消息中断或上下文不足也会留下观察空白。

认知变化提示只在 Web 展示，不发送飞书风险通知、不在普通回复或过程说明发布内部分析；直接询问功能时如实解释。真实急症的安全回应继续有效。插件不能保证宿主流式输出或日志已隔离，需按真实部署核验显示设置。家庭绑定不是对实际发言者的身份认证，下节的来源规则继续适用。

## 8. 飞书来源与重复消息

当前实现用已配置的 profile 关联识别提供者；这是文件归属约定，不是人员认证。本接入方式以“一个人的个人飞书会话对应其专用 profile”为前提。家人借用同一个飞书账号、共用 profile 或混合群聊发言时，profile 主人不一定是本次实际说话人，应先核对并在资料中注明；不能将这种输入直接当作老人的本人语言样本。

新版 Hermes 区分 `HERMES_SESSION_ACTOR_ID`（本轮实际发送者）和 `HERMES_SESSION_USER_ID`（会话路由），并提供 `HERMES_SESSION_PROFILE`、`HERMES_SESSION_MESSAGE_ID`、`HERMES_SESSION_PLATFORM`。混合发送者的运行轮次可能没有单一 actor。工具会保留可获得的网关标识作为观察到的来源，**不会根据这些标识自动登记成员或验证 profile 主人**。字段不存在时保留未知，不能伪造。依据见 [Hermes 会话上下文实现](https://github.com/NousResearch/hermes-agent/blob/main/gateway/session_context.py)。

家庭模式未显式传 `--request-id` 时，问诊工具可使用当前 `HERMES_SESSION_MESSAGE_ID`。同一消息产生多个独立问诊项目时，必须使用各自稳定的项目后缀，例如 `实际消息ID:complaint-1`、`实际消息ID:complaint-2`；重试沿用原键，后续新补充使用新消息键。相同键但不同内容会拒绝覆盖，不能用每次随机生成新键绕过重试去重。

飞书传输层去重与本人／家属对同一事件的合并是两件事。飞书已有消息 ID 去重仍不能替代本项目的来源和事件关联。飞书 `open_id` 按应用区分，若以后增加自动成员映射，需要同时保留接入应用范围；当前版本不会仅凭昵称或裸 `open_id` 猜测关联。[Hermes 飞书说明](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/feishu)、[官方飞书适配器](https://github.com/NousResearch/hermes-agent/blob/main/plugins/platforms/feishu/adapter.py)

无需为家庭资料共享改变现有机器人连接。多个 adapter 不应同时持有同一飞书应用身份；如果现有部署使用一个机器人按人分配 profile，沿用已核对的路由。当前 Hermes 还支持多 profile 网关，具体行为应按已安装版本核对。[官方多 profile 网关说明](https://hermes-agent.nousresearch.com/docs/user-guide/multi-profile-gateways)

## 9. 接入验证与运行边界

扫描件与图片遵循[附件识别规范](../skills/family-doctor/references/attachment-recognition.md)：当前模型有视觉能力且确实获得完整图像时先自行转录，能力或可见材料不足时再调用本地 OCR。PDF 图片页可由 `skills/pdf-text-extract/visual.py prepare` 在本机渲染后交给当前模型；该工具不会替宿主发送图片或切换模型。

需要核验实际部署的入站图片路由。Hermes 的原生图片路由会将像素交给当前主模型；文本路由可能先由辅助视觉模型生成描述，再交给主模型。宿主显式配置的辅助视觉路由也可能影响自动选择。辅助描述不能当作当前模型亲自看过图片；技能无法撤回宿主在它运行之前已执行的辅助调用，不能仅更新仓库就宣称整条链路改成了 OCR 后备。本项目不自动修改真实 profile 的图片路由、模型或工具配置。

先用独立临时目录和合成人物验证，不拿真实家庭资料试验。建议检查：

1. 每个健康 profile 实际加载的是完整仓库中的技能，能读到同一版 `WORKFLOW.md` 和 `family.py`。
2. 从各自飞书会话调用工具，核对共享家庭目录相同、当前 profile 不同；检查时只输出相关路径和 profile，不打印整份环境或 `.env`。
3. 本人问诊进入自己的目录；家属使用 `--member` 后进入目标成员目录，并保留家属姓名及 profile 为提供者。
4. 同一消息重试只有一份来源；两位家人的不同消息各保留一份来源，时间与症状满足规则时才整理成同一事件。
5. 同时写入同一成员不丢记录；时间不明、复发、部分症状重合和来源冲突按 [事件整理规则](family-events.md) 处理。
6. 切换 profile、重新开始会话和重启现有网关后再次核对，避免旧环境值造成串档。旧版本字段不足时使用明确配置，不猜测默认成员。
7. 使用合成报告图片核对当前模型实际收到的是原图还是辅助描述；视觉可用时不先跑 OCR，视觉不可用或识别受限时能调用 OCR 并保留未完成状态。逐页转录通过真实页数校验后，仍需核对字段才能正式入档。

仓库的合成数据回归测试验证文件路由与整理行为，不等于已经在你的 Hermes／飞书部署中完成联调。本项目不自动启动网关或发送飞书消息。可以手动启动[认知统计页面](web-dashboard.md)查看并导入结构化观察；后台不再展示一般健康文件或问诊目录。可选插件提供当前模型驱动的聊天观察流程和受限内存窗口，须另行启用并验证；没有独立后台模型或飞书认知风险推送。统计口径和医学边界见[认知观察说明](cognitive-observation.md)。

只开源代码、空模板和合成测试。家庭登记、成员资料、问诊原文件、导出文件及 Hermes 会话保留在私人目录，不上传 Git。共享健康文件不会自动共享各人的 Hermes 会话与普通记忆；涉及模型、搜索或其他外部服务的数据范围见 [隐私说明](../PRIVACY.md)。
