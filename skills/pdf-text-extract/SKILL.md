---
name: pdf-text-extract
description: Extract local PDF text with page provenance and explicit incomplete states. Optional local OCR for scanned or mixed PDFs. Does not interpret or confirm health data.
---

# PDF text extraction

Run from the repository root (or use the absolute script path):

```sh
python3 skills/pdf-text-extract/tool.py report.pdf --json -o extracted.json
python3 skills/pdf-text-extract/tool.py report.pdf --json --ocr --offline --lang-path /local/models
python3 skills/pdf-text-extract/tool.py report.pdf --json --ocr-all --lang eng --cache-path /local/model-cache
```

Requirements: `pypdf` from the project dependency file. Legacy `PyPDF2` installations are supported as a fallback. OCR additionally requires Poppler's `pdftoppm`, Node.js 18+, and `npm ci` in `skills/ocr-local`. The tool never installs software automatically.

Each page first attempts text extraction. An empty text layer is labelled `needs_ocr`, which can mean a scanned page **or a blank page**. With `--ocr`, only these pages are rendered locally and OCR processed. `--ocr-all` renders every page; use it when a page has a text header but its report body is an image. Text presence alone cannot prove the whole page was captured. No automatic table reconstruction or clinical value interpretation is performed.

JSON includes `schema_version`, source absolute path, SHA-256 and size, overall `status`, and `pages`. Each page retains its 1-based `page_number`, original `text`, `extraction_method` (`pdf_text`, `ocr`, `none`), `status`, warnings and errors. OCR pages include confidence and word boxes in rendered-image pixels. Overall status is `ok`, `partial`, `needs_ocr`, `empty`, or `error`. OCR output is unverified; decimal points, units and table alignment require checking against the original.

Exit 0 means every page yielded text. Exit 2 means incomplete/error extraction with a JSON result; exit 1 means a command/output error. JSON mode deliberately preserves partial results while exiting nonzero. In plain-text mode an incomplete document fails without writing a misleading partial or empty output file. `--output` cannot overwrite the input PDF.

Default limits: 50 MB input, 50 pages, 2,000,000 extracted characters, 180 seconds overall, 60 seconds per render/OCR operation. Adjust with `--max-mb`, `--max-pages`, `--max-chars`, `--timeout`, and `--ocr-timeout`. Rendering limits the longest image edge to 3500 pixels. The supervising process cleans temporary page images even after worker timeout on POSIX systems. These bounds are practical limits, not a security sandbox for hostile PDFs.

Images and PDFs are processed locally. Without `--offline`, OCR may download missing **language models**, not upload input images. `--offline` requires uncompressed `LANG.traineddata` files in `--lang-path`, or in `--cache-path` when no language path is supplied. The default language is `chi_sim+eng`. No model is silently downloaded in offline mode. Executable locations can be supplied with `--node`, `--pdftoppm`, and `--ocr-script`.

Keep the owner's extraction output in the document staging/review flow; proxy material stays in the current conversation or temporary files. Never turn an unverified OCR string into a confirmed health measurement or medication dose automatically. A successful extraction does not mean medical accuracy or verified report completeness.

## 本人和代问诊

只有本人资料才可调用 manage_records.py 暂存或入档。代问诊的提取仅输出到当前对话或临时目录，不保存到本人 health-records，不更新本人索引或普通记忆。先确认归属再选择输出位置。
