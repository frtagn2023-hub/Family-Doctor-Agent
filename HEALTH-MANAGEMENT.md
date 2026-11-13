# 家庭健康文件管理

家庭模式以 FAMILY_DOCTOR_FAMILY_HOME 为共享根目录。family.json 关联姓名与 Hermes profile，各成员各有独立健康目录。通过 family.py 选择成员，家属代问诊写入被咨询成员档案，提供者保持独立。没有授权审批服务。旧个人模式以 FAMILY_DOCTOR_HOME 为根目录，未关联的代问诊不持久化。

```text
共享家庭目录/                 # 放在开源代码仓库之外
  family.json                 # 姓名、稳定成员编号、profile 关联
  catalog.md                  # 按姓名汇总全家文件目录的可重建快照
  members/
    m_<内部编号>/
      health-records/         # 下列资料树，每位成员各一份
```

```text
health-records/
  user-profile.json    # 本人档案和已确认资料索引
  catalog.md          # 按资料标题/实际日期浏览的统一目录，隐藏内部定位编号
  medications.json    # 用药记录，统一 dose/status 字段
  vitals.json         # 带单位、测量时间的体征列表
  symptoms.json       # 症状记录
  raw/                # 按 SHA256 保存的原始文件
  imports/            # 待核对与已确认的解析记录
  sessions/           # 本人问诊阶段与事实状态
  consultations/      # 唯一编号、保留来源的问诊原始记录
    episodes/         # 按发生时间归并后的事件主档，可重建
  reports/            # 生成的报告
  .backups/           # 同级文件更新前的备份；各子目录也可有此目录
```

## 附件入档

原文件 → 按页提取 → stage 暂存 → review 核对字段 → confirm 确认 → 正式索引。

确认前验证原文件哈希、页码、对应原文、日期及数值单位。提取不完整时不能确认。同一成员的相同原文件复用一份内容和正式条目，各次上传保留来源。文件改名、扩展名或分类变化不制造新文档；识别值或元数据不同保留差异，不覆盖已核对字段。不同扫描/照片不凭相似内容自动去重，原文不会因字段修订而被覆盖。

资料标题、实际检查/报告日期与上传时间分开记录。目录含待核对资料、正式文档、健康事件、生成报告、用药/体征/症状台账和问诊进度；不会把重复上传或来源文件数量当作健康事件数。命令和实例见 [家庭文件管理](docs/family-files.md)。

## 关联、归档与导出

file_manager link/unlink 将文档关联到相关问诊来源，记录依据。事件合并/拆分后引用跟随原始来源，不仅依赖可能退役的事件编号。报告与事件主档展示相关原件、提供者及尚存差异。

archive/restore 只调整目录显示，保留所有原文件和医疗依据；默认目录隐藏归档，显式选择 archived/all 可查。归档的事件新增问诊来源后重新显示；拆分继承归档状态，合并包含未归档来源时保持可见。

file_manager export 输出一个事件的资料包：事件主档、原始问诊及结构化记录、明确关联的原文件与文档来源信息、SHA256 清单。不会带入无关事件、其他成员或完整家庭备份。导出前核对相关原件存在且内容未变；目标必须为数据目录之外的新文件。doctor export 则是选定成员全量备份，包含历史备份。

资料来源和确认方式见 `skills/family-doctor/references/data-model.md`。文件自动备份保留在数据目录中，随 `doctor.py export` 一起导出；定期检查容量。删除时确认包含自动备份，不影响其他位置的手动导出。

## 兼容与恢复

问诊原始文件不因合并而删除。user-profile.json 内 consultations 保存完整来源，consultation_episodes 保存归并关系；两者在同一文件锁下更新，Markdown 作为派生展示。合并、拆分留有历史，主档损坏或中途写入失败可通过 add_consultation rebuild 恢复。详细字段与时间判断见 [事件整理](docs/family-events.md)。问诊交流数与整理后的事件条目数分开。

共享存储层兼容旧版 dose/dosage、status/ongoing、体征列表/分类字典。读取规范化；下一次修改写入统一版本，原文件先备份。旧体征缺失单位时保留未知，不猜测。损坏 JSON 会报错并保留原文件。

旧问诊缺少角色或归属不明确时不自动汇总。人工核对为本人后再迁移。所有更新应通过脚本进行；多进程写入采用文件锁和原子替换。多文件导出前暂停其他写入，以得到一致快照。

成员目录随成功写入自动更新，也可 file_manager rebuild；家庭总览用 family.py files --rebuild。目录是展示快照，损坏时从权威索引重建，不根据目录文本反写医疗事实。doctor check 会检查原件哈希、活动关联、问诊来源缺失等问题，并保留原资料供恢复。
