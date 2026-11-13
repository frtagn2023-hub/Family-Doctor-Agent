#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');

const HELP = `Usage: node ocr.js IMAGE [options]
Recognizes a local image; does not interpret or save medical facts.
  --lang LANGS        Languages joined by + (default: chi_sim+eng)
  --json              Structured text, provenance, confidence and word boxes
  --lang-path DIR     Local directory of uncompressed *.traineddata files
  --cache-path DIR    Language cache (default: OS temp/family-doctor-ocr-cache)
  --offline           Require local language files; never download models
  --timeout SECONDS   Total timeout (default: 90)
  --max-mb MB         Maximum input size (default: 20)
  -h, --help          Show this help
Exit: 0 = text extracted, 2 = empty image, 1 = error.
Without --offline, missing language models may be downloaded; images stay local.`;

function positiveNumber(value, flag) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) throw new Error(`${flag} requires a positive number`);
  return number;
}

function parseArgs(argv) {
  const options = { lang: 'chi_sim+eng', json: false, offline: false, timeout: 90,
    maxMb: 20, cachePath: path.join(os.tmpdir(), 'family-doctor-ocr-cache') };
  const flags = { '--lang': 'lang', '--lang-path': 'langPath', '--cache-path': 'cachePath',
    '--timeout': 'timeout', '--max-mb': 'maxMb' };
  let positional = false;
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (!positional && (arg === '--help' || arg === '-h')) { options.help = true; continue; }
    if (!positional && arg === '--') { positional = true; continue; }
    if (!positional && arg === '--json') { options.json = true; continue; }
    if (!positional && arg === '--offline') { options.offline = true; continue; }
    if (!positional && flags[arg]) {
      const value = argv[++i];
      if (!value || value.startsWith('--')) throw new Error(`Missing value for ${arg}`);
      options[flags[arg]] = ['--timeout', '--max-mb'].includes(arg) ? positiveNumber(value, arg) : value;
      continue;
    }
    if (!positional && arg.startsWith('-')) throw new Error(`Unknown option: ${arg}`);
    if (options.image) throw new Error('Supply exactly one local image');
    options.image = arg;
  }
  if (!options.help && !options.image) throw new Error('A local image path is required');
  if (!/^[a-zA-Z0-9_]+(?:\+[a-zA-Z0-9_]+)*$/.test(options.lang)) throw new Error('Invalid language code');
  return options;
}

function localDirectory(value, label) {
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(value)) throw new Error(`${label} must be a local directory`);
  return path.resolve(value);
}

function workerOptions(options) {
  const cachePath = localDirectory(options.cachePath, '--cache-path');
  const settings = { cachePath, logger: () => {}, errorHandler: () => {} };
  if (options.langPath || options.offline) {
    const langPath = localDirectory(options.langPath || cachePath, '--lang-path');
    for (const lang of options.lang.split('+')) {
      const file = path.join(langPath, `${lang}.traineddata`);
      if (!fs.existsSync(file) || !fs.statSync(file).isFile()) {
        throw new Error(`Missing local language model: ${lang}.traineddata in ${langPath}`);
      }
    }
    // Explicit local langPath prevents Tesseract's CDN fallback, even on cache miss.
    Object.assign(settings, { langPath, gzip: false, cacheMethod: 'none' });
  } else {
    fs.mkdirSync(cachePath, { recursive: true, mode: 0o700 });
  }
  return settings;
}

function finiteOrNull(value) { return Number.isFinite(value) ? value : null; }
function validBox(box) {
  return box && ['x0', 'y0', 'x1', 'y1'].every(key => Number.isFinite(box[key]))
    ? { x0: box.x0, y0: box.y0, x1: box.x1, y1: box.y1 } : null;
}
function normalizeData(data) {
  const words = [];
  for (const block of data.blocks || []) {
    for (const paragraph of block.paragraphs || []) {
      for (const line of paragraph.lines || []) {
        for (const word of line.words || []) {
          words.push({ page_number: 1, text: String(word.text || ''),
            confidence: finiteOrNull(word.confidence), bbox: validBox(word.bbox) });
        }
      }
    }
  }
  const boxes = words.map(word => word.bbox).filter(Boolean);
  const bbox = boxes.reduce((combined, box) => combined ? {
    x0: Math.min(combined.x0, box.x0), y0: Math.min(combined.y0, box.y0),
    x1: Math.max(combined.x1, box.x1), y1: Math.max(combined.y1, box.y1),
  } : { ...box }, null);
  const text = String(data.text || '');
  const confidence = finiteOrNull(data.confidence);
  return { text, confidence, words, pages: [{ page_number: 1, text, bbox, confidence,
    status: text.trim() ? 'ok' : 'empty', extraction_method: 'ocr' }],
    status: text.trim() ? 'ok' : 'empty',
    warnings: ['OCR output requires human verification, especially numbers, decimal points and units.'] };
}

async function recognizeImage(options, createWorker) {
  const input = path.resolve(options.image);
  const stat = fs.statSync(input);
  if (!stat.isFile()) throw new Error('Input must be a regular local file');
  if (!stat.size || stat.size > options.maxMb * 1024 * 1024) throw new Error('Image is empty or exceeds --max-mb');
  const settings = workerOptions(options);
  const bytes = fs.readFileSync(input);
  let worker;
  let expired = false;
  let timer;
  const operation = (async () => {
    worker = await createWorker(options.lang, 1, settings);
    if (expired) { await worker.terminate(); throw new Error('OCR timed out'); }
    const result = await worker.recognize(bytes, {}, { text: true, blocks: true });
    return { schema_version: 1, tool: 'tesseract.js/7.0.0',
      source: { path: input, sha256: crypto.createHash('sha256').update(bytes).digest('hex'), size_bytes: bytes.length },
      extraction_method: 'ocr', ...normalizeData(result.data) };
  })();
  try {
    return await Promise.race([operation, new Promise((_, reject) => {
      timer = setTimeout(() => { expired = true; reject(new Error('OCR timed out')); }, options.timeout * 1000);
    })]);
  } finally {
    clearTimeout(timer);
    if (worker) await worker.terminate();
  }
}

async function main(argv) {
  let options;
  let hardTimeout;
  const jsonOutput = argv.includes('--json');
  const printError = message => {
    if (jsonOutput) fs.writeFileSync(1, `${JSON.stringify({ schema_version: 1, status: 'error', error: message })}\n`);
    else process.stderr.write(`Error: ${message}\n`);
  };
  try {
    options = parseArgs(argv);
    if (options.help) { fs.writeFileSync(1, `${HELP}\n`); return 0; }
    // A hard deadline also stops a hung worker initialization/termination.
    hardTimeout = setTimeout(() => { printError('OCR timed out'); process.exit(1); }, options.timeout * 1000 + 1000);
    let createWorker;
    try { ({ createWorker } = require('tesseract.js')); }
    catch (_) { throw new Error('OCR dependency missing; run npm ci in skills/ocr-local first'); }
    const result = await recognizeImage(options, createWorker);
    fs.writeFileSync(1, options.json ? `${JSON.stringify(result, null, 2)}\n` : result.text);
    return result.status === 'ok' ? 0 : 2;
  } catch (error) {
    printError(error.message);
    return 1;
  } finally { clearTimeout(hardTimeout); }
}

module.exports = { parseArgs, workerOptions, normalizeData, recognizeImage, main };
if (require.main === module) {
  // Worker-thread startup errors can occur before createWorker returns its handle.
  process.once('uncaughtException', error => {
    if (process.argv.includes('--json')) fs.writeFileSync(1, `${JSON.stringify({ schema_version: 1, status: 'error', error: error.message })}\n`);
    else fs.writeFileSync(2, `OCR worker failed: ${error.message}\n`);
    process.exit(1);
  });
  main(process.argv.slice(2)).then(code => { process.exit(code); });
}
