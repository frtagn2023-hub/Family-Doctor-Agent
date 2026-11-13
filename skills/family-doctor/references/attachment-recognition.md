# 附件识别：当前模型优先，OCR 后备

适用于本人及已登记家人的体检报告、检查单、报告照片和扫描 PDF。认知观察 JSON/CSV 导入是另一条流程，不接收这些原件。先按 [WORKFLOW.md](../../../WORKFLOW.md)确定资料归属，再使用下述识别方式；不把家属上传的文件写入家属本人的档案。

## 选择识别方式

| 当前材料和能力 | 执行方式 |
|---|---|
| 有可用文字层的 PDF | 可先用 `pdf-text-extract/tool.py` 读取文字；仍需留意扫描表格和图片区域，不能只凭页眉有字就断定整页已处理。 |
| 扫描件或图片，当前模型支持视觉，且实际收到目标原图或完整页面 | 优先由当前模型直接查看并逐页转录；不要求先调用 OCR，也不另开模型服务。 |
| 当前模型无视觉能力，或宿主无法把可读图片交给它 | 使用现有本地 OCR。只有文件路径或文字描述时，不得声称自己看过原图。 |
| 当前模型只读清部分内容 | 可以放大查看原图；仍有模糊字、缺列或无法确定的字段时，用 OCR 辅助核对，未解决部分保持待核对。 |
| 原件缺失、文件损坏或两种方式都不能可靠读取 | 明确说明未处理部分与原因，不猜测补齐，也不宣称完整入档。 |

能力依据是宿主已知的当前模型能力、实际传入的图像内容及调用结果，不是模型名称或一句“我能看图”。用户要求完全离线时，只使用已配置的本地视觉能力或本地文本/OCR 工具；不要为满足优先级改用云模型。

Hermes 的图片路由可能在技能运行前调用辅助视觉模型，再将文字描述交给当前模型；这不等于当前模型亲自查看了原图。部署时核对原生图片输入和实际工具路由，不能把任意 `vision_analyze` 输出都称作当前模型识别。本仓库不改变宿主路由或关闭其既有辅助调用，详见 [Hermes 接入](../../../docs/hermes-family.md)。

## 当前模型的转录步骤

1. 获取当前附件的本地原文件，确认成员、提供者和文件对应关系。只看到飞书缩略图、截图的一角或一个文件路径时，不能据此声称覆盖了原文件全部页面。
2. 用下面的本地 `visual.py prepare` 生成真实页数、来源校验值、逐页预览和转录模板。它只准备材料，不调用 OCR 或任何模型。
3. 按清单将每页完整图像交给当前模型。已经看过原图的单页照片无须重复识别，但仍需将本轮所见图片与准备清单对应；PDF 和多帧图片不能只看第一页。
4. 逐行忠实转录文字和表格行列关系，保留正常项、异常项、单位、参考范围、日期、异常箭头、正负号、定性结论与否定词。不要先总结后丢弃原始条目，不添加原图未写出的医学解释。
5. 不清晰、裁切、未展示或无法确定行列对应时，在该页 `issues` 列出问题，并使用 `partial` 或 `error`。不要把猜测填进 `text`，也不要将缺失值填为零、正常或阴性。确实没有可转录文字时使用 `empty`；当前入档确认仍会阻止空白页，不能为了通过校验捏造文字。
6. 用 `visual.py assemble` 复核原文件、真实页数和预览，生成现有文件入档协议。模型只提交转录页，不填写文件哈希、总页数、处理方式或置信度。
7. 从逐页转录中生成待核对字段，保留对应的 `source_page`、`original_text`，数值使用真实单位，未知日期不能用上传日期补造。`confidence` 填 `null`；模型自评不是 OCR 置信度。
8. 沿用 `stage → review → confirm`。本人或代问家属对照原件核对关键字段后才能确认；这是转录核对，不是家庭访问授权。

## 本地准备与组装

从仓库根目录执行。代码路径应按实际安装位置解析；所有输出放在代码仓库之外的私人目录。`prepare` 要求一个尚不存在的输出目录，`assemble` 要求新的输出文件，避免覆盖原件或已有提取结果。

```sh
# 仅准备页面，不执行 OCR 或模型调用
python3 skills/pdf-text-extract/visual.py prepare /private/report.pdf \
  --output-dir /private/report-visual-work

# 当前模型查看生成的逐页预览后，填写模板并另存 model-pages.json
python3 skills/pdf-text-extract/visual.py assemble \
  --manifest /private/report-visual-work/manifest.json \
  --transcription /private/report-visual-work/model-pages.json \
  --output /private/report-model-extraction.json

# 示例为已登记家属上传；本人省略 --member
python3 family.py --profile 实际家属profile --member 实际成员姓名 run manage_records -- stage \
  --file /private/report.pdf --extraction-json /private/report-model-extraction.json \
  --document-type 体检报告
```

`prepare` 生成 `manifest.json`、`transcription-template.json` 和页面 PNG。PDF 需要项目 PDF 解析依赖和 Poppler `pdftoppm`；图片使用 Pillow 读取实际帧数。依赖缺失明确失败，不自动安装或调用在线转换。固定上限为原文件 50 MiB、50 页/帧、转录 2,000,000 字符；每次 PDF 页面渲染最多 60 秒，准备过程检查 180 秒总预算，但第三方解析器没有进程级硬超时隔离。预览最长边为 3500 像素，不能替代原件；识别细小文字时还需放大查看原图。命令参数见 `--help`。

模型填写的转录格式如下，示例必须替换为实际所见内容：

```json
{
  "schema_version": 1,
  "pages": [
    {
      "page_number": 1,
      "text": "此处为模型实际看清的逐行文字",
      "status": "partial",
      "issues": ["右下角表格被裁切，无法核对最后一行"]
    }
  ]
}
```

每页 `status` 为 `ok`、`partial`、`empty` 或 `error`。`ok` 必须有非空文字且 `issues` 为空；它只表示模型报告本页已转录，仍需核对。未处理页面也必须在模板中保留并标为未完成，不能删除后面的页或改小总页数。组装器拒绝重复页、漏页、伪造来源字段和与准备时不一致的原件/预览。

组装结果每页固定为 `extraction_method: model_vision`、`confidence: null`，保留 `issues`，总状态由本地代码汇总。CLI 完整结果退出码为 0，已写出部分结果为 2，输入或工具错误为 1。部分结果可以暂存但不能确认；入档时对该识别方式重新核对原件真实页数。工具能核对文件与页数，不能证明模型真的看过页面、语义理解正确或已覆盖所有医学项目。

预览、清单和转录文件都属于敏感健康资料；准备目录不是正式档案，不应提交 Git。确认原件与提取结果已成功暂存后，再按实际需要清理私人工作目录；失败时不能把仍需重试的原件删除。

## OCR 后备

只有当前模型不能查看或无法可靠转录相应内容时，再调用 OCR；已可靠识别的单页不为走流程重复 OCR。OCR 不会自动修复原图裁切、反光或缺失。

```sh
# 普通报告图片
node skills/ocr-local/scripts/ocr.js /private/report.png --json

# 没有文字层的 PDF 页面
python3 skills/pdf-text-extract/tool.py /private/report.pdf --ocr --json \
  -o /private/report-ocr-extraction.json

# 页眉有字、正文仍是扫描表格时，普通 --ocr 可能跳过该页
python3 skills/pdf-text-extract/tool.py /private/report.pdf --ocr-all --json \
  -o /private/report-ocr-all-extraction.json
```

离线 OCR 按 [OCR 说明](../../ocr-local/README.md)准备语言资源并使用 `--offline`。普通模式可能下载语言资源，但 OCR 工具不上传输入图片。

保留模型转录和 OCR 各自的结果与来源，不用后来的文字覆盖先前结果，不把 OCR 输出填进模型模板后冒称 `model_vision`。当前工具不自动融合两份提取结果；选择可核对的完整提取结果继续入档，另一份保留作核对依据。两者数值不一致时对照原件；仍不清楚则继续待核对。重复原件的来源与差异按既有文档去重流程保留。

## 范围与现有边界

- 识别医生的影像文字报告与分析 X 光、CT/MRI 影像内容是不同任务。本流程处理文字转录，不新增影像诊断或 DICOM 支持。
- `status: ok` 不等于整份资料无遗漏。真实页数校验可发现漏页，但不能发现所有漏行、错字或错误医学解释。
- 文档确认后进入成员的文档档案，不会自动把所有指标同步到体征趋势，也不会自动变成认知观察。
- 本地准备工具不替代原件接收服务。当前仍在 `stage` 成功时正式保存原件；不支持或无法解析的材料尚无通用“只保存原件”入口。
- 未登记对象的临时代问诊和旧个人模式的非本人资料仍不进入已登记成员档案；家庭内已登记家属通过正确目标成员路由保存，不一概排除代问资料。
