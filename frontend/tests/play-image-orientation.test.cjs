const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const ts = require('typescript');
const moduleValue = { exports: {} };
new Function('module', 'exports', ts.transpileModule(fs.readFileSync('src/lib/playImageOrientation.ts', 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText)(moduleValue, moduleValue.exports);
const { playImageRotation } = moduleValue.exports;

test('an unrelated image keeps its own orientation instead of inheriting a repeated visual ID', async () => {
  assert.equal(await playImageRotation(new Blob(['unrelated synthetic image bytes'])), 0);
});
test('verified source hash selects a turn without modifying or uploading source bytes', async t => {
  const source = fs.readFileSync('src/lib/playImageOrientation.ts', 'utf8');
  const entries = [...source.matchAll(/'([a-f0-9]{64})': (90|180|270),/g)];
  assert.equal(entries.length, 15);
  const blob = new Blob(['synthetic source']);
  let current;
  t.mock.method(crypto.subtle, 'digest', async (algorithm, bytes) => {
    assert.equal(algorithm, 'SHA-256'); assert.equal(new TextDecoder().decode(bytes), 'synthetic source');
    return Uint8Array.from(current[1].match(/../g), hex => parseInt(hex, 16)).buffer;
  });
  for (current of entries) assert.equal(await playImageRotation(blob), Number(current[2]));
  assert.equal(await blob.text(), 'synthetic source');
});
