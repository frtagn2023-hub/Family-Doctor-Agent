# Hermes 家庭聊天观察插件

这个可选插件让已经登记的家庭飞书会话在自然交流中执行认知观察。它利用当前 Hermes 模型分析，不另开模型服务：轮次开始时暂存当前用户文本，轮次结束时暂存助手上下文；两个受限工具提供窗口、接收结构化结果。原文仅由插件在进程内有限保留，本地认知库只保存去原文结果，提示只在 Web 展示。

仓库中的插件文件不会自动安装或启用到任何真实 profile。下面是操作者明确启用时的步骤；先完成[家庭与 profile 登记](../../../docs/hermes-family.md)，保留完整 checkout。本插件只支持 `feishu` / `lark` 人类会话，跳过 CLI、cron 与子代理。

## 安装与启用

每个使用插件的 profile 保留自己的 Hermes 主目录、密钥和会话。将以下占位值替换为实际路径/profile；不要覆盖现有同名插件目录，也不要把全家的 `HERMES_HOME` 合成一个目录：

```sh
FAMILY_DOCTOR_CODE=/absolute/path/Family-Doctor-Agent
FAMILY_HERMES_PROFILE_HOME=/absolute/path/this-profile-hermes-home
mkdir -p "$FAMILY_HERMES_PROFILE_HOME/plugins"
ln -s "$FAMILY_DOCTOR_CODE/integrations/hermes/family-doctor-cognition" \
  "$FAMILY_HERMES_PROFILE_HOME/plugins/family-doctor-cognition"
hermes -p 实际profile plugins enable family-doctor-cognition
```

`ln -s` 遇到已有目录会报错，不自动替换。可以复制插件目录，但必须通过下面的 `FAMILY_DOCTOR_CODE` 指向完整 checkout。插件使用已有家庭登记，不会扫描 profile、自动加人或创建家庭。

在该 profile 自己的 `.env` 中合并真实路径：

```dotenv
FAMILY_DOCTOR_CODE=/absolute/path/Family-Doctor-Agent
FAMILY_DOCTOR_FAMILY_HOME=/absolute/path/family-doctor-household
FAMILY_DOCTOR_TIMEZONE=Asia/Shanghai
```

`HERMES_SESSION_PROFILE` 来自当前会话的任务级上下文。若配置了旧的 `FAMILY_DOCTOR_PROFILE`，必须与它一致，冲突时插件停止处理，避免串档。不要在整个多 profile 网关的全局环境中固定某一位家人的 profile。

启用后配置的等价片段为（合并，不能覆盖已有列表）：

```yaml
plugins:
  enabled:
    - family-doctor-cognition
```

重启原有 Hermes 网关或重新创建会话，使插件和工具加载。网关部署方式沿用现有配置，本项目不启动第二个飞书机器人。若 profile 限制了可用工具集，还需允许 `family_cognition` 工具集里的 `family_cognition_prepare`、`family_cognition_record`；不能只启用 hook 却禁止记录工具。

插件无需读取历史聊天库，也无需打开消息时间注入开关。它记录新消息实际到达插件时的本机接收观察时间，不冒充平台发送时间或健康事件发生时间。第一轮通常没有足够上下文，正常交流积累后才有可评估机会。

## 两个受限工具

`family_cognition_prepare` 接收 `source_mode`（`self`/`caregiver`）、`input_mode`（`text`/`voice`）、`conditions` 以及可选的已登记 `member`。它只读取当前 ContextVar 会话的内存窗口，校验来源后返回：

- `preparation_id`：当前回合内的随机准备票据，仅作为临时工具引用；不是统计来源编号。
- `input`：含真实接收观察时间、稳定消息标识和原始文本的有限窗口，供当前模型分析。
- `prepared.analysisTemplate`：必须填写的全部九维模板。
- `time_basis: local_observed_at`：明确时间含义。

`family_cognition_record` 只接收 `preparation_id`、`analysis` 和可选 `additional_conditions`。额外条件只能追加质量限制，不能清除原条件或改变成员、来源、原文、时间；它将固定窗口与结果交给 `ChatCognitionStore.record()`。成功只返回 `{"ok":true,"saved":true}`，不回传风险等级、原句、统计内容或文件路径。重复提交仍由核心稳定键处理。

两工具都不接受任意文件路径、其他会话选择器或模型提供的原文窗口。窗口按实际成员编号、本人/家属与输入方式分段；切换目标或文本/语音时从本轮新消息重新积累，同一消息已归属后不得更改。已知质量条件按消息保留，在后续包含该消息的窗口中继续生效。核心自动加入的上下文不足可随自然交流完整而改善。不注册消息发送工具、不调用网络或其他模型，不读取健康文件。分析详解及固定条件枚举见[聊天观察流程](../../../docs/chat-analysis.md)与[九维规则](../../../skills/family-doctor/references/chat-cognition-rubric.md)。

## 回合钩子、内存限制与实际保障

使用的是已核对的 Hermes `ctx.register_hook(name, callback)`、`ctx.register_tool(...handler...)` API。本次按本机 Hermes `a55c972e0`（2026-09-15）的 `agent/turn_context.py`、`agent/turn_finalizer.py`、`gateway/session_context.py` 和 `hermes_cli/plugins.py` 核对；不依赖更新版本中的 `side_question` 或 `hold_stream`。官方接口说明见[插件](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins)和[事件钩子](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks)。

`pre_llm_call` 在主要工具循环前执行，插件把固定流程注入当前模型上下文。`post_llm_call` 在有最终回复且未中断的回合结束时执行，返回值被忽略；这里仅保留助手文字供下一轮理解上下文，不另行做推理。中断回合可能缺少助手上下文；不要从缺失内容补造答案。

每个插件进程最多缓存 128 个活跃会话，每会话 24 条消息、240 KiB 文本、每条至多 12000 字符。一小时没有新用户轮次时该窗口失效，后续访问清理过期窗口；没有持久化原文队列。超限淘汰旧上下文，不从磁盘补回。进程重启丢弃全部窗口和准备票据。记录工具再次校验当前 profile/session/message，票据不能跨会话或跨回合使用。

这提供了可执行的自动工作流程，仍依赖当前模型遵循指令。它不会硬性保证每轮必分析、每个维度都有足够机会、或者分析解释正确。没有新记录时 Web 保留资料不足，不能填成低风险。

认知提醒只在 Web：不主动向老人发送飞书风险提醒、不把内部分析附在普通回复里。直接被问及功能时如实解释；真实急症的当轮安全回应继续有效。宿主的工具历史、调试日志、流式输出、过程说明或推理显示仍可能记录或显示信息；该插件不是日志隔离器，也不能撤回已经流出的文本。启用前应核验当前部署的显示设置。详情见[隐私说明](../../../PRIVACY.md)。

## 合成验证与实际接入检查

在仓库中运行以下测试，不需要实际 Hermes profile、网关或凭据：

```sh
python3 -m unittest discover -s tests -p 'test_hermes_cognition_plugin.py' -v
python3 -m unittest discover -s tests -p 'test_chat_cognition.py' -v
```

测试用兼容本机接口的 stub 注册两个 hook/两个工具，并与真实 `ChatCognitionStore` 在临时合成家庭中联调：本人/家属归属、上下文窗口、重试时间不变、跨 profile/跨回合票据拒绝、质量追加、坏证据拒绝、内存上限及落盘无原文。它不证明真实飞书已接通。

实际启用时使用专门的合成测试 profile 和家庭目录：确认插件列表已启用、两个工具出现在允许工具中；做两轮普通合成交流，检查 prepare 返回先前助手上下文、record 仅回保存回执，Web 显示观察；重放同一轮不增加计数。然后核验飞书没有出现风险分析、过程说明、原句引用或文件附件。切换 profile 和重启后复查，确认不会继承另一人的窗口。不要用真实老人对话作为首次联调数据。

停用时执行 `hermes -p 实际profile plugins disable family-doctor-cognition`，再重启原网关。停用不会删除已经保存的去原文统计；家庭级备份/删除范围见隐私说明。
