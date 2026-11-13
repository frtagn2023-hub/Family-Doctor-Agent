---
name: ocr-local
description: Local OCR fallback when the current model cannot view or reliably transcribe a report image. Uses Tesseract.js with word coordinates, confidence and provenance; supports offline language data. Extracted text still needs confirmation.
metadata:
  homepage: https://github.com/naptha/tesseract.js
---

# Local image OCR

For scans and images, follow [attachment recognition](../family-doctor/references/attachment-recognition.md): prefer the current model's own visual transcription when it supports vision and actually received the target image or complete pages. Do not run OCR first just because a document is an image. Use this local tool when current-model vision is unavailable, the host cannot deliver the image, or specific content remains unreadable. A path or another model's description is not visual access. Completely offline requirements take precedence over the model-first preference.

Install once with `npm ci --ignore-scripts` in this skill directory. Requires Node.js 18+. No health image is uploaded by this tool. Without offline mode, missing language models may be downloaded from Tesseract's CDN.

```sh
node {baseDir}/scripts/ocr.js /path/to/report.png --json
node {baseDir}/scripts/ocr.js /path/to/report.png --lang eng --json
node {baseDir}/scripts/ocr.js /path/to/report.png --json --offline --lang-path /local/models
```

Use `--lang chi_sim+eng`, `chi_tra+eng`, or other installed Tesseract language codes. `--lang-path` accepts only a local directory containing uncompressed `LANG.traineddata` files. `--offline` requires those files in that directory, or in `--cache-path`; missing models fail explicitly without a network fallback. Ordinary mode caches downloaded models in `--cache-path` (default: the OS temporary directory's `family-doctor-ocr-cache`). Use a persistent private cache directory if desired.

`--json` returns the original file's path/SHA-256/size, text, confidence (0–100 or null), per-word text/confidence/pixel bounding boxes, and a one-page `pages` array with source text. The page box encloses recognized words; it is not the full image boundary. Status is `ok`, `empty`, or `error`. Empty recognition exits 2; errors exit 1; text recognition exits 0. JSON errors are machine readable.

`--max-mb` defaults to 20 MB and `--timeout` to 90 seconds. Both must be positive. Timeouts cover worker initialization as well as recognition; workers terminate after use. Filesystem locations are independent of the Agent workspace. See `--help` for the complete interface.

This is transcription, not medical interpretation. Review low-confidence fields and all medical numbers, decimal points and units against the image. Stage the selected member's extracted material for human confirmation, retaining the actual provider. Keep model and OCR results separate when they disagree; never relabel OCR text as current-model visual transcription. Never treat `status: ok` or a high OCR confidence as a verified medical fact or complete coverage. A confidence score is an OCR estimate, not a clinical probability.

## 本人和代问诊

本人通过当前 profile 进入本人档案；替已登记家人上传时，通过 `family.py --member 目标姓名 run manage_records -- ...` 保存到目标成员目录，并保留实际提供者。未登记对象的临时代问诊不落盘，旧个人模式仍不接纳非本人资料。先确认归属，不写入普通记忆或误存到提供者的个人目录。
