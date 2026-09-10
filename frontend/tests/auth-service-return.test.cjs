const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');

function compile(relative) {
  const filename = path.join(__dirname, relative);
  return ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    fileName: filename, compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
}
const helper = { exports: {} };
new Function('module', 'exports', compile('../src/lib/authReturnPath.ts'))(helper, helper.exports);

function expiredSession(url) {
  let current = new URL(url, 'https://app.invalid');
  const location = {
    get href() { return current.href; },
    set href(value) { current = new URL(value, current); },
    get pathname() { return current.pathname; },
    get search() { return current.search; },
    get hash() { return current.hash; },
    get origin() { return current.origin; },
    get searchParams() { return current.searchParams; },
  };
  const window = { location, dispatchEvent: () => {} };
  const tokens = new Map([['access_token', 'expired-fixture-token']]);
  const storage = { getItem: key => tokens.get(key) ?? null, removeItem: key => tokens.delete(key) };
  const compiled = { exports: {} };
  let calls = 0;
  new Function('require', 'module', 'exports', 'window', 'localStorage', 'fetch', compile('../src/services/authService.ts'))(name => {
    if (name === '@/stores/configStore') return { config: { api: { baseUrl: 'https://fixture.invalid' } } };
    if (name === '@/lib/authReturnPath') return helper.exports;
    return require(name);
  }, compiled, compiled.exports, window, storage, async () => {
    calls += 1;
    return { ok: false, status: 401, json: async () => ({ detail: 'Expired fixture session' }) };
  });
  return { service: compiled.exports.default, window, tokens, calls: () => calls };
}

test('expired login returns to the original opening or exact saved game without retrying', async () => {
  for (const original of ['/play/package-preview', '/play/package-play?play=play-fixture#discussion']) {
    const fixture = expiredSession(original);
    await assert.rejects(fixture.service.getCurrentUser(), /Expired fixture session/);
    assert.equal(fixture.window.location.pathname, '/auth/login');
    assert.equal(fixture.window.location.searchParams.get('returnUrl'), original);
    assert.equal(fixture.tokens.has('access_token'), false);
    assert.equal(fixture.calls(), 1);
  }
});

test('a failed login preserves its return destination and displays the error without navigation', async () => {
  const original = '/auth/login?returnUrl=%2Fplay%2Fpackage-play%3Fplay%3Dplay-fixture';
  const fixture = expiredSession(original);
  await assert.rejects(fixture.service.getCurrentUser(), /Expired fixture session/);
  assert.equal(fixture.window.location.pathname + fixture.window.location.search, original);
  assert.equal(fixture.calls(), 1);
});

test('token-change listeners cannot replace the captured original game destination', async () => {
  const original = '/play/package-play?play=play-fixture#discussion';
  const fixture = expiredSession(original);
  fixture.window.dispatchEvent = () => { fixture.window.location.href = '/auth/login'; };
  await assert.rejects(fixture.service.getCurrentUser(), /Expired fixture session/);
  assert.equal(fixture.window.location.searchParams.get('returnUrl'), original);
  assert.equal(fixture.calls(), 1);
});

test('an unsafe normalized current path cannot become an external login destination', async () => {
  const fixture = expiredSession('/x/..//evil.invalid');
  await assert.rejects(fixture.service.getCurrentUser(), /Expired fixture session/);
  assert.equal(fixture.window.location.searchParams.get('returnUrl'), '/script-center');
  assert.equal(fixture.window.location.origin, 'https://app.invalid');
});
