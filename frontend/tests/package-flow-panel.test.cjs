const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
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
const serviceModule = compile('../src/services/packageFlowService.ts', name => {
  if (name === '@/stores/configStore') return { config: { api: { baseUrl: 'https://fixture.invalid' } } };
  if (name === '@/services/authService') return { default: { getToken: () => 'fictional-flow-token' } };
  return require(name);
});
const { default: service, PackageFlowError, preparePackageFlowAttempt } = serviceModule;
const link = { default: ({ children, ...props }) => React.createElement('a', props, children) };
const panelImports = hooks => name => {
  if (name === 'react' && hooks) return { ...React, ...hooks };
  if (name === 'next/link') return link;
  if (name === '@/services/packageFlowService') return serviceModule;
  return require(name);
};
const panelModule = compile('../src/components/PackageFlowPanel.tsx', panelImports());
const { default: Panel, canPerformFlowAction } = panelModule;
const material = (id, overrides = {}) => ({ id, text: `虚构材料 ${id}`, disclosure: 'MAY_SHARE', can_share: true, ...overrides });
const view = {
  flow_id: `flow-${'a'.repeat(32)}`, opening_session_id: 'fictional-opening', release_id: 11, version_id: 17, package_hash: 'b'.repeat(64), selected_character_id: 'fictional-a',
  revision: 0, runtime_ready: false, status: 'RULES_PREVIEW', script: { title: '完全虚构的阶段演练', content_version: 'test-1', player_count: 2 },
  characters: [{ id: 'fictional-a', name: '虚构角色甲' }, { id: 'fictional-b', name: '虚构角色乙' }], introduction: { text: '虚构故事介绍' },
  current_phase: { id: 'phase-1', title: '虚构第一阶段' }, can_advance: true, phase_complete: false,
  public_knowledge: [material('public-fact', { disclosure: 'PUBLIC', can_share: false, kind: 'FACT' })],
  private_knowledge: [material('my-claim', { kind: 'CLAIM' }), material('must', { disclosure: 'MUST_SHARE' }), material('secret', { disclosure: 'KEEP_PRIVATE', can_share: false })],
  public_evidence: [material('public-evidence', { disclosure: 'PUBLIC', can_share: false })], private_evidence: [material('my-evidence')],
};
const props = overrides => ({ flowId: view.flow_id, openingSessionId: '', view, loading: false, busy: false, requiresRefresh: false, error: '', notice: '', onReload() {}, onCreate() {}, onAction() {}, ...overrides });
const render = overrides => renderToStaticMarkup(React.createElement(Panel, props(overrides)));
const elements = (tree, type) => {
  if (Array.isArray(tree)) return tree.flatMap(child => elements(child, type));
  if (!React.isValidElement(tree)) return [];
  return [...(tree.type === type ? [tree] : []), ...elements(tree.props.children, type)];
};
const button = (tree, label) => elements(tree, 'button').find(node => node.props.children === label);
const response = value => ({ ok: true, json: async () => ({ success: true, data: value }) });
const settle = () => new Promise(resolve => setImmediate(resolve));
const share = (collection, id) => ({ action: 'SHARE_MATERIAL', target: { collection, id } });

test('rendering a flow is read-only and keeps stage rehearsal distinct from complete gameplay', () => {
  let writes = 0;
  const html = render({ onCreate() { writes++; }, onAction() { writes++; } });
  assert.equal(writes, 0);
  assert.match(html, /阶段演练/); assert.match(html, /AI 角色互动、主持结算和完整整局尚未接入/);
  assert.match(html, /当前阶段：虚构第一阶段/); assert.match(html, /虚构角色甲/);
  assert.match(html, /已保存操作：0 次/); assert.doesNotMatch(html, /当前保存版本/);
  assert.match(html, /按当前规则已公开的内容/); assert.match(html, /仅本角色可见；允许公开的材料可手动分享/);
  assert.match(html, /角色说法/); assert.match(html, /已知事实/);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b|游戏已结束|结算完成/);
});

test('missing lookup results cannot create a flow, while observed null needs an explicit click', () => {
  for (const state of [{ view: undefined }, { view: null, loading: true }, { view: null, busy: true }, { view: null, requiresRefresh: true }]) {
    let writes = 0;
    const tree = Panel(props({ flowId: '', openingSessionId: view.opening_session_id, ...state, onCreate() { writes++; } }));
    const start = button(tree, '开始阶段演练'); start?.props.onClick();
    assert.equal(writes, 0);
  }
  let writes = 0;
  const tree = Panel(props({ flowId: '', openingSessionId: view.opening_session_id, view: null, onCreate() { writes++; } }));
  assert.equal(writes, 0); button(tree, '开始阶段演练').props.onClick(); assert.equal(writes, 1);
  assert.match(render({ flowId: '', openingSessionId: view.opening_session_id, view: null }), /重新检查当前发布版本是否有效/);
});

test('only an observed private unlocked shareable item gets a share action with its own collection and ID', () => {
  const calls = [];
  const tree = Panel(props({ onAction(value) { calls.push(value); } }));
  const shares = elements(tree, 'button').filter(node => node.props['aria-label']?.startsWith('公开'));
  assert.equal(shares.length, 3);
  for (const item of shares) item.props.onClick();
  assert.deepEqual(calls, [share('knowledge', 'my-claim'), share('knowledge', 'must'), share('evidence', 'my-evidence')]);
  for (const target of [share('knowledge', 'secret'), share('knowledge', 'unknown'), share('evidence', 'my-claim'), share('knowledge', 'public-fact'), share('truth', 'secret')]) {
    assert.equal(canPerformFlowAction(view, target), false);
  }
  const keep = { ...view, private_knowledge: [material('must-stay-private', { disclosure: 'KEEP_PRIVATE', can_share: true })], private_evidence: [] };
  assert.equal(elements(Panel(props({ view: keep })), 'button').filter(node => node.props['aria-label']).length, 0);
  assert.equal(canPerformFlowAction({ ...view, private_evidence: [material('my-evidence', { can_share: false })] }, share('evidence', 'my-evidence')), false);
});

test('MUST_SHARE remains a manual prompt and does not invent a stage prerequisite', () => {
  let calls = 0;
  const tree = Panel(props({ onAction(value) { assert.deepEqual(value, { action: 'ADVANCE_PHASE' }); calls++; } }));
  assert.equal(calls, 0); assert.equal(button(tree, '进入下一阶段').props.disabled, false);
  button(tree, '进入下一阶段').props.onClick(); assert.equal(calls, 1);
  assert.match(render(), /页面不会自动替你分享/);
  assert.doesNotMatch(render(), /必须先分享才能|分享后才能进入/);
});

test('public material is displayed once and cannot be shared again, even with a stale private duplicate', () => {
  const current = { ...view, public_knowledge: [material('my-claim', { text: '已公开的虚构材料', can_share: false, shared_by_character_id: 'fictional-a' })] };
  const html = render({ view: current });
  assert.match(html, /已公开的虚构材料/); assert.match(html, /由虚构角色甲公开/);
  assert.doesNotMatch(html, /虚构材料 my-claim/);
  assert.equal(canPerformFlowAction(current, share('knowledge', 'my-claim')), false);
});

test('the final phase hides advance but still permits an explicit allowed share', () => {
  const final = { ...view, phase_complete: true, can_advance: false };
  const tree = Panel(props({ view: final }));
  assert.equal(button(tree, '进入下一阶段'), undefined);
  assert.equal(canPerformFlowAction(final, share('evidence', 'my-evidence')), true);
  const html = render({ view: final });
  assert.match(html, /阶段材料已走到末段/); assert.match(html, /不会自动给出结局/);
  assert.doesNotMatch(html, /已完成整局|游戏已结束/);
});

test('busy, loading and stale states lock direct action callbacks; stale records can still be read and refreshed', () => {
  for (const state of [{ busy: true }, { loading: true }, { requiresRefresh: true }]) {
    let calls = 0;
    const tree = Panel(props({ ...state, onAction() { calls++; } }));
    button(tree, '进入下一阶段').props.onClick();
    for (const item of elements(tree, 'button').filter(node => node.props['aria-label'])) item.props.onClick();
    assert.equal(calls, 0);
  }
  const tree = Panel(props({ requiresRefresh: true, error: '请刷新演练核对。' }));
  assert.equal(button(tree, '刷新演练').props.disabled, false);
  assert.match(render({ requiresRefresh: true }), /虚构材料 my-claim/);
});

test('only projected fields render; hidden truths, future materials, paths and extra metadata stay absent', () => {
  const current = { ...view, truth: ['PRIVATE_TRUTH_SENTINEL'], settlement: { text: 'PRIVATE_SETTLEMENT_SENTINEL' }, sources: ['PRIVATE_PATH_SENTINEL'],
    locked_knowledge: [{ text: 'PRIVATE_FUTURE_SENTINEL' }], other_private: ['PRIVATE_OTHER_SENTINEL'] };
  const html = render({ view: current });
  assert.match(html, /虚构材料 my-claim/);
  assert.doesNotMatch(html, /PRIVATE_TRUTH|PRIVATE_SETTLEMENT|PRIVATE_PATH|PRIVATE_FUTURE|PRIVATE_OTHER/);
});

test('untrusted visible text remains inert and long content has bounded responsive wrapping', () => {
  const evil = '<script>FICTIONAL()</script> [open](https://evil.invalid)';
  const html = render({ view: { ...view, script: { ...view.script, title: evil }, introduction: { text: evil }, private_evidence: [material('long-id', { text: evil })] } });
  assert.match(html, /&lt;script&gt;FICTIONAL/); assert.doesNotMatch(html, /<script>|href="https:\/\/evil|<iframe/);
  assert.match(html, /min-w-0 max-w-4xl/); assert.match(html, /whitespace-pre-wrap break-all/);
});

test('a different flow or opening never displays this response or grants an action', () => {
  for (const route of [{ flowId: `flow-${'c'.repeat(32)}` }, { openingSessionId: 'another-opening' }]) {
    const html = render(route); assert.doesNotMatch(html, /虚构材料|虚构第一阶段|进入下一阶段/);
  }
  assert.doesNotMatch(render({ view: { ...view, revision: -1 } }), /虚构材料/);
  assert.doesNotMatch(render({ view: { ...view, runtime_ready: true } }), /虚构材料/);
});

test('service lookup handles null without mutation and encodes every path/query identifier', async t => {
  const calls = [];
  t.mock.method(global, 'fetch', async (...args) => { calls.push(args); return response(null); });
  const controller = new AbortController();
  assert.equal(await service.lookup('opening/id?x=1&y=2', controller.signal), null);
  await service.read('flow/id?x=1', controller.signal);
  assert.equal(calls[0][0], 'https://fixture.invalid/api/fusion/package-flows?opening_session_id=opening%2Fid%3Fx%3D1%26y%3D2');
  assert.equal(calls[1][0], 'https://fixture.invalid/api/fusion/package-flows/flow%2Fid%3Fx%3D1');
  for (const [, options] of calls) {
    assert.equal(options.method, undefined); assert.equal(options.cache, 'no-store'); assert.equal(options.signal, controller.signal);
    assert.equal(options.headers.Authorization, 'Bearer fictional-flow-token');
  }
});

test('explicit create and actions carry exact version preconditions; revision zero and target-free advance are preserved', async t => {
  const calls = [];
  t.mock.method(global, 'fetch', async (...args) => { calls.push(args); return response(view); });
  const create = { opening_session_id: view.opening_session_id, idempotency_key: 'fictional-create' };
  const advance = { action: 'ADVANCE_PHASE', expected_revision: 0, idempotency_key: 'fictional-advance' };
  const disclose = { ...share('evidence', 'my-evidence'), expected_revision: 1, idempotency_key: 'fictional-share' };
  await service.create(create); await service.act(view.flow_id, advance); await service.act(view.flow_id, disclose);
  assert.deepEqual(calls.map(([, options]) => JSON.parse(options.body)), [create, advance, disclose]);
  assert.ok(calls.every(([, options]) => options.method === 'POST' && options.cache === 'no-store' && options.headers['Content-Type'] === 'application/json'));
  assert.equal(calls[1][0], `https://fixture.invalid/api/fusion/package-flows/${view.flow_id}/actions`);
  assert.equal(Object.hasOwn(JSON.parse(calls[1][1].body), 'target'), false);
});

test('HTTP denial and conflict feedback never reads private error payloads or invites automatic advance replay', async t => {
  for (const [status, message] of [[401, /登录/], [403, /无权/], [404, /属于你的/], [409, /刷新演练.*不会自动重放/], [500, /稍后重试/]]) {
    let bodyRead = false;
    t.mock.method(global, 'fetch', async () => ({ ok: false, status, json: async () => { bodyRead = true; return { detail: 'PRIVATE_SERVER_SENTINEL' }; } }));
    await assert.rejects(service.read(view.flow_id), error => error instanceof PackageFlowError && error.status === status && message.test(error.message) && !error.message.includes('PRIVATE_SERVER'));
    assert.equal(bodyRead, false); t.mock.restoreAll();
  }
});

test('retry keys are reused only for the same operation, revision, flow and target', () => {
  const payload = { flow_id: view.flow_id, ...share('knowledge', 'my-claim'), expected_revision: 0 };
  const attempt = preparePackageFlowAttempt(payload);
  assert.strictEqual(preparePackageFlowAttempt({ ...payload }, attempt), attempt);
  for (const changed of [{ ...payload, expected_revision: 1 }, { ...payload, flow_id: 'other' }, { ...payload, target: { collection: 'evidence', id: 'my-claim' } }]) {
    assert.notEqual(preparePackageFlowAttempt(changed, attempt).key, attempt.key);
  }
  assert.notEqual(preparePackageFlowAttempt(payload).key, attempt.key);
});

// Isolated state/effect runner for request lifecycles. Browser and layout acceptance
// run separately; these tests use only invented records and a mocked fetch.
function hookRunner() {
  const slots = [], effects = [], pending = []; let index = 0;
  const hooks = {
    useState(initial) {
      const slot = index++; if (!(slot in slots)) slots[slot] = typeof initial === 'function' ? initial() : initial;
      return [slots[slot], value => { slots[slot] = typeof value === 'function' ? value(slots[slot]) : value; }];
    },
    useRef(initial) { const slot = index++; if (!(slot in slots)) slots[slot] = { current: initial }; return slots[slot]; },
    useEffect(callback, deps) {
      const slot = index++;
      if (!effects[slot] || deps.some((value, offset) => value !== effects[slot].deps[offset])) pending.push(() => {
        effects[slot]?.cleanup?.(); effects[slot] = { deps, cleanup: callback() };
      });
    },
  };
  return { hooks, run(callback) { index = 0; const value = callback(); while (pending.length) pending.shift()(); return value; }, unmount() { for (const effect of effects) effect?.cleanup?.(); } };
}
function workspaceHarness(overrides = {}) {
  const runner = hookRunner();
  const Workspace = compile('../src/components/PackageFlowPanel.tsx', panelImports(runner.hooks)).PackageFlowWorkspace;
  const config = { flowId: '', openingSessionId: view.opening_session_id, onCreated: async () => {}, ...overrides };
  return { render: () => runner.run(() => Workspace(config)).props, unmount: runner.unmount };
}

test('lookup restores an existing flow without creating it again or changing its fixed character', async t => {
  const calls = [];
  t.mock.method(global, 'fetch', async (...args) => { calls.push(args); return response({ ...view, revision: 4 }); });
  const workspace = workspaceHarness();
  try {
    workspace.render(); await settle(); const panel = workspace.render(); await panel.onCreate();
    assert.equal(panel.view.revision, 4); assert.equal(panel.view.selected_character_id, 'fictional-a');
    assert.equal(calls.length, 1); assert.equal(calls[0][1].method, undefined);
  } finally { workspace.unmount(); }
});

test('explicit creation is single-flight, reuses a failed key and navigates only after a successful save', async t => {
  const calls = [], navigation = []; let failures = 1;
  t.mock.method(global, 'fetch', async (url, options) => {
    calls.push({ url, options });
    if (options.method === 'POST') { if (failures--) throw new TypeError('invented network loss'); return response(view); }
    return response(null);
  });
  const workspace = workspaceHarness({ onCreated: async id => navigation.push(id) });
  try {
    workspace.render(); await settle(); let panel = workspace.render();
    assert.equal(calls.length, 1); panel.onCreate(); panel.onCreate(); await settle();
    assert.equal(calls.filter(call => call.options.method === 'POST').length, 1); assert.deepEqual(navigation, []);
    panel = workspace.render(); await panel.onCreate(); panel = workspace.render();
    const posts = calls.filter(call => call.options.method === 'POST');
    assert.equal(posts.length, 2); assert.equal(posts[0].options.body, posts[1].options.body);
    assert.deepEqual(navigation, [view.flow_id]); assert.equal(panel.view.selected_character_id, view.selected_character_id);
    assert.ok(calls.every(call => !call.url.endsWith('/actions')));
  } finally { workspace.unmount(); }
});

test('action network retry preserves its key and revision, then performs only a read to refresh the result', async t => {
  const calls = []; let failures = 1; let current = view;
  t.mock.method(global, 'fetch', async (url, options) => {
    calls.push({ url, options });
    if (options.method === 'POST') { if (failures--) throw new TypeError('invented network loss'); current = { ...view, revision: 1, current_phase: { id: 'phase-2', title: '虚构第二阶段' }, phase_complete: true, can_advance: false }; }
    return response(current);
  });
  const workspace = workspaceHarness({ flowId: view.flow_id, openingSessionId: '' });
  try {
    workspace.render(); await settle(); let panel = workspace.render();
    panel.onAction({ action: 'ADVANCE_PHASE' }); panel.onAction({ action: 'ADVANCE_PHASE' }); await settle();
    panel = workspace.render(); assert.match(panel.error, /相同操作会复用/);
    await panel.onAction({ action: 'ADVANCE_PHASE' }); panel = workspace.render();
    const posts = calls.filter(call => call.options.method === 'POST');
    assert.equal(posts.length, 2); assert.equal(posts[0].options.body, posts[1].options.body);
    assert.equal(JSON.parse(posts[0].options.body).expected_revision, 0);
    assert.equal(panel.view.revision, 1); assert.equal(calls.at(-1).options.method, undefined);
  } finally { workspace.unmount(); }
});

test('409 leaves already acquired materials visible and waits for an explicit refresh instead of replaying', async t => {
  const calls = []; let current = view;
  t.mock.method(global, 'fetch', async (url, options) => { calls.push({ url, options }); return options.method === 'POST' ? { ok: false, status: 409 } : response(current); });
  const workspace = workspaceHarness({ flowId: view.flow_id, openingSessionId: '' });
  try {
    workspace.render(); await settle(); await workspace.render().onAction({ action: 'ADVANCE_PHASE' });
    let panel = workspace.render(); assert.equal(panel.requiresRefresh, true); assert.equal(panel.view.private_knowledge[0].id, 'my-claim');
    await panel.onAction(share('knowledge', 'my-claim')); assert.equal(calls.length, 2);
    current = { ...view, revision: 1 }; panel.onReload(); workspace.render(); await settle(); panel = workspace.render();
    assert.equal(panel.requiresRefresh, false); assert.equal(panel.view.revision, 1);
    assert.equal(calls.length, 3); assert.equal(calls.at(-1).options.method, undefined);
  } finally { workspace.unmount(); }
});

test('a replayed older action response cannot roll back an already newer flow projection', async t => {
  const current = { ...view, revision: 4 }; let reads = 0;
  t.mock.method(global, 'fetch', async (url, options) => {
    if (options.method === 'POST') return response({ ...view, revision: 2 });
    reads++; return response(current);
  });
  const workspace = workspaceHarness({ flowId: view.flow_id, openingSessionId: '' });
  try {
    workspace.render(); await settle(); await workspace.render().onAction(share('knowledge', 'my-claim'));
    assert.equal(workspace.render().view.revision, 4); assert.equal(reads, 2);
  } finally { workspace.unmount(); }
});

test('manual refresh rejects a changed binding or older revision while retaining the last authorized materials', async t => {
  const observed = { ...view, revision: 4 };
  let current = observed;
  t.mock.method(global, 'fetch', async () => response(current));
  for (const changed of [
    { ...observed, revision: 3 }, { ...observed, selected_character_id: 'fictional-b' }, { ...observed, version_id: 99 },
    { ...observed, release_id: 99 }, { ...observed, package_hash: 'c'.repeat(64) }, { ...observed, opening_session_id: 'other-opening' }, null,
  ]) {
    current = observed;
    const workspace = workspaceHarness({ flowId: view.flow_id, openingSessionId: '' });
    try {
      workspace.render(); await settle(); assert.equal(workspace.render().view.revision, 4);
      current = changed;
      workspace.render().onReload(); workspace.render(); await settle();
      let panel = workspace.render();
      assert.strictEqual(panel.view, observed); assert.equal(panel.requiresRefresh, true); assert.match(panel.error, /已保留原有材料/);
      current = { ...observed, revision: 5 };
      panel.onReload(); workspace.render(); await settle(); panel = workspace.render();
      assert.equal(panel.view.revision, 5); assert.equal(panel.requiresRefresh, false); assert.equal(panel.error, '');
    } finally { workspace.unmount(); }
  }
});

test('access denial during read, write or post-write refresh hides materials permanently through later network failures', async t => {
  let mode = 'read', phase = 'initial';
  t.mock.method(global, 'fetch', async (url, options) => {
    if (phase === 'initial') return response(view);
    if (phase === 'network') throw new TypeError('invented network failure after denied access');
    if (mode === 'followup' && options.method === 'POST') return response({ ...view, revision: 1 });
    return { ok: false, status: 403 };
  });
  for (mode of ['read', 'write', 'followup']) {
    phase = 'initial';
    const workspace = workspaceHarness({ flowId: view.flow_id, openingSessionId: '' });
    try {
      workspace.render(); await settle(); assert.equal(workspace.render().view.flow_id, view.flow_id);
      phase = 'denied';
      if (mode === 'read') { workspace.render().onReload(); workspace.render(); await settle(); }
      else await workspace.render().onAction(share('knowledge', 'my-claim'));
      let panel = workspace.render(); assert.equal(panel.view, undefined); assert.equal(panel.requiresRefresh, true);
      phase = 'network'; panel.onReload(); workspace.render(); await settle(); panel = workspace.render();
      assert.equal(panel.view, undefined); assert.equal(panel.requiresRefresh, true);
      assert.doesNotMatch(renderToStaticMarkup(React.createElement(Panel, panel)), /虚构材料 my-claim|虚构材料 secret/);
    } finally { workspace.unmount(); }
  }
});

test('saved action with a failed refresh requires a read, not another action attempt', async t => {
  let reads = 0; const calls = [];
  t.mock.method(global, 'fetch', async (url, options) => {
    calls.push({ url, options });
    if (options.method === 'POST') return response({ ...view, revision: 1 });
    if (reads++ > 0) throw new TypeError('invented read failure');
    return response(view);
  });
  const workspace = workspaceHarness({ flowId: view.flow_id, openingSessionId: '' });
  try {
    workspace.render(); await settle(); await workspace.render().onAction(share('knowledge', 'my-claim'));
    const panel = workspace.render(); assert.equal(panel.requiresRefresh, true); assert.match(panel.error, /操作已保存，但刷新失败/);
    await panel.onAction({ action: 'ADVANCE_PHASE' }); assert.equal(calls.filter(call => call.options.method === 'POST').length, 1);
  } finally { workspace.unmount(); }
});

test('leaving a workspace aborts late lookup and late creation without updating content or navigation', async t => {
  const pending = [], navigation = [];
  t.mock.method(global, 'fetch', (url, options) => new Promise(resolve => pending.push({ url, options, resolve })));
  const first = workspaceHarness();
  first.render(); first.unmount(); pending[0].resolve(response(view)); await settle();
  assert.equal(pending[0].options.signal.aborted, true); assert.equal(first.render().view, undefined);
  const second = workspaceHarness({ onCreated: async id => navigation.push(id) });
  try {
    second.render(); pending[1].resolve(response(null)); await settle(); second.render().onCreate();
    second.unmount(); pending[2].resolve(response(view)); await settle();
    assert.equal(pending[2].options.signal.aborted, true); assert.deepEqual(navigation, []); assert.equal(second.render().view, null);
  } finally { first.unmount(); second.unmount(); }
});

test('page query changes remount the workspace and creation uses only the saved flow ID in its new URL', async () => {
  const calls = [];
  const router = { isReady: true, asPath: '/play/package-flow?opening_session_id=fictional-opening', query: { opening_session_id: 'fictional-opening' }, replace: async value => calls.push(value) };
  const Frame = ({ children }) => children;
  const Page = compile('../src/pages/play/package-flow.tsx', name => {
    if (name === 'next/router') return { useRouter: () => router };
    if (name === '@/components/AuthGuard' || name === '@/components/AppLayout') return { default: Frame };
    if (name === '@/components/PackageFlowPanel') return { PackageFlowWorkspace: Frame };
    return require(name);
  }).default;
  const first = Page().props.children.props.children;
  assert.equal(first.key, router.asPath); assert.equal(first.props.openingSessionId, 'fictional-opening');
  await first.props.onCreated(view.flow_id);
  assert.deepEqual(calls, [{ pathname: '/play/package-flow', query: { flow: view.flow_id } }]);
  router.asPath = `/play/package-flow?flow=${view.flow_id}`; router.query = { flow: view.flow_id };
  const next = Page().props.children.props.children;
  assert.notEqual(first.key, next.key); assert.equal(next.props.flowId, view.flow_id); assert.equal(next.props.openingSessionId, '');
});

test('opening preview removes the player stage-inspection entry and makes no flow request', async () => {
  const runner = hookRunner(); const reads = [];
  const opening = { ...view, session_id: 'fictional-opening/id?x=1' };
  const router = { isReady: true, asPath: '/play/package-preview?session=fictional', query: { session: 'fictional' } };
  const Page = compile('../src/pages/play/package-preview.tsx', name => {
    if (name === '@/components/OpeningImage') return { default: () => null };
    if (name === 'react') return { ...React, ...runner.hooks };
    if (name === 'next/router') return { useRouter: () => router };
    if (name === 'next/link') return link;
    if (name === '@/components/PlayText') return compile('../src/components/PlayText.tsx');
    if (name === '@/components/PlayReferencePanel') return { default: () => null };
    if (name === '@/stores/authStore') return { useAuthStore: select => select({ isAuthenticated: true, user: { id: 'account-a' } }) };
    if (name === '@/components/AuthGuard' || name === '@/components/AppLayout') return { default: ({ children }) => children };
    if (name === '@/services/packagePlayService') return { default: {} };
    if (name === '@/services/packagePreviewService') return { default: { read: async id => { reads.push(id); return opening; } }, watchPackagePreviewAuth: () => ({ isCurrent: () => true, dispose() {} }) };
    return require(name);
  }).default;
  const workspace = Page().props.children.props.children;
  try {
    runner.run(() => workspace.type(workspace.props)); await settle();
    const html = renderToStaticMarkup(runner.run(() => workspace.type(workspace.props)));
    assert.deepEqual(reads, ['fictional']);
    assert.match(html, /开始游戏/); assert.doesNotMatch(html, /package-flow|检查阶段规则|href="\/play\/package-play/);
    assert.doesNotMatch(html, /<button[^>]*>进入下一阶段|<button[^>]*>开始阶段演练/);
  } finally { runner.unmount(); }
});
