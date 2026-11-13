# Local OCR

The repository's image transcription tool uses pinned Tesseract.js 7.0.0 and its current `blocks` output. It extracts text and provenance without uploading source images.

This is the fallback in the [attachment workflow](../family-doctor/references/attachment-recognition.md). For scans and report photos, the current model should first transcribe the actual image when its visual capability and access are available. Run local OCR when the model cannot view the image or recognition remains incomplete; do not infer capability from a filename or another model's description. Offline requirements remain in force.

From the repository root:

```sh
npm ci --ignore-scripts --prefix skills/ocr-local
node skills/ocr-local/scripts/ocr.js image.png --lang eng --json
npm test --prefix skills/ocr-local
```

Requires Node.js 18 or newer. `--help` and unit tests do not need a language-model download. The lockfile makes dependency installation reproducible. `wasm-feature-detect` is overridden to 1.8.0 because 1.10.0 includes `import.source` syntax that fails to parse on the tested Node.js 22 runtime.

For offline operation, provision uncompressed language models such as `eng.traineddata` and `chi_sim.traineddata` locally, then run:

```sh
node skills/ocr-local/scripts/ocr.js image.png --json --offline --lang-path /local/models
```

Alternatively, first use ordinary mode with a chosen `--cache-path` to download the required models, then reuse that directory with `--offline --cache-path`. Downloads fetch language models; the tool does not send the input image. Language selection defaults to `chi_sim+eng`; use `--lang eng` if only the English model is provisioned. An explicit `--lang-path` also selects local-only models.

Results contain text, word coordinates, confidence, page metadata and the source SHA-256. Default input size/total time limits are 20 MB/90 seconds (`--max-mb`, `--timeout`). The JSON contract, error handling and review requirements are described in [SKILL.md](SKILL.md). OCR confidence does not certify medical accuracy or complete coverage. When OCR fallback is needed for PDF scans, use the repository's PDF extractor with explicit `--ocr`; mixed text/image pages may need `--ocr-all`.

Tests cover v7 word extraction, provenance, offline model checks, argument errors, size/time limits and worker cleanup. They use synthetic fixtures and fake workers; actual OCR needs installed models. No model binaries or health records belong in the repository.
