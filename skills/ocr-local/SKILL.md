---
name: ocr-local
description: Extract text from local images with Tesseract.js, word coordinates, confidence and original-file provenance. Optional offline language models. OCR text requires confirmation before health-record entry.
metadata:
  homepage: https://github.com/naptha/tesseract.js
---

# Local image OCR

Install once with `npm ci --ignore-scripts` in this skill directory. Requires Node.js 18+. No health image is uploaded by this tool. Without offline mode, missing language models may be downloaded from Tesseract's CDN.

```sh
node {baseDir}/scripts/ocr.js /path/to/report.png --json
node {baseDir}/scripts/ocr.js /path/to/report.png --lang eng --json
node {baseDir}/scripts/ocr.js /path/to/report.png --json --offline --lang-path /local/models
```

Use `--lang chi_sim+eng`, `chi_tra+eng`, or other installed Tesseract language codes. `--lang-path` accepts only a local directory containing uncompressed `LANG.traineddata` files. `--offline` requires those files in that directory, or in `--cache-path`; missing models fail explicitly without a network fallback. Ordinary mode caches downloaded models in `--cache-path` (default: the OS temporary directory's `family-doctor-ocr-cache`). Use a persistent private cache directory if desired.

`--json` returns the original file's path/SHA-256/size, text, confidence (0–100 or null), per-word text/confidence/pixel bounding boxes, and a one-page `pages` array with source text. The page box encloses recognized words; it is not the full image boundary. Status is `ok`, `empty`, or `error`. Empty recognition exits 2; errors exit 1; text recognition exits 0. JSON errors are machine readable.

`--max-mb` defaults to 20 MB and `--timeout` to 90 seconds. Both must be positive. Timeouts cover worker initialization as well as recognition; workers terminate after use. Filesystem locations are independent of the Agent workspace. See `--help` for the complete interface.

This is transcription, not medical interpretation. Review low-confidence fields and all medical numbers, decimal points and units against the image. Stage only the owner's extracted material for human confirmation; proxy material stays in the current conversation or temporary files. Never treat `status: ok` or a high OCR confidence as a verified medical fact. A confidence score is an OCR estimate, not a clinical probability.

## 本人和代问诊

只有本人资料才可调用 manage_records.py 暂存或入档。代问诊的提取仅输出到当前对话或临时目录，不保存到本人 health-records，不更新本人索引或普通记忆。先确认归属再选择输出位置。
