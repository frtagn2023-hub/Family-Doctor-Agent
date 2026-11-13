# 家安：认知观察统计与导入

Web 后台只保留认知观察统计和数据导入。页面按成员姓名展示结构化观察、聊天观察的九维聚合、趋势与 Web 复核提示；不再读取或展示健康文件目录、用药、体征、一般问诊和报告内容。项目原有的[文件管理工具](family-files.md)继续独立使用。

这里区分手工导入的结构化观察与 Hermes 当前模型生成的聊天观察，不计算患病概率。聊天分析逐维核对上下文与原句，在 Web 只展示聚合结果和需核实的变化；仅 Web 提示，不主动通过飞书发认知风险提醒。没有有效资料时显示相应的资料不足状态，不能解读为低风险。

## 启动

在项目目录运行以下两种模式之一：

```sh
# 合成演示，不读取实际家庭资料
python3 web_server.py --demo --port 8765

# 读取已登记家庭的认知观察统计，并允许导入
python3 web_server.py --home /实际家庭资料目录 --profile 已登记profile --port 8765
```

在同一台电脑打开 `http://127.0.0.1:8765`。端口已占用时换一个端口，按 Ctrl+C 停止服务。已有 FAMILY_DOCTOR_FAMILY_HOME 和 FAMILY_DOCTOR_PROFILE / HERMES_SESSION_PROFILE 环境变量时可省略对应参数。

演示模式的数据全部为合成示例，只能检查导入预览，不能提交到真实家庭档案。真实模式读取失败不会回退到演示数据。后台不另建 Web 账号；启动 profile 用于现有家庭路由及导入操作来源记录，不是新增授权审批。

## 聊天观察与 Web 提示

通过 [聊天分析接入](chat-analysis.md) 或 [Hermes 可选插件](../integrations/hermes/family-doctor-cognition/README.md) 形成去原文观察后，页面展示本人/家属片段数、独立天数、九维机会和线索天数、早期参考与提示。聊天 API 有按日聚合，当前页面尚未绘制聊天折线；已有折线仅属于手工补充观察。单次线索只供继续观察，近 30 天同维度、同来源类别在至少三个独立日期出现有效线索时建议家属核实；家属重复复述旧事不能算新日期。急性起病证据单独提示，不等累计阈值。这是工程规则，不是临床风险评分。

页面不接收聊天原文上传，不展示证据原句、模型推理或自由分析报告。插件在当前 Hermes 会话内分析，最终持久化库为 `cognition/chat-observations.json`；下面的 JSON/CSV 导入仍使用 `cognition/observations.json`，两类数据不重复相加成疾病分数。认知提醒只在 Web，实际急症的当轮安全回应仍可进行。

## 导入步骤

1. **下载模板。** 使用页面的 JSON 或 CSV 模板。模板含必须替换的占位行，不能直接导入，避免把样例混入真实统计。
2. **填写实际观察。** 明确被观察成员、提供者 profile、实际发生时间、来源类型、质量、信号与影响因素。文件只能含约定的结构化字段，不放对话原文、报告、自由文本摘要或诊断分数。
3. **选择文件并预览。** 后台校验格式、成员归属、来源与时间，显示新增、重复及错误数量和成员分布。错误只显示行号、字段及修正方向，不回显私人内容。
4. **修正错误后重新预览。** 未登记成员、重名无法定位、错误来源角色、无时区或未来时间、重复键冲突等都会阻止整批导入。不会悄悄丢弃错误行、只导入其余行。
5. **提交已预览批次。** 导入会再次校验，并在锁内原子保存。预览后已有其他人导入导致资料版本变化时，需重新预览；导入成功后刷新统计。该步骤是数据核对，不是家庭访问授权。

预览保存在服务进程内存中，有效期 15 分钟；服务重启或预览过期后重新上传预览即可。文件内容最多 1 MiB，每批最多 1000 条；超限时拆分为多个批次，稳定来源编号不要改变。后台不保存上传文件本身或文件名。

## 文件格式

JSON 顶层只能使用 `schema_version` 和 `records`，版本为 1：

```json
{
  "schema_version": 1,
  "records": [
    {
      "source_record_id": "REPLACE_SOURCE_ID",
      "member": "填写已登记姓名",
      "reporter_profile": "填写已登记profile",
      "observed_at": "YYYY-MM-DDTHH:MM:SS+08:00",
      "source_type": "self_expression",
      "quality": "usable",
      "signals": [],
      "confounders": [],
      "event_key": ""
    }
  ]
}
```

这是格式说明，不是可直接导入的真实记录。模板中的 `quality: usable` 只演示可用记录格式，只有已核对、适合纳入统计的来源才使用它；受限或应排除的来源分别填写 `limited`、`excluded`。`signals: []` 只表示没有录入信号，不是认知正常结论。

CSV 使用同一组列名，UTF-8 编码。`signals`、`confounders` 多个值用 `|` 分隔；没有值时留空。`event_key` 可以为空。JSON 中这两个字段使用数组。

| 字段 | 要求 |
|---|---|
| `source_record_id` | 实际稳定来源编号。同一来源重试保持不变，不用每次随机编号规避去重。 |
| `member` | 已登记的精确姓名或成员编号。重名时必须使用成员编号。 |
| `reporter_profile` | 已登记的提供者 profile；导入不会根据昵称或文件名创建成员。 |
| `observed_at` | 实际发生时间，ISO 8601 且带时区，例如 `2026-11-11T08:30:00+08:00`。不能用上传时间补填，不接受未来时间。 |
| `source_type` | `self_expression` 本人表达、`caregiver_observation` 家属观察、`clinician_record` 医生记录来源。 |
| `quality` | `usable` 可纳入统计、`limited` 信息受限、`excluded` 排除。后两类保留但不进入有效观察趋势。 |
| `signals` | 信号枚举数组；见下表。不能包含原句或模型解释。 |
| `confounders` | 可能影响表达或判断的因素枚举数组；见下表。不是已经排除其他疾病的声明。 |
| `event_key` | 可选的同一次观察事件关联键。确知同一成员、同一实际时刻的同一事件时才使用同一个键。 |

| 信号值 | 含义 |
|---|---|
| `repeated_question` | 重复提问线索 |
| `disorientation` | 时间或地点定向混淆线索 |
| `topic_discontinuity` | 话题衔接困难线索 |
| `word_finding` | 找词困难线索 |
| `daily_function_change` | 日常活动能力变化线索 |

影响因素可选 `sleep`（睡眠）、`mood`（情绪）、`medication`（药物）、`hearing_vision`（听视力）、`transcription`（转写）、`other`（其他）、`unknown`（未知）。这些类别用于保留背景，不自动推导原因。

本人表达要求提供者 profile 对应被观察成员；家属观察要求提供者和被观察成员不同。医生记录的提供者是上传者，`clinician_record` 不表示上传者已验证为医生，也不能变成本人的语言样本。来源不明时先核实，不为了导入成功改写为本人。

## 重复、关联与统计口径

- 去重键是“被观察成员 + 提供者 profile + source_record_id”。同键同内容再次提交会跳过；同键不同内容属于冲突，整批不写入。
- 本人和家属提供同一次观察时，各自来源仍保留；只有明确使用相同 `event_key` 且实际发生时刻一致，才按一个独立事件统计。时区写法不同但实际时刻相同可以匹配。
- 同一成员、同一个 `event_key` 的实际发生时刻不同会阻止导入。不能用一个键覆盖不同日期或时段再次出现的情况；也不因内容相似或上传时间接近自动合并。
- 近 30 天按上海日历计算。有效来源数统计 `usable` 来源；独立事件数按明确关联后的事件统计；待核实事件数是其中至少含一个信号的事件数，不能把多个信号或多位提供者重复算成多次异常。
- 本人语言样本只计有 `usable` 本人表达的独立事件。家属观察和医生记录不会增加本人语言样本数。
- 有效观察为空时显示“尚未评估”；有有效观察但没有录入信号时显示“已记录观察”；存在有效信号时显示“待核实变化”。这些都是资料状态，不是低、中、高患病风险。

导入内容保存在家庭目录的 `cognition/observations.json`，与一般健康档案分开。导入历史记录批次、时间、数量、校验值和操作来源，不保存上传原文。损坏数据会停止导入并保留原文件，不能用空模板重置。

## Hermes 导入入口

认知统计使用独立 CLI，不通过 `family.py run` 调用：

```sh
python3 cognition.py --home /实际家庭资料目录 --profile father validate \
  --file /private/observations.json --format json
python3 cognition.py --home /实际家庭资料目录 --profile father import \
  --file /private/observations.json --format json
```

默认要求批次里的 `reporter_profile` 都是当前 profile。代问时改变 `member` 并使用 `caregiver_observation`，提供者 profile 仍保留当前家属；不能写成老人的 profile。

明确整理历史多来源批次时，可在 validate/import 命令后加 `--batch`，仍会逐条校验成员与提供者登记关系。它只是允许一个批次保留不同历史提供者，不是授予额外家庭权限。

Hermes 需要先根据有依据的观察生成上述结构化文件，再调用校验与导入。此入口不会扫描飞书聊天。自然交流的当前模型分析使用独立的[聊天观察流程](chat-analysis.md)，可选插件需明确启用；完整医学定位见[认知观察机制](cognitive-observation.md)。

## 数据边界与验证

服务只监听 `127.0.0.1`。页面不加载外部资源，不把健康内容持久化到浏览器本地存储；统计 API 不返回对话、逐条来源内容、原始 profile、来源键、报告、文件路径或诊断。模板和导入端点仅在本机处理文件内容，不上传第三方。共享机器上的页面不区分查看者身份，不能视为账号权限隔离，不应直接转发到公网。

```sh
python3 -m unittest discover -s tests -p 'test_cognition_import.py' -v
python3 -m unittest discover -s tests -p 'test_web_server.py' -v
node --test tests/test_web_frontend.cjs
```

测试使用合成家庭，检查归属、去重、整批写入、统计口径、隐私字段和接口边界。工程验证不等于痴呆筛查或语言分析的医学有效性验证。
