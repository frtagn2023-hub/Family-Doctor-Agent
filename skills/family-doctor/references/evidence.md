# 医学资料与证据记录

根据问题选择来源：官方药品说明书用于药品成分和已批准用法；专业指南用于诊疗建议；官方医疗机构资料用于患者教育；原始研究需要判断人群与适用条件。搜索引擎和百科可用于定位术语，不直接作为具体用药调整的依据。

每条关键建议保留：

```json
{
  "claim": "需要支持的具体表述",
  "source_type": "guideline | drug_label | institution | primary_research",
  "title": "原文标题",
  "url": "可打开的原始页面链接",
  "publisher": "发布机构",
  "published_on": "YYYY-MM-DD 或 unknown",
  "accessed_on": "YYYY-MM-DD",
  "population": "资料适用的人群与限制",
  "supporting_text": "支持该表述的短原文或位置",
  "verification_status": "verified | unavailable | conflicting"
}
```

只有实际打开原文并确认支持关系后才能标 verified。未注明发布日期时保留 unknown；不能编造引用、日期、概率或适用人群。报告中的 AI 可能原因与医生已确诊诊断分别展示。证据冲突时说明冲突并降低结论确定性。

相互作用脚本的内置规则没有完整性保证，也不是经过临床验证的药学数据库；只作为待专业人员核验的提示。中英文成分别名归一化仅解决名称匹配，不证明规则覆盖了全部风险。
