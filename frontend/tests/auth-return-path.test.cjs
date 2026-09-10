const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');
const filename = path.join(__dirname, '../src/lib/authReturnPath.ts');
const { outputText } = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  fileName: filename, compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
});
const compiled = { exports: {} };
new Function('module', 'exports', outputText)(compiled, compiled.exports);
const { authReturnPath } = compiled.exports;

test('login returns to the exact game or opening including its query and fragment', () => {
  for (const value of ['/play/package-preview', '/play/package-play?play=play-123#discussion', '/profile/game-history']) {
    assert.equal(authReturnPath(value), value);
  }
});

test('external, encoded, malformed, and authentication loop destinations use the fallback', () => {
  for (const value of [undefined, [], ['/play'], '', 'https://evil.invalid', '//evil.invalid', '/\\evil.invalid',
    'javascript:alert(1)', '/%2f%2fevil.invalid', '/%5cevil.invalid', '/%252fevil.invalid', '/\nevil.invalid',
    '/%0devil.invalid', '/%zz', '/auth', '/auth/login?returnUrl=/play', '/%61uth/login', '/x/../auth/login',
    '/x/..//evil.invalid', '/%2e%2e//evil.invalid']) {
    assert.equal(authReturnPath(value), '/script-center', String(value));
  }
});
