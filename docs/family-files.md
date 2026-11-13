# 家庭文件管理

本项目在同一台机器上供一个家庭使用，各人通过自己的 Hermes profile 和飞书交流。文件管理沿用已经登记的姓名与成员档案，不增加授权审批或另一套账号。日常由 Hermes 根据姓名和资料标题选择目标；内部文件编号供工具操作，默认目录不展示。

## 两级目录

- 家庭目录：`family.py --profile 当前profile files --rebuild`，按姓名显示每位成员的文档、事件、待核对和归档数量，生成共享目录中的 catalog.md。
- 成员目录：通过 `family.py --member 目标姓名 run file_manager -- ...` 查看该成员资料。成功写入后自动更新该成员 health-records/catalog.md；单独 rebuild 可恢复展示。

目录包含检查文档、健康事件、问诊来源、生成报告、健康台账与问诊进度。默认按事件组织问诊，不将同一事件的本人和家属来源显示为多次独立发作。原文件、完整来源、待核对状态和历史备份继续保留。

姓名和资料标题用于浏览，文件实际位置仍用稳定编号和内容哈希，避免重名或文件改名破坏关联。家庭总览是可重建快照；筛选单个成员重建时只更新该成员目录，不覆盖已有全家总览。

```sh
# 以下姓名/profile 是示例，请替换为实际登记
python3 family.py --profile daughter files --rebuild
python3 family.py --profile daughter --member '张明' run file_manager -- list
python3 family.py --profile daughter --member '张明' run file_manager -- list --kind document --status pending
python3 family.py --profile daughter --member '张明' run file_manager -- list --sources
```

`list --json` 返回完整定位编号供 Hermes 使用，列表不包含整页 OCR 原文。`show 定位编号` 查看明确选中的文档字段和来源；不要无条件读取全家的完整资料。

## 上传与重复资料

原文件经本地 PDF/OCR 提取后，按原流程 stage → review → confirm。`--reviewed` 表示转录已对照原文核对，不是访问授权。

```sh
python3 family.py --profile daughter --member '张明' run manage_records -- stage \
  --file /private/report.pdf --extraction-json /private/extraction.json \
  --fields-json /private/fields.json --document-type '检验结果' \
  --title '本次复诊检查' --document-date 2026-11-01 \
  --request-id '实际飞书消息ID:附件编号'
python3 family.py --profile daughter --member '张明' run manage_records -- confirm 实际上传编号 --reviewed
```

实际文档日期未知时省略 document-date，不能用上传当天填充。标题仅用于资料管理，不据标题猜测诊断。稳定 request-id 用于同一附件重试；一条消息有多个附件时使用不同附件后缀。

同一成员上传完全相同的文件内容，复用一份原件和一份正式文档。更换文件名、扩展名、上传人或分类不会产生第二份正式文档。每次上传仍保留原文件名、提供者、上传时间、报告日期、核对者和该次提取的内容。

例如，本人上传一次，家属随后又上传同一报告，目录显示一份报告、两个来源。第二次识别数值、日期或分类不同，会记录差异并提示核对，不覆盖原已核对字段。恢复中断操作时，以已经发布的文档及来源索引为准，不用旧的待核对副本反写正式信息。

不同照片、不同扫描件或修订版本即使看起来相似也不自动合并；同名不同内容仍是独立文件。不同家庭成员的文件不会跨成员去重或自动改变归属。

## 找资料时区分两种日期

默认筛选实际检查/文档日期或症状发生时间。生成报告只有明确标注的文件更新时间，不会把它当作检查日期。日期未知的资料仍可在无日期筛选的列表中找到，不伪造日期以满足搜索。

```sh
# 找实际发生在这段时间内的资料
python3 family.py --profile daughter --member '张明' run file_manager -- list \
  --from 2026-11-01 --to 2026-11-10 --query '检查'
# 找这段时间上传或记录的资料
python3 family.py --profile daughter --member '张明' run file_manager -- list \
  --from 2026-11-01 --to 2026-11-10 --date-basis recorded
```

事件有多个不连续发生区间时，按各个区间匹配，不把中间没有症状的日期当作持续发生。带不同时区的时间按实际时间点比较，原始时间表述保留。

## 报告与健康事件关联

资料确认属于某次症状或就诊时，通过 link 保存事实依据。日期相近只是一条线索，不自动断定归属；同一报告也可能与多个事件有关。

```sh
python3 family.py --profile daughter --member '张明' run file_manager -- link \
  document:实际文档编号 episode:实际事件编号 --basis '该检查由这次症状就诊开具，日期与就诊记录已核对'
python3 family.py --profile daughter --member '张明' run file_manager -- unlink \
  document:实际文档编号 episode:实际事件编号 --basis '核对后发现属于另一次就诊'
```

关联实际锚定原始问诊来源，因此事件主档合并、拆分后仍跟随相关来源。取消一个拆分事件的关系不会丢掉其他来源的关系。文件目录、事件主档和就诊报告会显示关联资料、提供者、原文件链接及差异提示。

## 归档和恢复

归档只让资料退出默认文件列表，不删除原件、上传来源或已经核对的健康事实。就诊报告仍保留医疗依据；归档用药台账也不代表停药。

```sh
python3 family.py --profile daughter --member '张明' run file_manager -- archive \
  document:实际文档编号 --reason '资料已整理完成，收起默认列表'
python3 family.py --profile daughter --member '张明' run file_manager -- list --status archived
python3 family.py --profile daughter --member '张明' run file_manager -- restore \
  document:实际文档编号 --reason '需要重新查看'
```

上传来源编号可定位到同一份文档，不会出现只隐藏一个重复副本的情况。待核对资料转为正式文档时保留已有归档状态。归档事件收到新问诊来源后重新显示；拆分继承原状态，合并时只要有活动事件就保持可见。操作与依据保留在历史中。

## 按事件导出

```sh
python3 family.py --profile daughter --member '张明' run file_manager -- export \
  episode:实际事件编号 --output /private/exports/本次就诊资料.zip
```

导出包包含 event.md、事件结构化记录、原始问诊来源、明确关联的报告原件及完整上传信息、逐文件 SHA256 清单。主档内的链接可以在解压后使用。归档的相关文档也会保留并标注；无关事件、其他成员、全量档案和历史备份不进入此包。

目标是家庭数据目录之外的新文件，不覆盖已有导出。文件缺失、原件内容变化或路径异常会停止导出，不发布半成品。包是普通本地 ZIP，不自动上传、不加密。完整成员备份继续使用 doctor export，范围不同。

## 完整性与恢复

```sh
python3 family.py --profile daughter --member '张明' run doctor -- check
python3 family.py --profile daughter --member '张明' run file_manager -- rebuild
python3 family.py --profile daughter files --rebuild
```

check 检查 JSON、成员关联、原文件内容、问诊来源和活动文档关系，发现问题会列出并保留原数据。rebuild 只从权威索引恢复展示，不能补回丢失的原始扫描件，也不能自动裁定不同来源的医学矛盾。

成功写入后若目录生成失败，工具会明确提示“资料已保存，展示待更新”，不把已保存的内容当作新提交再次入档。旧目录、备份、Hermes 会话不会因新增文件功能自动迁移或删除。仍只开源代码和合成测试，真实家庭目录及导出放在仓库外。
