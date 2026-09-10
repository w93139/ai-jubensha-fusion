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
  new Function('require', 'module', 'exports', outputText)(imports, compiled, compiled.exports);
  return compiled.exports;
}
const serviceModule = compile('../src/services/scriptPublicationService.ts', name => {
  if (name === '@/stores/configStore') return { config: { api: { baseUrl: 'https://fixture.invalid' } } };
  if (name === '@/services/authService') return { default: { getToken: () => 'fictional-publication-token' } };
  return require(name);
});
const { default: service, preparePublicationAttempt, ScriptPublicationError } = serviceModule;
const panelImports = hooks => name => {
  if (name === 'react' && hooks) return { ...React, ...hooks };
  if (name === 'next/link') return { default: ({ children, ...props }) => React.createElement('a', props, children) };
  if (name === '@/services/scriptPublicationService') return serviceModule;
  return require(name);
};
const { ScriptPublicationView: Panel, modelFindingKey, modelDecisions, approvalPayload, publishPayload } = compile('../src/components/ScriptPublicationPanel.tsx', panelImports());
const candidate = { id: 17, script_key: 'fictional', content_version: 'test-0.1', title: '完全虚构的发布候选', package_hash: 'a'.repeat(64), manifest_hash: 'd'.repeat(64), contract_version: 'script-package/1.1', player_count: 2 };
const finding = { id: 'fictional-warning', severity: 'WARNING', category: 'PROVENANCE', target: { collection: 'introduction', id: null }, message: '完全虚构的模型意见', sources: [{ source_id: 'fictional-source', anchor: 'L1-L3' }] };
const report = { schema_version: 'script-audit/1.0', summary: '完全虚构的模型审核报告', coverage: ['PROVENANCE', 'TIMELINE', 'EVIDENCE', 'KNOWLEDGE_BOUNDARY', 'PLAYABILITY'], findings: [finding] };
const model = { job_id: 8, state: 'COMPLETED', attempt_id: 9, status: 'SUCCEEDED', output_hash: 'e'.repeat(64), receipt_hash: 'f'.repeat(64), report, error_code: null };
const gate = { candidate, basis_hash: 'b'.repeat(64), bundle_hash: 'c'.repeat(64), manual_reports: [], model_reports: [model], checks: [{ code: 'SOURCE_VERIFIED', passed: true, message: '虚构来源已核验' }], can_approve: true, can_publish: false, approval: null, release: null };
const document = { id: candidate.id, script_key: candidate.script_key, content_version: candidate.content_version, package_hash: candidate.package_hash, manifest_hash: candidate.manifest_hash,
  package: { title: candidate.title, introduction: { text: '虚构开场，仅用于测试。', sources: finding.sources }, characters: [{ id: 'fictional-role', name: '虚构角色', sources: finding.sources }],
    phases: [{ id: 'fictional-phase', title: '虚构阶段' }], knowledge: [{ id: 'fictional-private', text: '虚构角色私有材料', visibility: 'CHARACTER_PRIVATE', sources: finding.sources }],
    evidence: [{ id: 'fictional-evidence', text: '虚构证据' }], truth: [{ id: 'fictional-truth', text: '虚构后台真相' }], settlement: { instructions: { text: '虚构结算' } } }, publication_ready: false };
const decisions = [{ job_id: model.job_id, finding_id: finding.id, status: 'DISMISSED', note: '虚构资料核对后判定误报的依据' }];
const drafts = { [modelFindingKey(model.job_id, finding.id)]: { status: decisions[0].status, note: decisions[0].note } };
const approval = { id: 19, version_id: candidate.id, approval_hash: '1'.repeat(64), package_hash: candidate.package_hash, bundle_hash: gate.bundle_hash, basis_hash: gate.basis_hash,
  source_report_hash: '2'.repeat(64), submitted_by: 7, note: '管理员虚构核对说明', model_dispositions: decisions, policy_version: 'fictional-policy', created_at: '2026-09-06T00:00:00Z', valid: true };
const release = { id: 20, version_id: candidate.id, release_hash: '3'.repeat(64), approval_id: approval.id, approval_hash: approval.approval_hash, package_hash: candidate.package_hash,
  bundle_hash: gate.bundle_hash, basis_hash: gate.basis_hash, source_report_hash: approval.source_report_hash, submitted_by: 7, created_at: approval.created_at, current_approval_valid: true };
const props = overrides => ({ candidate, gate, drafts: {}, note: '', confirmed: false, loading: false, reading: false, busy: false, error: '', notice: '', onReload() {}, onRead() {}, onDraft() {}, onNote() {}, onConfirmed() {}, onApprove() {}, onPublish() {}, ...overrides });
const render = overrides => renderToStaticMarkup(React.createElement(Panel, props(overrides)));
const elements = (tree, type) => {
  if (Array.isArray(tree)) return tree.flatMap(child => elements(child, type));
  if (!React.isValidElement(tree)) return [];
  return [...(tree.type === type ? [tree] : []), ...elements(tree.props.children, type)];
};
const byLabel = (tree, type, label) => elements(tree, type).find(node => node.props['aria-label'] === label);
const button = (tree, label) => elements(tree, 'button').find(node => node.props.children === label);
const response = value => ({ ok: true, json: async () => ({ success: true, data: value }) });
const settle = () => new Promise(resolve => setImmediate(resolve));

test('initial review never approves, publishes, reads private bodies or pre-fills human decisions', () => {
  let calls = 0;
  const tree = Panel(props({ onApprove() { calls++; }, onPublish() { calls++; }, onRead() { calls++; } }));
  assert.equal(calls, 0);
  assert.equal(byLabel(tree, 'input', '已核对本版正文、来源和模型意见').props.checked, false);
  assert.equal(byLabel(tree, 'input', '已核对本版正文、来源和模型意见').props.disabled, true);
  assert.equal(elements(tree, 'select')[0].props.value, '');
  assert.equal(byLabel(tree, 'textarea', '最终核对说明').props.value, '');
  assert.equal(button(tree, '确认本版内容').props.disabled, true);
  assert.equal(button(tree, '登记发布版本').props.disabled, true);
  const html = render();
  assert.match(html, /完整剧本玩法仍在开发/);
  assert.match(html, /模型建议不会替你批准/);
  assert.doesNotMatch(html, /虚构角色私有材料/);
});

test('confirmation requires observed candidate body, explicit responsibility, notes and every model warning or blocker decision', () => {
  const valid = [candidate, gate, document, drafts, '已核对虚构材料', true];
  assert.ok(approvalPayload(...valid));
  for (const [index, value] of [[1, undefined], [2, undefined], [3, {}], [4, '  '], [5, false], [4, 'x'.repeat(4001)]]) {
    const args = [...valid]; args[index] = value; assert.equal(approvalPayload(...args), undefined);
  }
  assert.equal(approvalPayload(candidate, { ...gate, can_approve: false }, document, drafts, '虚构', true), undefined);
  assert.equal(approvalPayload(candidate, { ...gate, checks: [{ passed: false }] }, document, drafts, '虚构', true), undefined);
  assert.equal(approvalPayload(candidate, gate, { ...document, package_hash: 'different' }, drafts, '虚构', true), undefined);
  assert.equal(approvalPayload(candidate, { ...gate, candidate: { ...candidate, id: 99 } }, document, drafts, '虚构', true), undefined);
});

test('blockers cannot be acknowledged; model decisions stay bound to the observed job and finding', () => {
  const blocker = { ...finding, severity: 'BLOCKER' };
  const second = { ...model, job_id: 80, report: { ...report, findings: [blocker, { ...finding, id: 'info', severity: 'INFO' }] } };
  const complete = { ...drafts, [modelFindingKey(80, finding.id)]: { status: 'DISMISSED', note: '第二任务独立核对的虚构依据' }, [modelFindingKey(800, 'foreign')]: { status: 'DISMISSED', note: '不能被发送' } };
  const current = { ...gate, model_reports: [model, second] };
  assert.equal(modelDecisions(current, drafts), undefined);
  assert.equal(modelDecisions(current, { ...complete, [modelFindingKey(80, finding.id)]: { status: 'ACKNOWLEDGED', note: '不能知悉阻断' } }), undefined);
  assert.deepEqual(modelDecisions(current, complete), [...decisions, { job_id: 80, finding_id: finding.id, status: 'DISMISSED', note: '第二任务独立核对的虚构依据' }]);
  const tree = Panel(props({ gate: current }));
  const select = byLabel(tree, 'select', `模型任务 80 问题 ${finding.id} 的处理结果`);
  assert.equal(elements(select, 'option').find(node => node.props.value === 'ACKNOWLEDGED').props.disabled, true);
  assert.equal(elements(tree, 'select').length, 2);
});

test('successful model status without a complete report never makes confirmation possible', () => {
  for (const item of [{ ...model, report: null }, { ...model, status: 'FAILED' }, { ...model, status: 'MISSING', error_code: 'PRIVATE_SERVER_ERROR' }]) {
    assert.equal(modelDecisions({ ...gate, model_reports: [item] }, drafts), undefined);
    const html = render({ gate: { ...gate, model_reports: [item] } });
    assert.match(html, /尚无可用于确认的完整模型审核报告/);
    assert.doesNotMatch(html, /PRIVATE_SERVER_ERROR/);
  }
});

test('settled failed audit history remains visible without blocking decisions for a later valid report', () => {
  const history = { ...model, job_id: 81, state: 'BLOCKED', status: 'FAILED', report: null, error_code: 'AUDIT_OUTPUT_INVALID' };
  const current = { ...gate, model_reports: [history, { ...history, job_id: 82, state: 'CANCELLED', status: 'MISSING' }, model] };
  assert.deepEqual(modelDecisions(current, drafts), decisions);
  assert.ok(approvalPayload(candidate, current, document, drafts, '虚构人工核对', true));
  const html = render({ gate: current });
  assert.match(html, /模型任务 #81/); assert.match(html, /模型任务 #82/);
  assert.match(html, /尚无可用于确认的完整模型审核报告/);
  assert.match(html, /完全虚构的模型审核报告/);
  assert.equal(modelDecisions({ ...current, model_reports: [model, { ...history, report }] }, drafts), undefined);
});

test('approval and publication are separate explicit actions carrying exact observed CAS bindings', () => {
  const calls = [];
  const tree = Panel(props({ document, drafts, note: '  虚构人工最终核对  ', confirmed: true, onApprove(value) { calls.push(value); }, onPublish() { assert.fail('approval must not publish'); } }));
  byLabel(tree, 'form', '人工确认本版内容').props.onSubmit({ preventDefault() {} });
  assert.deepEqual(calls, [{ expected_package_hash: candidate.package_hash, bundle_hash: gate.bundle_hash, expected_basis_hash: gate.basis_hash, model_dispositions: decisions, note: '虚构人工最终核对' }]);
  assert.equal(button(tree, '登记发布版本').props.disabled, true);
  const published = [];
  const approved = Panel(props({ gate: { ...gate, approval, can_publish: true }, onPublish(value) { published.push(value); } }));
  button(approved, '登记发布版本').props.onClick();
  assert.deepEqual(published, [{ approval_id: approval.id, expected_approval_hash: approval.approval_hash, expected_basis_hash: gate.basis_hash }]);
});

test('stale approval, package mismatch, release duplication or denied server gate prevents publication', () => {
  const approved = { ...gate, approval, can_publish: true };
  for (const current of [{ ...approved, can_publish: false }, { ...approved, release }, { ...approved, basis_hash: 'changed' },
    { ...approved, approval: { ...approval, valid: false } }, { ...approved, approval: { ...approval, version_id: 999 } },
    { ...approved, approval: { ...approval, bundle_hash: 'foreign' } }, { ...approved, approval: { ...approval, package_hash: 'foreign' } }]) {
    assert.equal(publishPayload(candidate, current), undefined);
  }
  assert.match(render({ gate: { ...approved, approval: { ...approval, valid: false } } }), /旧确认已失效/);
});

test('busy and loading lock even direct event callbacks for both irreversible user actions', () => {
  for (const state of [{ busy: true }, { loading: true }, { reading: true }]) {
    let calls = 0;
    const tree = Panel(props({ gate: { ...gate, approval, can_publish: true }, document, drafts, note: '虚构核对', confirmed: true,
      ...state, onApprove() { calls++; }, onPublish() { calls++; }, onRead() { calls++; } }));
    byLabel(tree, 'form', '人工确认本版内容').props.onSubmit({ preventDefault() {} });
    button(tree, '登记发布版本').props.onClick();
    button(tree, '重新读取候选正文').props.onClick();
    assert.equal(calls, 0);
  }
});

test('candidate bodies, model reports and saved human decisions render only as inert bounded text', () => {
  const evil = '<script>FICTIONAL_SENTINEL()</script> [click](https://evil.invalid)';
  const content = { ...document, package: { ...document.package, introduction: { text: evil }, knowledge: [{ id: evil, text: evil, sources: [{ source_id: evil }] }] } };
  const html = render({ document: content, gate: { ...gate, model_reports: [{ ...model, report: { ...report, summary: evil, findings: [{ ...finding, message: evil, sources: [{ source_id: evil, anchor: evil }] }] } }],
    approval: { ...approval, note: evil, model_dispositions: [{ ...decisions[0], note: evil }] } } });
  assert.match(html, /&lt;script&gt;FICTIONAL_SENTINEL/);
  assert.doesNotMatch(html, /<script>|href="https:\/\/evil|<iframe|<img/);
  assert.match(html, /max-h-\[36rem\].*max-w-full.*overflow-auto/);
  assert.match(html, /whitespace-pre-wrap break-all/);
  assert.match(html, /虚构后台真相/);
});

test('another candidate response never exposes bodies, model findings or approval records', () => {
  const html = render({ document: { ...document, id: 100 }, gate: { ...gate, candidate: { ...candidate, id: 100 }, approval } });
  assert.doesNotMatch(html, /虚构角色私有材料|完全虚构的模型审核报告|管理员虚构核对说明/);
});

test('saved release points to its fixed opening preview and never claims full playability', () => {
  const html = render({ gate: { ...gate, approval, release } });
  assert.match(html, /href="\/play\/package-preview\?release_id=20"/);
  assert.match(html, /完整剧本玩法仍在开发/);
  assert.match(html, /已登记发布版本/);
  assert.doesNotMatch(html, /<button[^>]*>确认本版内容|<button[^>]*>登记发布版本/);
  const stale = render({ gate: { ...gate, release: { ...release, current_approval_valid: false } } });
  assert.match(stale, /当前审核依据已变化/);
  assert.match(stale, /已创建的开场预览仍保留原版本/);
  assert.doesNotMatch(stale, /href="\/play\/package-preview|查看本版开场预览/);
});

test('same failed action keeps its idempotency key; changed evidence or note gets a new key', () => {
  const payload = approvalPayload(candidate, gate, document, drafts, '虚构说明', true);
  const attempt = preparePublicationAttempt(payload);
  assert.strictEqual(preparePublicationAttempt({ ...payload }, attempt), attempt);
  assert.notEqual(preparePublicationAttempt({ ...payload, expected_basis_hash: 'changed' }, attempt).key, attempt.key);
  assert.notEqual(preparePublicationAttempt({ ...payload, note: '修改说明' }, attempt).key, attempt.key);
  assert.notEqual(preparePublicationAttempt(payload).key, attempt.key);
});

test('service uses private admin paths, no-store, auth, cancellation and exact explicit POST bodies', async () => {
  const original = global.fetch;
  const calls = [];
  global.fetch = async (...args) => { calls.push(args); return response(gate); };
  try {
    const controller = new AbortController();
    const confirm = { ...approvalPayload(candidate, gate, document, drafts, '虚构核对', true), idempotency_key: 'fictional-confirm' };
    const publish = { ...publishPayload(candidate, { ...gate, approval, can_publish: true }), idempotency_key: 'fictional-publish' };
    await service.gate(17, controller.signal); await service.candidate(17, controller.signal);
    await service.approve(17, confirm, controller.signal); await service.publish(17, publish, controller.signal);
    assert.deepEqual(calls.map(([url]) => url), ['publication', '', 'approvals', 'publish'].map(suffix => `https://fixture.invalid/api/admin/fusion/script-packages/17${suffix ? `/${suffix}` : ''}`));
    for (const [, options] of calls) {
      assert.equal(options.cache, 'no-store'); assert.equal(options.signal, controller.signal);
      assert.equal(options.headers.Authorization, 'Bearer fictional-publication-token');
    }
    assert.equal(calls[0][1].method, undefined); assert.equal(calls[1][1].method, undefined);
    assert.equal(calls[2][1].method, 'POST'); assert.deepEqual(JSON.parse(calls[2][1].body), confirm);
    assert.equal(calls[3][1].method, 'POST'); assert.deepEqual(JSON.parse(calls[3][1].body), publish);
  } finally { global.fetch = original; }
});

test('auth denial and CAS errors are actionable without reflecting private backend details', async () => {
  const original = global.fetch;
  try {
    for (const [status, expected] of [[401, /登录/], [403, /管理员/], [409, /刷新.*旧确认不能直接发布/], [422, /处理结果与必填说明/]]) {
      global.fetch = async () => ({ ok: false, status, json: async () => ({ detail: 'PRIVATE_ERROR_SENTINEL' }) });
      await assert.rejects(service.gate(17), error => {
        assert.ok(error instanceof ScriptPublicationError); assert.equal(error.status, status);
        assert.match(error.message, expected); assert.doesNotMatch(error.message, /PRIVATE_ERROR_SENTINEL/); return true;
      });
    }
    global.fetch = async () => ({ ok: true, json: async () => ({ success: false, data: 'PRIVATE_ERROR_SENTINEL' }) });
    await assert.rejects(service.gate(17), /未收到完整发布结果/);
  } finally { global.fetch = original; }
});

// An isolated hook runner exercises this workspace's requests and callbacks without a browser,
// services or real data. Browser timing/layout is checked separately by the acceptance task.
function workspaceHarness() {
  const slots = [], effects = [], pending = [];
  let index = 0;
  const hooks = {
    useState(initial) {
      const slot = index++;
      if (!(slot in slots)) slots[slot] = typeof initial === 'function' ? initial() : initial;
      return [slots[slot], value => { slots[slot] = typeof value === 'function' ? value(slots[slot]) : value; }];
    },
    useRef(initial) { const slot = index++; if (!(slot in slots)) slots[slot] = { current: initial }; return slots[slot]; },
    useEffect(callback, deps) {
      const slot = index++;
      if (!effects[slot] || deps.some((value, position) => value !== effects[slot].deps[position])) {
        pending.push(() => { effects[slot]?.cleanup?.(); effects[slot] = { deps, cleanup: callback() }; });
      }
    },
  };
  const Wrapper = compile('../src/components/ScriptPublicationPanel.tsx', panelImports(hooks)).default;
  let current = { candidate, reviewToken: 'fictional-reviews-1', externalLocked: false };
  return {
    render(overrides = {}) { current = { ...current, ...overrides }; index = 0; const element = Wrapper(current); const view = element.type(element.props); while (pending.length) pending.shift()(); return view.props; },
    unmount() { for (const item of effects) item?.cleanup?.(); },
  };
}

test('workspace initially reads only the gate, and ignores all late replies after leaving the candidate', async () => {
  const original = global.fetch;
  const pending = [];
  global.fetch = (url, options) => new Promise(resolve => pending.push({ url, options, resolve }));
  const workspace = workspaceHarness();
  try {
    workspace.render();
    assert.equal(pending.length, 1); assert.match(pending[0].url, /\/publication$/); assert.equal(pending[0].options.method, undefined);
    workspace.unmount(); pending[0].resolve(response(gate)); await settle();
    assert.equal(pending[0].options.signal.aborted, true);
    assert.equal(workspace.render().gate, undefined);
  } finally { workspace.unmount(); global.fetch = original; }
});

test('workspace retries the same failed confirmation once per click, then refreshes without publishing', async () => {
  const original = global.fetch;
  const calls = []; let approved = false; let failures = 1;
  global.fetch = async (url, options) => {
    calls.push({ url, options });
    if (url.endsWith('/approvals')) { if (failures--) throw new TypeError('fictional network loss'); approved = true; return response(approval); }
    if (url.endsWith('/publication')) return response(approved ? { ...gate, approval, can_publish: true } : gate);
    return response(document);
  };
  const workspace = workspaceHarness();
  try {
    workspace.render(); await settle();
    await workspace.render().onRead();
    workspace.render().onDraft(modelFindingKey(model.job_id, finding.id), drafts[modelFindingKey(model.job_id, finding.id)]);
    workspace.render().onNote('虚构最终核对'); workspace.render().onConfirmed(true);
    let view = workspace.render();
    let payload = approvalPayload(candidate, view.gate, view.document, view.drafts, view.note, view.confirmed);
    view.onApprove(payload); view.onApprove(payload); await settle();
    assert.equal(calls.filter(call => call.url.endsWith('/approvals')).length, 1);
    view = workspace.render(); assert.match(view.error, /相同内容会复用/);
    payload = approvalPayload(candidate, view.gate, view.document, view.drafts, view.note, view.confirmed);
    view.onApprove(payload); await settle(); view = workspace.render();
    const writes = calls.filter(call => call.options.method === 'POST');
    assert.equal(writes.length, 2);
    assert.equal(JSON.parse(writes[0].options.body).idempotency_key, JSON.parse(writes[1].options.body).idempotency_key);
    assert.equal(view.gate.approval.id, approval.id); assert.equal(view.confirmed, false);
    assert.match(view.notice, /尚未登记发布版本/);
    assert.equal(calls.filter(call => call.url.endsWith('/publish')).length, 0);
    assert.equal(calls.at(-1).options.method, undefined);
  } finally { workspace.unmount(); global.fetch = original; }
});

test('changed review basis clears prior human selections; an old approval cannot survive refresh', async () => {
  const original = global.fetch;
  let current = gate;
  global.fetch = async url => response(url.endsWith('/publication') ? current : document);
  const workspace = workspaceHarness();
  try {
    workspace.render(); await settle(); await workspace.render().onRead();
    workspace.render().onDraft(modelFindingKey(model.job_id, finding.id), drafts[modelFindingKey(model.job_id, finding.id)]);
    workspace.render().onNote('虚构旧说明'); workspace.render().onConfirmed(true);
    current = { ...gate, basis_hash: 'new-basis', approval: { ...approval, valid: false } };
    workspace.render({ reviewToken: 'fictional-reviews-2' }); await settle();
    const view = workspace.render();
    assert.equal(view.confirmed, false); assert.deepEqual(view.drafts, {}); assert.equal(view.note, '');
    assert.match(view.notice, /审核依据已变化/); assert.equal(publishPayload(candidate, view.gate), undefined);
  } finally { workspace.unmount(); global.fetch = original; }
});

test('409 confirmation conflict clears explicit approval and refreshes gate without retrying a write', async () => {
  const original = global.fetch;
  const calls = [];
  global.fetch = async (url, options) => { calls.push({ url, options }); return url.endsWith('/approvals') ? { ok: false, status: 409 } : response(url.endsWith('/publication') ? gate : document); };
  const workspace = workspaceHarness();
  try {
    workspace.render(); await settle(); await workspace.render().onRead();
    workspace.render().onDraft(modelFindingKey(model.job_id, finding.id), drafts[modelFindingKey(model.job_id, finding.id)]);
    workspace.render().onNote('虚构说明'); workspace.render().onConfirmed(true);
    let view = workspace.render(); view.onApprove(approvalPayload(candidate, view.gate, view.document, view.drafts, view.note, view.confirmed));
    await settle(); workspace.render(); await settle(); view = workspace.render();
    assert.equal(view.confirmed, false); assert.deepEqual(view.drafts, {}); assert.match(view.error, /旧确认不能直接发布/);
    assert.equal(calls.filter(call => call.options.method === 'POST').length, 1);
    assert.equal(calls.at(-1).options.method, undefined);
  } finally { workspace.unmount(); global.fetch = original; }
});
