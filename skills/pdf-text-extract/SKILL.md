---
name: pdf-text-extract
description: Prepare PDF/image pages for the current vision-capable model and validate its transcription; extract PDF text or use local OCR when model vision is unavailable or insufficient. Preserve provenance and incomplete states, without diagnosing or confirming health data.
---

# PDF text extraction

Follow [attachment recognition](../family-doctor/references/attachment-recognition.md). For scans and images, first let the current model transcribe when it supports vision and actually receives the original image or complete page. A path, thumbnail, extracted text or auxiliary-model description is not proof that the current model saw the source. Use local OCR only when current-model vision is unavailable, the host cannot deliver the image, or recognition remains incomplete. Respect offline requirements. Text PDFs may still use direct text extraction; a readable header does not imply a scanned body was captured.

For current-model transcription, `visual.py prepare INPUT --output-dir NEW_PRIVATE_DIR` creates real source metadata, page previews and a transcription template without calling OCR or a model. After the current model has viewed the pages, use `visual.py assemble --manifest MANIFEST --transcription TRANSCRIPTION --output NEW_JSON` to validate and convert its per-page text/status/issues into `model_vision` extraction JSON. Do not invent file hashes, page counts or confidence scores. See the linked workflow for the exact format, optional dependencies and staging. Partial results exit 2 and may be staged, but cannot be confirmed.

Run from the repository root (or use the absolute script path):

```sh
python3 skills/pdf-text-extract/tool.py report.pdf --json -o extracted.json
python3 skills/pdf-text-extract/tool.py report.pdf --json --ocr --offline --lang-path /local/models
python3 skills/pdf-text-extract/tool.py report.pdf --json --ocr-all --lang eng --cache-path /local/model-cache
```

Requirements: `pypdf` from the project dependency file. Legacy `PyPDF2` installations are supported as a fallback. OCR additionally requires Poppler's `pdftoppm`, Node.js 18+, and `npm ci` in `skills/ocr-local`. The tool never installs software automatically.

The following describes `tool.py`, the text/OCR utility; it does not detect model capabilities or choose the host's image route. Each page first attempts text extraction. An empty text layer is labelled `needs_ocr`, which can mean a scanned page **or a blank page**; at workflow level a capable current model may read that page before OCR. With explicit `--ocr`, only these pages are rendered locally and OCR processed. `--ocr-all` renders every page; use it as the OCR fallback when a page has a text header but its report body is an image. Text presence alone cannot prove the whole page was captured. No automatic table reconstruction or clinical value interpretation is performed.

JSON includes `schema_version`, source absolute path, SHA-256 and size, overall `status`, and `pages`. Each page retains its 1-based `page_number`, original `text`, `extraction_method` (`pdf_text`, `ocr`, `none`), `status`, warnings and errors. OCR pages include confidence and word boxes in rendered-image pixels. Overall status is `ok`, `partial`, `needs_ocr`, `empty`, or `error`. OCR output is unverified; decimal points, units and table alignment require checking against the original.

Exit 0 means every page yielded text. Exit 2 means incomplete/error extraction with a JSON result; exit 1 means a command/output error. JSON mode deliberately preserves partial results while exiting nonzero. In plain-text mode an incomplete document fails without writing a misleading partial or empty output file. `--output` cannot overwrite the input PDF.

Default limits: 50 MB input, 50 pages, 2,000,000 extracted characters, 180 seconds overall, 60 seconds per render/OCR operation. Adjust with `--max-mb`, `--max-pages`, `--max-chars`, `--timeout`, and `--ocr-timeout`. Rendering limits the longest image edge to 3500 pixels. The supervising process cleans temporary page images even after worker timeout on POSIX systems. These bounds are practical limits, not a security sandbox for hostile PDFs.

Images and PDFs are processed locally. Without `--offline`, OCR may download missing **language models**, not upload input images. `--offline` requires uncompressed `LANG.traineddata` files in `--lang-path`, or in `--cache-path` when no language path is supplied. The default language is `chi_sim+eng`. No model is silently downloaded in offline mode. Executable locations can be supplied with `--node`, `--pdftoppm`, and `--ocr-script`.

Keep the selected member's extraction output in the document staging/review flow, with provider and subject recorded separately. Never turn unverified model or OCR text into a confirmed health measurement or medication dose automatically. A successful extraction does not mean medical accuracy or verified report completeness.

## 本人和代问诊

本人资料通过当前 profile 的家庭路由入档；已登记家属代问上传时，通过 `family.py --member 目标姓名 run manage_records -- ...` 保存到目标成员目录，保留实际提供者，不写进提供者自己的档案。未登记对象的临时代问诊不落盘；未配置家庭模式时，仍只允许本人资料入个人档案。先确认归属再选择输出位置，健康资料不写普通记忆。
