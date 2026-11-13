# 工具与数据位置

工具清单及使用边界见 `skills/family-doctor/SKILL.md`；唯一问诊流程见 `WORKFLOW.md`。

- Python 核心管理脚本仅依赖标准库。
- `web_server.py` 只提供本机认知观察统计、Web 复核提示和结构化导入。`cognition.py validate/import` 的 JSON/CSV 格式不接收原文；预览、去重和整批保存见 `docs/web-dashboard.md`。
- `cognition.py chat-prepare/chat-record/chat-stats` 接收 stdin JSON，由 Hermes 当前模型完成九维分析；本地内存核验原句，去原文结果写入独立聊天观察库。可选 `integrations/hermes/family-doctor-cognition/` 注册回合 hook 与两个受限工具，有限缓存启用后的当前对话，不读取历史库、不另调模型。详见 `docs/chat-analysis.md`。认知提醒仅 Web，禁止飞书主动风险通知；实际急症回应仍有效。
- `file_manager` 提供统一文件目录、实际日期检索、文档与事件关联、归档恢复和按事件导出；`family.py files --rebuild` 生成按姓名的家庭总览。原始健康事实与文件显示状态分开。
- 附件按 `skills/family-doctor/references/attachment-recognition.md` 选择识别方式：文字 PDF 可文本提取，扫描件/图片优先当前模型实际可用的视觉能力，不能查看或识别受限才用本地 OCR。`skills/pdf-text-extract/visual.py prepare/assemble` 只准备真实页数/预览与校验模型转录，不调用模型；依赖与自检见 `INSTALL.md`。关键字段仍需核对。
- 医学检索需要宿主提供网页搜索/读取工具，遵循 `skills/cn-web-search/SKILL.md`。
- 本仓库没有 LLM 服务、聊天界面、后台调度器或完整药学数据库。
- 家庭模式配置共享 `FAMILY_DOCTOR_FAMILY_HOME`，通过 `family.py run` 为每次调用选择成员级 `FAMILY_DOCTOR_HOME`；不合并 Hermes profile。个人模式仍使用 FAMILY_DOCTOR_HOME。真实资料不进入仓库模板。
