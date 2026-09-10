const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const React = require('react');
const ts = require('typescript');

function compile(relativePath, imports = require) {
  const filename = path.join(__dirname, relativePath);
  const { outputText } = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    fileName: filename, compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
  });
  const compiled = { exports: {} };
  new Function('require', 'module', 'exports', outputText)(name => name === '@/lib/playPresentation' ? compile('../src/lib/playPresentation.ts') : imports(name), compiled, compiled.exports);
  return compiled.exports;
}
let token = 'account-a-token';
const serviceModule = compile('../src/services/packagePreviewService.ts', name => {
  if (name === '@/services/authService') return { default: { getToken: () => token } };
  if (name === '@/stores/configStore') return { config: { api: { baseUrl: 'https://fixture.invalid' } } };
  return require(name);
});
const service = serviceModule.default;
const imageOrientation = { playImageRotation: async () => 0 };
const playServiceModule = compile('../src/services/packagePlayService.ts', name => {
  if (name === '@/services/authService') return { default: { getToken: () => token } };
  if (name === '@/stores/configStore') return { config: { api: { baseUrl: 'https://fixture.invalid' } } };
  return require(name);
});
const elements = (tree, name) => Array.isArray(tree) ? tree.flatMap(node => elements(node, name))
  : !React.isValidElement(tree) ? [] : tree.type?.name === 'PlayImageView' ? elements(tree.type(tree.props), name) : [...(tree.type === name || tree.type.name === name ? [tree] : []), ...elements(tree.props.children, name)];
function text(tree) {
  if (Array.isArray(tree)) return tree.map(text).join('');
  return React.isValidElement(tree) ? text(tree.props.children) : typeof tree === 'string' ? tree : '';
}
function hookRunner() {
  const slots = [], effects = [], pending = []; let index = 0, dirty = false;
  return {
    hooks: {
      useState(initial) { const slot = index++; if (!(slot in slots)) slots[slot] = typeof initial === 'function' ? initial() : initial;
        return [slots[slot], value => { const next = typeof value === 'function' ? value(slots[slot]) : value;
          if (!Object.is(next, slots[slot])) { slots[slot] = next; dirty = true; } }]; },
      useCallback(callback, deps) { const slot = index++;
        if (!slots[slot] || deps.some((value, offset) => !Object.is(value, slots[slot].deps[offset]))) slots[slot] = { callback, deps };
        return slots[slot].callback; },
      useRef(initial) { const slot = index++; if (!(slot in slots)) slots[slot] = { current: initial }; return slots[slot]; },
      useEffect(callback, deps) { const slot = index++;
        if (!effects[slot] || deps.some((value, offset) => !Object.is(value, effects[slot].deps[offset]))) pending.push(() => {
          effects[slot]?.cleanup?.(); effects[slot] = { deps, cleanup: callback() };
        }); },
    },
    render(callback, flush = true) { let tree, passes = 0;
      do { index = 0; dirty = false; tree = callback(); if (flush) while (pending.length) pending.shift()(); assert.ok(++passes < 15); } while (dirty);
      return tree;
    },
    unmount() { effects.forEach(effect => effect?.cleanup?.()); },
  };
}
const preview = { session_id: 'private-opening', selected_character_id: 'role-a',
  private_knowledge: [{ id: 'goals', text: '你的目标\nOLD_PRIVATE_GOAL' }] };
function pageHarness(api, query = { session: 'private-opening' }, playApi = {}) {
  const runner = hookRunner(), navigation = [];
  let owner = 'account-a';
  const router = { isReady: true, query, asPath: '/fixture', replace: async value => navigation.push(value) };
  const Page = compile('../src/pages/play/package-preview.tsx', name => {
    if (name === 'react') return { ...React, ...runner.hooks };
    if (name === 'next/router') return { useRouter: () => router };
    if (name === 'next/link') return { default: ({ children }) => children };
    if (name.startsWith('@/components/')) return { default: ({ children }) => children };
    if (name === '@/stores/authStore') return { useAuthStore: select => select({ isAuthenticated: true, user: { id: owner } }) };
    if (name === '@/services/packagePreviewService') return { ...serviceModule, default: api };
    if (name === '@/services/packagePlayService') return { ...playServiceModule, default: playApi };
    return require(name);
  }).default;
  const workspace = Page().props.children.props.children;
  return { navigation, router, parentKey: () => Page().props.children.props.children.key, setOwner(value) { owner = value; },
    render(flush = true) { return runner.render(() => workspace.type(workspace.props), flush); }, unmount: runner.unmount };
}
async function withWindow(run) {
  const previous = global.window; global.window = new EventTarget(); token = 'account-a-token';
  try { await run(); } finally { global.window = previous; token = 'account-a-token'; }
}
const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

function imageHarness() {
  const runner = hookRunner();
  const useRef = runner.hooks.useRef;
  runner.hooks.useRef = initial => { const ref = useRef(initial); if (initial === null) ref.current = {}; return ref; };
  const Image = compile('../src/components/OpeningImage.tsx', name => {
    if (name === 'react') return { ...React, ...runner.hooks };
    if (name === '@/services/packagePreviewService') return serviceModule;
    if (name === '@/lib/playImageOrientation') return imageOrientation;
    if (name === './PlayImageView') return compile('../src/components/PlayImageView.tsx');
    return require(name);
  }).default;
  return { render: () => runner.render(() => Image({ sessionId: 'opening/a?b', visualId: 'image/a?b', label: '虚构地图' })), unmount: runner.unmount };
}
async function withObserver(run) {
  const original = global.IntersectionObserver;
  let notify, disconnected = 0;
  global.IntersectionObserver = class {
    constructor(callback) { notify = callback; }
    observe() {}
    disconnect() { disconnected++; }
  };
  try { await withWindow(() => run({ notify: visible => notify([{ isIntersecting: visible }]), disconnected: () => disconnected })); }
  finally { if (original === undefined) delete global.IntersectionObserver; else global.IntersectionObserver = original; }
}
const imageResponse = () => ({ ok: true, headers: new Headers({ 'Content-Type': 'image/png' }), blob: async () => new Blob(['fictional-pixel'], { type: 'image/png' }) });

test('opening image visibility makes one authenticated no-store request and supports safe rotation', async t => {
  const calls = [];
  t.mock.method(global, 'fetch', async (...args) => { calls.push(args); return imageResponse(); });
  t.mock.method(URL, 'createObjectURL', () => 'blob:opening-authorized');
  t.mock.method(URL, 'revokeObjectURL', () => {});
  await withObserver(async observer => {
    const app = imageHarness();
    try {
      app.render(); assert.equal(calls.length, 0);
      observer.notify(false); assert.equal(calls.length, 0);
      observer.notify(true); observer.notify(true); await settle();
      let tree = app.render(); observer.notify(true); assert.equal(calls.length, 1);
      assert.equal(calls[0][0], 'https://fixture.invalid/api/fusion/package-sessions/opening%2Fa%3Fb/images/image%2Fa%3Fb');
      assert.equal(calls[0][1].cache, 'no-store');
      assert.equal(calls[0][1].headers.Authorization, 'Bearer account-a-token');
      assert.equal(calls[0][1].signal.aborted, false);
      assert.equal(elements(tree, 'img')[0].props.src, 'blob:opening-authorized');
      elements(tree, 'button').find(button => text(button) === '旋转图片').props.onClick(); tree = app.render();
      assert.equal(elements(tree, 'img')[0].props.style.transform, 'rotate(90deg)');
      assert.match(elements(tree, 'button').find(b => b.props['aria-label'] === '放大虚构地图').props.className, /aspect-square.*overflow-hidden/);
      assert.ok(observer.disconnected() > 0);
    } finally { app.unmount(); }
  });
});

test('opening image auth change clears displayed pixels, revokes blob and permanently disables the old binding', async t => {
  const revoked = []; let calls = 0;
  t.mock.method(global, 'fetch', async () => { calls++; return imageResponse(); });
  t.mock.method(URL, 'createObjectURL', () => 'blob:opening-owner-a');
  t.mock.method(URL, 'revokeObjectURL', value => revoked.push(value));
  await withObserver(async observer => {
    const app = imageHarness();
    try {
      app.render(); observer.notify(true); await settle(); assert.equal(elements(app.render(), 'img').length, 2);
      token = 'account-b-token'; window.dispatchEvent(new Event('auth-token-changed'));
      const tree = app.render(); assert.equal(elements(tree, 'img').length, 0);
      assert.deepEqual(revoked, ['blob:opening-owner-a']); assert.equal(elements(tree, 'button')[0].props.disabled, true);
      token = 'account-a-token'; window.dispatchEvent(new Event('storage'));
      elements(tree, 'button')[0].props.onClick(); observer.notify(true); await settle();
      assert.equal(calls, 1); assert.equal(elements(app.render(), 'img').length, 0);
    } finally { app.unmount(); }
  });
});

test('opening image unload and auth loss abort pending transport and ignore late private bytes', async t => {
  for (const action of ['unmount', 'auth-change']) {
    let finish, signal; const urls = [];
    t.mock.method(global, 'fetch', (_url, options) => { signal = options.signal; return new Promise(resolve => { finish = resolve; }); });
    t.mock.method(URL, 'createObjectURL', value => { urls.push(value); return 'blob:must-not-appear'; });
    await withObserver(async observer => {
      const app = imageHarness();
      try {
        app.render(); observer.notify(true); assert.equal(signal.aborted, false);
        if (action === 'unmount') app.unmount();
        else { token = 'account-b-token'; window.dispatchEvent(new Event('storage')); }
        assert.equal(signal.aborted, true); finish(imageResponse()); await settle();
        assert.equal(urls.length, 0);
        if (action === 'auth-change') assert.equal(elements(app.render(), 'img').length, 0);
      } finally { app.unmount(); }
    });
    t.mock.restoreAll();
  }
});

test('opening image unmount revokes already displayed blob', async t => {
  const revoked = [];
  t.mock.method(global, 'fetch', async () => imageResponse());
  t.mock.method(URL, 'createObjectURL', () => 'blob:opening-close');
  t.mock.method(URL, 'revokeObjectURL', value => revoked.push(value));
  await withObserver(async observer => {
    const app = imageHarness(); app.render(); observer.notify(true); await settle();
    assert.equal(elements(app.render(), 'img').length, 2); app.unmount();
    assert.deepEqual(revoked, ['blob:opening-close']);
  });
});

test('opening image transport rejects non-image, mismatched and empty bodies without reflecting their content', async t => {
  for (const [header, blob, expectedReads] of [
    ['text/html', new Blob(['PRIVATE_HTML'], { type: 'text/html' }), 0],
    ['image/svg+xml', new Blob(['PRIVATE_SVG'], { type: 'image/svg+xml' }), 0],
    ['image/png', new Blob(['PRIVATE_WRONG_TYPE'], { type: 'text/html' }), 1],
    ['image/png', new Blob([], { type: 'image/png' }), 1],
  ]) {
    let reads = 0;
    t.mock.method(global, 'fetch', async () => ({ ok: true, headers: new Headers({ 'Content-Type': header }), blob: async () => { reads++; return blob; } }));
    await assert.rejects(service.image('opening', 'map'), error => /有效原图/.test(error.message) && !error.message.includes('PRIVATE'));
    assert.equal(reads, expectedReads); t.mock.restoreAll();
  }
});

test('opening image transport refuses credentials changed during blob decoding without a browser event', async t => {
  token = 'account-a-token';
  t.mock.method(global, 'fetch', async () => ({ ok: true, headers: new Headers({ 'Content-Type': 'image/png' }), blob: async () => {
    token = 'account-b-token'; return new Blob(['PRIVATE_OLD_PIXELS'], { type: 'image/png' });
  } }));
  try { await assert.rejects(service.image('opening', 'map'), error => /登录身份已变化/.test(error.message) && !error.message.includes('PRIVATE')); }
  finally { token = 'account-a-token'; }
});

test('failed opening image shows an explicit retry and makes no automatic repeated request', async t => {
  let calls = 0;
  t.mock.method(global, 'fetch', async () => { calls++; return calls === 1 ? { ok: false, status: 409 } : imageResponse(); });
  t.mock.method(URL, 'createObjectURL', () => 'blob:opening-retry');
  t.mock.method(URL, 'revokeObjectURL', () => {});
  await withObserver(async observer => {
    const app = imageHarness();
    try {
      app.render(); observer.notify(true); await settle();
      let tree = app.render(); assert.match(text(tree), /原图读取失败/); assert.equal(calls, 1);
      app.render(); assert.equal(calls, 1);
      elements(tree, 'button').find(button => text(button) === '重试原图').props.onClick(); await settle();
      tree = app.render(); assert.equal(calls, 2); assert.equal(elements(tree, 'img')[0].props.src, 'blob:opening-retry');
    } finally { app.unmount(); }
  });
});

test('auth watchers invalidate on storage, same-tab token events and focus, and never revive', async () => {
  await withWindow(async () => {
    for (const event of ['storage', 'auth-token-changed', 'focus']) {
      token = 'account-a-token'; let calls = 0;
      const guard = serviceModule.watchPackagePreviewAuth(() => calls++);
      window.dispatchEvent(new Event(event)); assert.equal(calls, 0);
      token = 'account-b-token'; window.dispatchEvent(new Event(event));
      assert.equal(calls, 1); assert.equal(guard.isCurrent(), false);
      token = 'account-a-token'; window.dispatchEvent(new Event(event));
      assert.equal(guard.isCurrent(), false); assert.equal(calls, 1); guard.dispose();
    }
    let disposedCalls = 0; const guard = serviceModule.watchPackagePreviewAuth(() => disposedCalls++);
    guard.dispose(); token = 'after-disposal'; window.dispatchEvent(new Event('storage'));
    assert.equal(disposedCalls, 0);
  });
});

test('transport rejects changed identity before reading an old response body', async t => {
  token = 'account-a-token'; let bodyReads = 0;
  t.mock.method(global, 'fetch', async () => { token = 'account-b-token'; return { ok: true, json: async () => { bodyReads++; return { data: preview }; } }; });
  try { await assert.rejects(service.read('private-opening'), /登录身份已变化/); assert.equal(bodyReads, 0); }
  finally { token = 'account-a-token'; }
});

test('transport checks identity after JSON body completion even without an auth event', async t => {
  token = 'account-a-token';
  t.mock.method(global, 'fetch', async () => ({ ok: true, json: async () => { token = 'account-b-token'; return { data: preview }; } }));
  try { await assert.rejects(service.read('private-opening'), error => /登录身份已变化/.test(error.message) && !error.message.includes('OLD_PRIVATE')); }
  finally { token = 'account-a-token'; }
});

test('token-only change hides loaded private reading and reference before effects run', async () => {
  await withWindow(async () => {
    let reads = 0; const app = pageHarness({ read: async () => { reads++; return preview; } });
    try {
      app.render(); await settle(); assert.equal(elements(app.render(), 'OpeningReading')[0].props.preview, preview);
      const ownerKey = app.parentKey(); token = 'account-b-token'; window.dispatchEvent(new Event('storage'));
      const tree = app.render(false);
      assert.equal(app.parentKey(), ownerKey, 'auth store has not yet observed the other tab');
      assert.equal(elements(tree, 'OpeningReading').length, 0); assert.match(text(tree), /重新打开开场/);
      assert.equal(elements(tree, 'select').length, 0); assert.equal(reads, 1);
      token = 'account-a-token'; window.dispatchEvent(new Event('storage'));
      assert.equal(elements(app.render(), 'OpeningReading').length, 0); assert.equal(reads, 1);
    } finally { app.unmount(); }
  });
});

test('token change aborts pending read and ignores its late private result', async () => {
  await withWindow(async () => {
    let finish, signal; const app = pageHarness({ read: (_id, value) => { signal = value; return new Promise(resolve => { finish = resolve; }); } });
    try {
      app.render(); token = 'account-b-token'; window.dispatchEvent(new Event('auth-token-changed'));
      assert.equal(signal.aborted, true); finish(preview); await settle();
      assert.equal(elements(app.render(), 'OpeningReading').length, 0); assert.deepEqual(app.navigation, []);
    } finally { app.unmount(); }
  });
});

test('a late read detects changed credentials even when no browser event was delivered', async () => {
  await withWindow(async () => {
    let finish; const app = pageHarness({ read: () => new Promise(resolve => { finish = resolve; }) });
    try {
      app.render(); token = 'account-b-token'; finish(preview); await settle();
      const tree = app.render(); assert.equal(elements(tree, 'OpeningReading').length, 0);
      assert.match(text(tree), /登录身份已变化/);
    } finally { app.unmount(); }
  });
});

test('old create callbacks cannot write after credentials change, and pending creation cannot navigate', async () => {
  await withWindow(async () => {
    const calls = []; let finish;
    const app = pageHarness({ releases: async () => [{ id: 1, title: '虚构发布', characters: [{ id: 'role-a', name: '虚构甲' }] }],
      create: (...args) => { calls.push(args); return new Promise(resolve => { finish = resolve; }); } }, { release_id: '1' });
    try {
      app.render(); await settle(); let tree = app.render();
      elements(tree, 'select')[1].props.onChange({ target: { value: 'role-a' } }); tree = app.render();
      const createButton = elements(tree, 'button').find(button => text(button) === '阅读开场');
      createButton.props.onClick(); assert.equal(calls.length, 1);
      token = 'account-b-token'; window.dispatchEvent(new Event('storage'));
      assert.equal(calls[0][3].aborted, true); createButton.props.onClick(); assert.equal(calls.length, 1);
      finish(preview); await settle(); assert.deepEqual(app.navigation, []);
      assert.equal(elements(app.render(), 'select').length, 0);
    } finally { app.unmount(); }
  });
});

test('unmount removes auth observers, aborts pending requests and ignores navigation', async () => {
  await withWindow(async () => {
    let finish, signal; const app = pageHarness({ read: (_id, value) => { signal = value; return new Promise(resolve => { finish = resolve; }); } });
    app.render(); const key = app.parentKey(); app.setOwner('account-b'); assert.notEqual(app.parentKey(), key);
    app.unmount(); assert.equal(signal.aborted, true);
    token = 'account-b-token'; window.dispatchEvent(new Event('storage')); finish(preview); await settle();
    assert.deepEqual(app.navigation, []);
  });
});

const boundPreview = { ...preview, release_id: 11, version_id: 17 };
const boundGame = { play_id: `play-${'a'.repeat(32)}`, opening_session_id: boundPreview.session_id,
  release_id: 11, version_id: 17, selected_character_id: 'role-a' };
const openingControls = tree => elements(tree, 'OpeningReading')[0].props;

test('script selection shows only the title while retaining the selected release identity', async () => {
  await withWindow(async () => {
    const app = pageHarness({ releases: async () => [{ id: 17, title: '虚构案件', content_version: 'M3-INTERNAL-VERSION', characters: [] }] }, {});
    try {
      app.render(); await settle(); const tree = app.render();
      const option = elements(tree, 'option').find(item => item.props.value === 17);
      assert.equal(text(option), '虚构案件'); assert.doesNotMatch(text(tree), /M3|INTERNAL|版本|预览/);
    } finally { app.unmount(); }
  });
});

test('one explicit Start click restores the existing bound game without creating or sending model actions', async () => {
  await withWindow(async () => {
    const calls = []; const app = pageHarness({ read: async () => boundPreview }, undefined, {
      lookup: async id => { calls.push(['lookup', id]); return boundGame; }, create() { throw new Error('must not create'); },
    });
    try {
      app.render(); await settle(); let tree = app.render(); assert.deepEqual(calls, []);
      openingControls(tree).onStart(); openingControls(tree).onStart(); await settle(); tree = app.render();
      assert.deepEqual(calls, [['lookup', boundPreview.session_id]]);
      assert.deepEqual(app.navigation, [{ pathname: '/play/package-play', query: { play: boundGame.play_id } }]);
      assert.equal(openingControls(tree).starting, false);
    } finally { app.unmount(); }
  });
});

test('one explicit Start click creates once after a missing lookup and directly navigates to its saved ID', async () => {
  await withWindow(async () => {
    const requests = []; let finish;
    const app = pageHarness({ read: async () => boundPreview }, undefined, {
      lookup: async () => null, create: (body, signal) => { requests.push({ body, signal }); return new Promise(resolve => { finish = resolve; }); },
    });
    try {
      app.render(); await settle(); openingControls(app.render()).onStart(); await settle();
      let tree = app.render(); assert.equal(openingControls(tree).starting, true);
      openingControls(tree).onStart(); assert.equal(requests.length, 1); assert.deepEqual(Object.keys(requests[0].body).sort(), ['idempotency_key', 'opening_session_id']);
      finish(boundGame); await settle(); assert.deepEqual(app.navigation, [{ pathname: '/play/package-play', query: { play: boundGame.play_id } }]);
    } finally { app.unmount(); }
  });
});

test('unknown creation checks only saved progress; an absent game requires another explicit same-key retry', async () => {
  for (const recorded of [true, false]) await withWindow(async () => {
    let exists = false; const calls = [], bodies = [];
    const app = pageHarness({ read: async () => boundPreview }, undefined, {
      lookup: async () => { calls.push('GET'); return exists ? boundGame : null; },
      create: async body => { calls.push('POST'); bodies.push(body); if (bodies.length === 1) { exists = recorded; throw new TypeError('lost response'); } exists = true; return boundGame; },
    });
    try {
      app.render(); await settle(); openingControls(app.render()).onStart(); await settle();
      let controls = openingControls(app.render()); assert.equal(controls.startLabel, '检查开始结果');
      app.render(); await settle(); assert.deepEqual(calls, ['GET', 'POST']); assert.deepEqual(app.navigation, []);
      controls.onStart(); await settle(); assert.deepEqual(calls, ['GET','POST','GET']);
      if (!recorded) {
        controls = openingControls(app.render()); assert.equal(controls.startLabel, '重试开始游戏'); assert.deepEqual(app.navigation, []);
        controls.onStart(); await settle(); assert.deepEqual(calls, ['GET','POST','GET','GET','POST']); assert.deepEqual(bodies[0], bodies[1]);
      }
      assert.deepEqual(app.navigation, [{ pathname: '/play/package-play', query: { play: boundGame.play_id } }]);
    } finally { app.unmount(); }
  });
});

test('unavailable lookup never creates, and mismatched saved bindings never navigate or expose the result', async () => {
  for (const patch of [null, { play_id: 'invalid' }, { opening_session_id: 'foreign' }, { release_id: 99 }, { version_id: 99 }, { selected_character_id: 'other-role' }]) await withWindow(async () => {
    let posts = 0;
    const app = pageHarness({ read: async () => boundPreview }, undefined, {
      lookup: async () => { if (!patch) throw new Error('lookup unavailable'); return { ...boundGame, ...patch }; }, create: async () => { posts++; return boundGame; },
    });
    try {
      app.render(); await settle(); openingControls(app.render()).onStart(); await settle();
      assert.equal(posts, 0); assert.deepEqual(app.navigation, []); assert.match(openingControls(app.render()).startError, /暂时无法确认游戏进度/);
    } finally { app.unmount(); }
  });
});

test('saved game with navigation failure is resumed by lookup and cannot be recreated if it disappears', async () => {
  await withWindow(async () => {
    let lookups = 0, posts = 0;
    const app = pageHarness({ read: async () => boundPreview }, undefined, {
      lookup: async () => { lookups++; return lookups === 1 ? null : lookups === 2 ? null : boundGame; },
      create: async () => { posts++; return boundGame; },
    });
    app.router.replace = async () => { throw new Error('navigation failed'); };
    try {
      app.render(); await settle(); openingControls(app.render()).onStart(); await settle();
      assert.equal(openingControls(app.render()).startLabel, '继续游戏'); assert.match(openingControls(app.render()).startError, /游戏已保存/);
      openingControls(app.render()).onStart(); await settle(); assert.equal(posts, 1); assert.deepEqual(app.navigation, []);
      app.router.replace = async destination => app.navigation.push(destination);
      openingControls(app.render()).onStart(); await settle(); assert.equal(posts, 1); assert.equal(app.navigation.length, 1);
    } finally { app.unmount(); }
  });
});

test('Start aborts on auth loss or unmount at lookup and create, ignores late results and never revives old callbacks', async () => {
  for (const at of ['lookup','create']) for (const reason of ['auth','unmount']) await withWindow(async () => {
    let finish, signal, posts = 0;
    const app = pageHarness({ read: async () => boundPreview }, undefined, {
      lookup: async (_id, s) => at === 'lookup' ? new Promise(resolve => { signal = s; finish = resolve; }) : null,
      create: async (_body, s) => { posts++; return new Promise(resolve => { signal = s; finish = resolve; }); },
    });
    try {
      app.render(); await settle(); const old = openingControls(app.render()); old.onStart(); await settle(); assert.equal(signal.aborted, false);
      if (reason === 'auth') { token = 'account-b-token'; window.dispatchEvent(new Event('storage')); }
      else app.unmount();
      assert.equal(signal.aborted, true); finish(at === 'lookup' ? null : boundGame); await settle(); assert.deepEqual(app.navigation, []);
      if (reason === 'auth') { old.onStart(); await settle(); assert.equal(posts, at === 'lookup' ? 0 : 1); assert.equal(elements(app.render(), 'OpeningReading').length, 0); }
    } finally { app.unmount(); }
  });
});

test('opening image hashing cannot restore pixels after an account change', async t => {
  let finish; const urls = [];
  t.mock.method(global, 'fetch', async () => imageResponse());
  t.mock.method(imageOrientation, 'playImageRotation', () => new Promise(resolve => { finish = resolve; }));
  t.mock.method(URL, 'createObjectURL', blob => { urls.push(blob); return 'blob:forbidden'; });
  await withObserver(async observer => {
    const app = imageHarness();
    try {
      app.render(); observer.notify(true); await settle(); assert.equal(typeof finish, 'function');
      token = 'different-owner'; window.dispatchEvent(new Event('storage'));
      finish(270); await settle(); assert.equal(urls.length, 0); assert.equal(elements(app.render(), 'img').length, 0);
    } finally { app.unmount(); }
  });
});
