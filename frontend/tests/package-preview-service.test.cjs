const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

const filename = path.join(__dirname, '../src/services/packagePreviewService.ts');
const { outputText } = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  fileName: filename, compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
});
const compiled = { exports: {} };
new Function('require', 'module', 'exports', outputText)(name => {
  if (name === '@/stores/configStore') return { config: { api: { baseUrl: 'https://fixture.invalid' } } };
  if (name === '@/services/authService') return { default: { getToken: () => 'synthetic-token' } };
  return require(name);
}, compiled, compiled.exports);
const service = compiled.exports.default;

test('opening requests authenticate and never use browser cache', async t => {
  let call;
  t.mock.method(global, 'fetch', async (...args) => { call = args; return { ok: true, json: async () => ({ data: [] }) }; });
  assert.deepEqual(await service.releases(), []);
  assert.equal(call[0], 'https://fixture.invalid/api/fusion/package-releases');
  assert.equal(call[1].cache, 'no-store');
  assert.equal(call[1].headers.Authorization, 'Bearer synthetic-token');
});

test('creation binds the explicit release, fixed role and same retry key without a caller identity', async t => {
  const calls = [];
  t.mock.method(global, 'fetch', async (...args) => { calls.push(args); return { ok: true, json: async () => ({ data: { session_id: 'synthetic' } }) }; });
  await service.create(3, 'role-a', 'same-retry');
  await service.create(3, 'role-a', 'same-retry');
  assert.equal(calls[0][1].body, calls[1][1].body);
  assert.deepEqual(JSON.parse(calls[0][1].body), { release_id: 3, character_id: 'role-a', idempotency_key: 'same-retry' });
  assert.equal(calls[0][1].method, 'POST');
});

test('session identifiers cannot alter the request path and abort signal is preserved', async t => {
  const controller = new AbortController();
  let call;
  t.mock.method(global, 'fetch', async (...args) => { call = args; return { ok: true, json: async () => ({ data: {} }) }; });
  await service.read('invalid/id?x=1', controller.signal);
  assert.equal(call[0], 'https://fixture.invalid/api/fusion/package-sessions/invalid%2Fid%3Fx%3D1');
  assert.equal(call[1].signal, controller.signal);
});

for (const status of [401, 403, 404, 409, 422, 500]) {
  test(`opening error ${status} never renders a private server response`, async t => {
    let read = false;
    t.mock.method(global, 'fetch', async () => ({ ok: false, status, json: async () => { read = true; return { detail: 'PRIVATE_SENTINEL' }; } }));
    await assert.rejects(service.releases(), error => !error.message.includes('PRIVATE_SENTINEL'));
    assert.equal(read, false);
  });
}
