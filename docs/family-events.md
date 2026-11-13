# 重复问诊与健康事件整理

本功能整理同一家庭成员的资料，不判断疾病、不计算认知风险。两次问诊可以描述一次健康事件；来源份数不能直接当作独立发作次数或独立认知异常次数。

## 数据如何保存

每份问诊在 consultations/cons_编号.md 中保留原文整理和来源。user-profile.json 内的 consultations 保存完整结构化副本，consultation_episodes 保存事件与来源的关联；主档位于 consultations/episodes/episode_编号.md。列表和报告按主档组织，同时能展开本人和家属的原始说法。

合并保留全部原文件与差异，不用后来的说法覆盖较早的风险、否认或医生资料。主档是可重建的展示文件，索引是权威资料。查找候选、判断关联、追加来源和更新事件在同一个成员文件锁内完成，多位家人同时补充也不会互相覆盖。

## 比较的是症状发生时间

例如，张明周一描述周一上午持续咳嗽，张悦周三补充张明周一上午的同一次咳嗽，仍可能是同一事件。两人都在周三发消息，但一人描述上午、另一人描述晚上再次发作，则不能因为聊天时间接近就合并。

Hermes 应先核对目标成员和实际发生时间，保留原始时间说法。相对日期依原消息时间及其时区换算；消息日期不明时询问，不按入档日期猜测。“最近”“这几天”“上午”等模糊描述不得伪造精确起止点。start/end 是症状实际存在的区间，不是“可能在这些时间中的某刻开始”的估计范围。

传给 add_consultation add --event-json 的示例文件（合成数据）：

```json
{
  "symptoms": [
    {"key": "咳嗽", "site": "不适用", "polarity": "present"}
  ],
  "symptoms_complete": true,
  "start": "2026-11-11T08:00:00+08:00",
  "end": "2026-11-11T10:00:00+08:00",
  "precision": "hour",
  "course": "new",
  "time_text": "11月11日早上八点到十点一直咳嗽"
}
```

- symptoms：统一且具体的症状词、部位与侧别、present/denied/unknown。不要只记录疾病名称。site 的“不适用”仅用于确实不适用部位的症状；不知道部位应留空或写未知。
- symptoms_complete：是否已覆盖本次事件涉及的完整症状集合；缺项保持 false。
- precision：minute/hour 表示明确到分钟/小时，day 表示只知道日期，unknown 表示时间不清楚。
- course：new/ongoing/recurrent/unknown。恢复后再次出现标为 recurrent，不能按持续症状并入旧发作。
- 时间必须带时区。起止时间缺失可为 null，原始表述仍保留。缺省 event-json 表示信息不足；明确指定一个不存在或无效文件会报错。

自动整理采用保守的文件规则：同一明确成员、完整症状集合/部位/肯定状态相同、minute/hour 精度且有原始时间表述、new/ongoing、区间长度大于零且不超过 48 小时，并同时满足交集占较长区间至少 80%、交集占并集至少 70%。新来源必须与事件中每一份既有来源都满足条件，且不能同时匹配多个候选。

**这些数值只是当前文件整理规则，不是医学阈值、疾病概率或临床验证结果。** 它们也不保证一定为同一事件。宽泛时间、明确复发、未知时间、多个候选、多症状只有部分重合不会自动合并。只知道某一时刻而没有持续区间时，也保留待核实；不人为加宽区间以通过规则。

一条问诊同时包含多个独立发作时，应分别整理并使用不同的请求项目后缀。当前工具不会自动拆分一段多症状描述再逐个合并；部分重合会保留完整来源并提出候选，避免吞掉另一症状。

## 使用与核实

下例姓名和 profile 为示意。实际工具通过 family.py 选择目标，不在运行中的 Python 进程里切换全局成员目录。

```sh
python3 family.py --profile father run add_consultation -- add \
  --chief-complaint '早晨持续咳嗽' --event-json /private/event.json \
  --request-id 'feishu-message-A:cough'
python3 family.py --profile daughter --member '张明' run add_consultation -- add \
  --chief-complaint '补充张明早晨同一次咳嗽' --event-json /private/event.json \
  --request-id 'feishu-message-B:cough'
python3 family.py --profile daughter --member '张明' run add_consultation -- list
```

资料模糊时可以问：“说的是周一上午那次，还是后来又出现的一次？”这是核对事件事实，不是授权流程。核实为同一次后，可将新来源明确加入已有事件：

```sh
python3 family.py --profile daughter --member '张明' run add_consultation -- add \
  --chief-complaint '补充情况' --episode-id episode_实际编号 \
  --merge-basis '家属明确说明是周一上午已记录的那一次，之后未再发作' \
  --request-id 'feishu-message-C:followup'
```

已分成两条主档的同一事件可以 merge；误合并的某份来源可以 split。它们只改变关联，保留事实依据、操作时间、提供者及前后快照，不删除原始问诊。

```sh
python3 family.py --profile daughter --member '张明' run add_consultation -- merge \
  --episodes episode_实际编号1 episode_实际编号2 --basis '核实为同一次发作，保留两方描述'
python3 family.py --profile daughter --member '张明' run add_consultation -- split \
  --report cons_实际来源编号 --basis '核实为恢复后第二次出现，之前归并有误'
python3 family.py --profile daughter --member '张明' run add_consultation -- rebuild
```

手工核实关联或拆分后的事件不会立即被自动规则重新合并。旧主档保留重新整理提示，当前列表以活动索引为准。rebuild 在成员锁内重新生成主档，并补回缺失的来源展示文件。

## 重试与归并不同

--request-id 标识同一输入的重试。同一 profile、渠道、目标成员下重复提交相同内容只保留一份问诊来源；同一键出现不同内容会拒绝覆盖，后续补充需使用新键。未显式提供时，家庭模式可使用 Hermes 的当前消息 ID；一条消息包含多个独立问诊项目时必须显式加稳定项目后缀。

不同家人的两条消息不会因消息 ID 恰好相同而互相吞并，而是先保留来源，再判断是否属于同一事件。任何问诊整理文档都有 language_sample_eligible=false：它是经过整理的健康资料，并非可以直接拿来分析老人语言能力的原始发言。

## 恢复与旧资料

索引已保存但主档生成失败时，命令会明确提示展示未更新，可用 rebuild 恢复；重试同一 request-id 不会多存一份。原有无归属、无角色、无发生时间的历史数据不会凭姓名或问诊日期自动补齐。家庭登记也不会自动搬迁已有 Hermes profile 的健康目录，迁移前需核对归属并保留原目录。
