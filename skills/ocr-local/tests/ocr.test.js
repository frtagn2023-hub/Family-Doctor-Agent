'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const { parseArgs, workerOptions, normalizeData, recognizeImage } = require('../scripts/ocr.js');

test('CLI help succeeds and bad options fail without invoking OCR', () => {
  const script = path.resolve(__dirname, '../scripts/ocr.js');
  assert.equal(spawnSync(process.execPath, [script, '--help']).status, 0);
  const bad = spawnSync(process.execPath, [script, 'file.png', '--json', '--timeout', 'NaN'], { encoding: 'utf8' });
  assert.equal(bad.status, 1);
  assert.equal(JSON.parse(bad.stdout).status, 'error');
  assert.equal(parseArgs(['--lang', 'eng', 'a.png']).image, 'a.png');
  assert.throws(() => parseArgs(['a.png', '--wat']), /Unknown option/);
});

test('v7 blocks flatten to words, confidence and page source text', () => {
  const result = normalizeData({ text: 'value 12.5', confidence: 82, blocks: [{ paragraphs: [{ lines: [{ words: [
    { text: 'value', confidence: 91, bbox: { x0: 10, y0: 10, x1: 50, y1: 40 } },
    { text: '12.5', confidence: 74, bbox: { x0: 60, y0: 11, x1: 90, y1: 42 } },
  ] }] }] }] });
  assert.equal(result.words[1].text, '12.5');
  assert.equal(result.pages[0].text, 'value 12.5');
  assert.deepEqual(result.pages[0].bbox, { x0: 10, y0: 10, x1: 90, y1: 42 });
  assert.equal(normalizeData({ text: '', blocks: null }).status, 'empty');
});

test('offline mode cannot silently download missing language data', () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'ocr-test-'));
  try {
    const options = parseArgs(['a.png', '--lang', 'eng', '--offline', '--cache-path', temp]);
    assert.throws(() => workerOptions(options), /Missing local language model/);
    fs.writeFileSync(path.join(temp, 'eng.traineddata'), 'synthetic');
    const settings = workerOptions(options);
    assert.equal(settings.langPath, temp);
    assert.equal(settings.cacheMethod, 'none');
    assert.equal(settings.gzip, false);
    assert.throws(() => workerOptions({ ...options, langPath: 'https://example.test/models' }), /local directory/);
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
});

test('worker requests blocks, hashes actual input and terminates on success or failure', async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'ocr-test-'));
  try {
    const input = path.join(temp, 'synthetic.png');
    fs.writeFileSync(input, 'synthetic bytes');
    const options = parseArgs([input, '--cache-path', temp]);
    let terminated = 0;
    const create = async () => ({
      recognize: async (bytes, parameters, output) => {
        assert.ok(Buffer.isBuffer(bytes));
        assert.deepEqual(output, { text: true, blocks: true });
        return { data: { text: 'example', blocks: [] } };
      }, terminate: async () => { terminated += 1; },
    });
    const result = await recognizeImage(options, create);
    assert.equal(terminated, 1);
    assert.equal(result.source.sha256, crypto.createHash('sha256').update('synthetic bytes').digest('hex'));
    await assert.rejects(recognizeImage(options, async () => ({
      recognize: async () => { throw new Error('bad image'); },
      terminate: async () => { terminated += 1; },
    })), /bad image/);
    assert.equal(terminated, 2);
    await assert.rejects(recognizeImage({ ...options, maxMb: 0.000001 }, create), /exceeds/);
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
});

test('recognition timeout terminates a live worker', async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'ocr-test-'));
  try {
    const input = path.join(temp, 'test.png'); fs.writeFileSync(input, 'synthetic');
    let terminated = false;
    await assert.rejects(recognizeImage(parseArgs([input, '--cache-path', temp, '--timeout', '0.01']), async () => ({
      recognize: async () => new Promise(() => {}), terminate: async () => { terminated = true; },
    })), /timed out/);
    assert.equal(terminated, true);
  } finally { fs.rmSync(temp, { recursive: true, force: true }); }
});
