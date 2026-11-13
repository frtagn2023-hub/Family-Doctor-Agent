# 安装、接入与迁移

## 1. 获取代码和选择数据目录

本家庭的推荐接入方式见 [Hermes 家庭接入](docs/hermes-family.md)：共享 FAMILY_DOCTOR_FAMILY_HOME，通过 family.py 登记姓名与 profile，再按成员调用工具，不需要额外授权服务。每个 Hermes profile 保留自己的 HERMES_HOME。下面的直接 doctor.py 示例是兼容的个人模式，不能在多个家庭成员间共用同一个个人健康目录。

```sh
git clone https://github.com/frtagn2023-hub/Family-Doctor-Agent.git
cd Family-Doctor-Agent
export FAMILY_DOCTOR_HOME="$HOME/.local/share/family-doctor"
export FAMILY_DOCTOR_TIMEZONE="Asia/Shanghai"
python3 doctor.py init --role self
python3 doctor.py check --role self
```

核心工具使用 Python 3.9+ 标准库。`init` 可重复执行，只创建缺失模板，验证而不覆盖已有档案。真实资料不写入 `templates/`。未设置 FAMILY_DOCTOR_HOME 时默认使用仓库根目录下的 health-records。

个人模式的脚本与宿主进程使用同一 FAMILY_DOCTOR_HOME。家庭模式共享 FAMILY_DOCTOR_FAMILY_HOME，family.py 为每次操作选择成员目录，不把各成员原有个人目录混合为一份档案。已有旧个人资料保留在原位置，不因新增家庭登记而自动搬迁。

## 2. 可选文件功能

推荐使用独立虚拟环境；核心功能不需要执行本节。

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

`pypdf` 用于 PDF 文本层解析，`weasyprint` 用于报告渲染。Python 直接依赖版本已固定；系统库和字体由操作系统提供。已有 PyPDF2 仅作为兼容后备，不是新安装推荐路径。

PDF 报告需要 Pango 等系统渲染库和可显示中文的字体。按 [WeasyPrint 官方安装说明](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation) 安装对应平台依赖。macOS 可使用 Homebrew 提供的 Pango；受系统保护的 Python 可能无法读取动态库环境变量，建议使用独立 Python 及虚拟环境。缺依赖时导出会明确失败并返回非零，不会产生伪 PDF。

本次在 macOS、独立 Python 3.11 虚拟环境和 Homebrew Pango 下验证了实际中文 PDF。若动态库已安装但加载不到，可在启动该独立 Python 前设置 `DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib"`；不要假设系统 Python 会保留这个变量。`doctor.py check` 区分渲染器“已安装”和“能加载”。

```sh
python skills/family-doctor/scripts/generate_health_report.py --role self --pdf
# 或仅 PDF
python skills/family-doctor/scripts/generate_health_report.py --role self --pdf-only --output /path/to/report.pdf
```

图片 OCR 需要 Node.js 18+，推荐使用已验证的 Node 22。扫描 PDF 另需 Poppler 的 `pdftoppm`。

```sh
npm ci --ignore-scripts --prefix skills/ocr-local
npm test --prefix skills/ocr-local
node skills/ocr-local/scripts/ocr.js /path/to/image.png --json
python skills/pdf-text-extract/tool.py /path/to/report.pdf --ocr --json -o /path/to/extraction.json
```

`--ocr` 仅处理没有可读文本的页面；页面有部分文本层但仍含扫描表格时，核对后可使用 `--ocr-all`。OCR 默认可能下载语言资源，图片本身在本地处理。完全离线所需的语言文件和 `--offline` 参数见 `skills/ocr-local/README.md`。提取结果含敏感原文，保存路径也应放在私人目录。

## 3. Agent 宿主接入

仓库提供文件和 CLI 工具，不内置 Hermes/OpenClaw 服务，不自动修改其全局配置。将仓库作为宿主工作区，按宿主文档配置 AGENTS.md/skills 加载，并将数据目录环境变量传给其工具进程。

接入后用合成资料检查：本人记录写入自己的目录；替已登记家人咨询只写入目标成员目录并保留代述来源；未登记者不入档；重复问诊按实际发生时间整理；紧急情况优先于首次引导。网页搜索、模型处理位置和提醒调度属于宿主能力，不能仅凭技能文件存在就视为已启用。

文件管理使用同一家庭路由，不需安装另一个服务。用 `family.py --profile 实际profile files --rebuild` 生成全家总览；通过 `family.py --member 姓名 run file_manager -- list` 查看成员目录。重复上传、事件关联、归档恢复和导出验证见 [家庭文件管理](docs/family-files.md)。

认知统计 Web 页面同样只依赖 Python 标准库。运行 `python3 web_server.py --demo` 查看合成演示并测试导入预览；运行 `python3 web_server.py --home /实际家庭目录 --profile 实际profile` 查看并导入该家庭的结构化观察。仅供同机浏览器访问，不显示一般健康文件和问诊目录。JSON/CSV 模板、校验、去重见 [认知统计与导入](docs/web-dashboard.md)。

聊天观察由当前 Hermes 模型按九维规则完成，本地 `chat-prepare/chat-record/chat-stats` 校验与统计；日常交流可按 [插件接入说明](integrations/hermes/family-doctor-cognition/README.md) 启用回合工作流。插件仅在进程内保留有限窗口，使用真实接收观察时间，不扫描历史聊天库，不另开模型服务。仓库不会自动修改真实 profile。认知提醒只出现在 Web，禁止主动飞书风险提醒；直接询问功能时如实解释，正常急症安全回应保留。

## 4. 旧数据兼容

- 原 `health-records/user-profile.json`、`medications.json`、`vitals.json` 可以保留在原目录。
- 用药 dosage/ongoing 与 dose/status 通过共享层统一；修改时保留旧文件备份。
- 旧版体征分类字典转换为列表，缺失单位保留未知，不猜测单位。
- 旧 USER.md 只用于读取真实已填写姓名，不再写入健康信息。初始化不会清空过敏史、慢病或联系人。
- 旧问诊索引没有 role，无法可靠区分本人/代问诊，因此报告排除这些记录并显示数量。核对原记录后再明确迁移；不要自动把全部历史标为本人。
- 损坏 JSON、非有限数值和结构错误会停止操作；先从备份恢复或人工核对，不能用空模板重置。
- 新建家庭不会自动导入旧 profile 的档案。迁移前保留完整旧目录与备份，逐份核对档案主人和来源；不能把所有旧目录合并覆盖到一家人的 user-profile.json，也不能仅凭同名补写成员编号。来源不明的旧问诊继续保留待整理，不自动参加重复事件或认知统计。

## 5. 备份、导出与删除

每次文件更新保留旧版备份，需按实际使用检查磁盘容量。导出前暂停其他写入，以得到一致快照。

```sh
python3 doctor.py export --role self --output /private/path/health-backup.zip
```

导出是普通本地 ZIP，含原始资料和历史备份，不加密、不上传。删除命令只删除配置目录下的 health-records，详见 PRIVACY.md；不会处理宿主会话、旧 USER.md 或外部导出。

## 6. 开发验证

```sh
python3 -m unittest discover -s tests -v
npm test --prefix skills/ocr-local
node --test tests/test_web_frontend.cjs
```

测试会为每个流程建立临时 FAMILY_DOCTOR_HOME，不应指定真实健康目录。CI 运行 Python 与 Node 回归测试。医疗判断仍需要独立、专业审核的评估集；现有测试只验证软件行为和数据边界。
