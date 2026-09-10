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
const { default: Panel, authoringSourceUnavailable, canQueueAuthoringJob, canCancelAuthoringJob, canRecoverAuthoringJob } = compile('../src/components/AuthoringJobsPanel.tsx', name => name === 'next/link'
  ? { default: ({ children, ...props }) => React.createElement('a', props, children) }
  : name === './AuthoringRulePlanInput' ? compile('../src/components/AuthoringRulePlanInput.tsx') : require(name));
const { default: service, AuthoringJobError, prepareAuthoringQueueAttempt, watchAuthoringJobs } = compile('../src/services/authoringJobService.ts', name => {
  if (name === '@/stores/configStore') return { config: { api: { baseUrl: 'https://fixture.invalid' } } };
  if (name === '@/services/authService') return { default: { getToken: () => 'fictional-authoring-token' } };
  return require(name);
});

const source = { id: 'source-a', relative_path: 'fictional/material.md', kind: 'original', material_type: 'rules', original_paths: [], sha256: 'a'.repeat(64), size_bytes: 50, media_type: 'text/markdown' };
const bundle = { bundle_hash: 'b'.repeat(64), script_key: 'fictional', status: 'FROZEN', edition: '虚构材料版', file_count: 1, sources: [source] };
const draft = { title: '虚构编译测试', content_version: '0.1.0', player_count: 5, source_ids: [source.id] };
const job = { id: 12, title: draft.title, bundle_hash: bundle.bundle_hash, source_ids: draft.source_ids, content_version: draft.content_version, player_count: draft.player_count, state: 'QUEUED', step: 'COMPILE', revision: 1, created_at: '2026-09-05T00:00:00Z', updated_at: '2026-09-05T00:00:00Z', candidate_version_id: null, source_report_hash: null, error_code: null, publication_ready: false, model_snapshot: { provider: 'fictional-provider', model: 'fictional-model', pricing_version: 'fictional-prices' }, attempts: [], charged_cost_cny: '0' };
const props = overrides => ({ bundles: [bundle], bundle, bundleHash: bundle.bundle_hash, sourceQuery: '', draft, jobs: [job], selectedJobId: String(job.id), job, loading: false, sourceLoading: false, detailLoading: false, busy: false, error: '', notice: '', onBundle() {}, onSourceQuery() {}, onDraft() {}, onQueue() {}, onSelectJob() {}, onReload() {}, onCancel() {}, onRecover() {}, ...overrides });
const render = overrides => renderToStaticMarkup(React.createElement(Panel, props(overrides)));

test('frozen rule plan requires explicit mode, loaded draft and matching metadata', () => {
  const plan = { schema_version: 'script-package/1.2', title: draft.title, content_version: draft.content_version, player_count: draft.player_count };
  const frozen = { ...draft, package_contract: 'script-package/1.2', rule_plan: plan };
  assert.equal(canQueueAuthoringJob(bundle, frozen), true);
  for (const change of [{ rule_plan: null }, { package_contract: undefined },
    { rule_plan: { ...plan, title: 'different' } }, { rule_plan: { ...plan, content_version: 'different' } },
    { rule_plan: { ...plan, player_count: 2 } }, { rule_plan: { ...plan, schema_version: 'script-package/1.1' } }]) {
    assert.equal(canQueueAuthoringJob(bundle, { ...frozen, ...change }), false);
  }
  assert.equal(canQueueAuthoringJob(bundle, draft), true);
});

test('frozen rule plan upload remains a draft and does not expose uploaded content', () => {
  const html = render({ draft: { ...draft, package_contract: 'script-package/1.2', rule_plan: { private_text: 'PRIVATE_PLAN_SENTINEL' } } });
  assert.match(html, /固定规则草案，只摘录原文/);
  assert.match(html, /规则草案文件/);
  assert.match(html, /草案仍需人工审核/);
  assert.doesNotMatch(html, /PRIVATE_PLAN_SENTINEL/);
});

test('rule plan changes receive a new queue key, identical request reuses its key', () => {
  const payload = { ...draft, package_contract: 'script-package/1.2', rule_plan: { cost: 1 } };
  const first = prepareAuthoringQueueAttempt(payload);
  assert.equal(prepareAuthoringQueueAttempt(payload, first), first);
  assert.notEqual(prepareAuthoringQueueAttempt({ ...payload, rule_plan: { cost: 2 } }, first).key, first.key);
});

test('saved task explains its frozen-rule mode without claiming semantic approval', () => {
  const html = render({ job: { ...job, model_snapshot: { ...job.model_snapshot, schema_version: 'authoring-model/1.3' } } });
  assert.match(html, /本任务使用固定规则草案/);
  assert.match(html, /草案仍需人工审核/);
});

test('text confirmation mode preserves prepared text and still requires a loaded plan', () => {
  const confirmed = { ...draft, package_contract: 'script-package/1.2', compiler_mode: 'CONFIRM_FROZEN_TEXT', rule_plan: { schema_version: 'script-package/1.2', title: draft.title, content_version: draft.content_version, player_count: draft.player_count } };
  assert.equal(canQueueAuthoringJob(bundle, confirmed), true);
  assert.equal(canQueueAuthoringJob(bundle, { ...confirmed, rule_plan: null }), false);
  assert.equal(canQueueAuthoringJob(bundle, { ...confirmed, compiler_mode: 'PRIVATE_SENTINEL' }), false);
  const html = render({ draft: confirmed, job: { ...job, model_snapshot: { ...job.model_snapshot, schema_version: 'authoring-model/1.4' } } });
  assert.match(html, /规则和正文均保持草案原样/);
  assert.match(html, /本任务固定规则和正文，AI 只检查内容/);
});

test('changing confirmation mode clears its frozen file and restores old request fields', () => {
  let changed;
  let tree = Panel(props({ onDraft: value => { changed = value; } }));
  const select = elements(tree, 'select').find(item => item.props['aria-label'] === '本次规则范围');
  select.props.onChange({ target: { value: 'confirm-frozen-text' } });
  assert.equal(changed.compiler_mode, 'CONFIRM_FROZEN_TEXT');
  assert.equal(changed.audit_mode, 'CITATION_CATALOG');
  assert.equal(changed.rule_plan, null);
  tree = Panel(props({ draft: changed, onDraft: value => { changed = value; } }));
  elements(tree, 'select').find(item => item.props['aria-label'] === '本次规则范围').props.onChange({ target: { value: 'script-package/1.1' } });
  assert.deepEqual(changed, draft);
});

test('indexed audit requires the matching frozen-text mode and rejects unknown values', () => {
  const value = { ...draft, package_contract: 'script-package/1.2', compiler_mode: 'CONFIRM_FROZEN_TEXT', audit_mode: 'TARGET_SOURCE_INDEXES', rule_plan: { schema_version: 'script-package/1.2', title: draft.title, content_version: draft.content_version, player_count: draft.player_count } };
  assert.equal(canQueueAuthoringJob(bundle, value), true);
  assert.equal(canQueueAuthoringJob(bundle, { ...value, compiler_mode: undefined }), false);
  assert.equal(canQueueAuthoringJob(bundle, { ...value, audit_mode: 'UNKNOWN' }), false);
  const html = render({ job: { ...job, model_snapshot: { ...job.model_snapshot, schema_version: 'authoring-model/1.5' }, error_code: 'AUDIT_INDEXED_OUTPUT_INVALID' } });
  assert.match(html, /审核意见缺少有效的来源选择/);
  assert.match(html, /本任务固定规则和正文/);
});

test('frozen-rule queue uses its dedicated endpoint and preserves the entire explicit input', async () => {
  const previous = global.fetch;
  const request = { ...draft, package_contract: 'script-package/1.2', rule_plan: { schema_version: 'script-package/1.2' }, idempotency_key: 'plan-request', bundle_hash: bundle.bundle_hash };
  global.fetch = async (url, options) => {
    assert.equal(url, 'https://fixture.invalid/api/admin/fusion/authoring-jobs/with-rule-plan');
    assert.deepEqual(JSON.parse(options.body), request);
    assert.equal(options.cache, 'no-store');
    return { ok: true, json: async () => ({ data: job }) };
  };
  try { assert.deepEqual(await service.create(request), job); } finally { global.fetch = previous; }
});
const elements = (tree, type) => {
  if (Array.isArray(tree)) return tree.flatMap(child => elements(child, type));
  if (!React.isValidElement(tree)) return [];
  return [...(tree.type === type ? [tree] : []), ...elements(tree.props.children, type)];
};
const settle = () => new Promise(resolve => setImmediate(resolve));
const response = data => ({ ok: true, json: async () => ({ success: true, data }) });

test('investigation authoring is explicit and switching back restores the exact old request shape', () => {
  let changed;
  const select = state => elements(Panel(props({ ...state, onDraft(value) { changed = value; } })), 'select')
    .find(node => node.props['aria-label'] === '本次规则范围');
  assert.equal(select({}).props.value, 'script-package/1.1');
  select({}).props.onChange({ target: { value: 'script-package/1.2' } });
  assert.deepEqual(changed, { ...draft, package_contract: 'script-package/1.2' });
  assert.equal(canQueueAuthoringJob(bundle, changed), true);
  assert.equal(canQueueAuthoringJob(bundle, { ...draft, package_contract: 'invented-protocol' }), false);
  const oldAttempt = prepareAuthoringQueueAttempt(draft);
  assert.notEqual(prepareAuthoringQueueAttempt(changed, oldAttempt).key, oldAttempt.key);
  select({ draft: changed }).props.onChange({ target: { value: 'script-package/1.1' } });
  assert.deepEqual(changed, draft);
  changed = undefined;
  select({ busy: true }).props.onChange({ target: { value: 'script-package/1.2' } });
  assert.equal(changed, undefined);
});

test('rendering only shows metadata and never queues, cancels, recovers or loads bodies', () => {
  let calls = 0;
  const html = render({ onQueue() { calls++; }, onCancel() { calls++; }, onRecover() { calls++; }, onDraft() { calls++; } });
  assert.equal(calls, 0);
  assert.match(html, /新建只排队/);
  assert.match(html, /各最多 ¥0.05/);
  assert.match(html, /任务最多 ¥0.10/);
  assert.match(html, /正文不会自动填入表单/);
  assert.match(html, /尚未批准发布/);
  assert.doesNotMatch(html, /<iframe|<img|\/sources\//);
});

test('source selection excludes references, images, unlinked normalized text and oversized files', () => {
  for (const override of [{ kind: 'reference' }, { media_type: 'image/jpeg' }, { kind: 'revised' }, { kind: 'ocr' }, { size_bytes: 16 * 1024 + 1 }, { kind: 'unknown' }]) {
    assert.ok(authoringSourceUnavailable({ ...source, ...override }));
    assert.equal(canQueueAuthoringJob({ ...bundle, sources: [{ ...source, ...override }] }, draft), false);
  }
  for (const override of [{}, { kind: 'supplement' }, { kind: 'ocr', original_paths: ['fictional/original.jpg'] }, { kind: 'revised', original_paths: ['fictional/original.jpg'] }]) {
    assert.equal(authoringSourceUnavailable({ ...source, ...override }), '');
  }
});

test('queue validation binds selected IDs to the loaded bundle and enforces count and text budgets', () => {
  assert.equal(canQueueAuthoringJob(bundle, draft), true);
  for (const value of [{ ...draft, source_ids: [] }, { ...draft, source_ids: ['unknown'] }, { ...draft, source_ids: [source.id, source.id] }, { ...draft, player_count: 9 }, { ...draft, content_version: '../bad' }, { ...draft, title: '  ' }]) {
    assert.equal(canQueueAuthoringJob(bundle, value), false);
  }
  const sources = Array.from({ length: 21 }, (_, i) => ({ ...source, id: `source-${i}`, size_bytes: 50 }));
  assert.equal(canQueueAuthoringJob({ ...bundle, sources }, { ...draft, source_ids: sources.map(item => item.id) }), false);
  assert.equal(canQueueAuthoringJob({ ...bundle, sources: sources.slice(0, 4).map(item => ({ ...item, size_bytes: 16 * 1024 })) }, { ...draft, source_ids: sources.slice(0, 4).map(item => item.id) }), false);
});

test('checkbox carries only its observed source ID and disabled sources cannot be selected', () => {
  const blocked = { ...source, id: 'reference', kind: 'reference', relative_path: 'fictional/reference.md' };
  const calls = [];
  const tree = Panel(props({ bundle: { ...bundle, sources: [source, blocked] }, draft: { ...draft, source_ids: [] }, onDraft(value) { calls.push(value); } }));
  const checkboxes = elements(tree, 'input').filter(node => node.props.type === 'checkbox');
  checkboxes[0].props.onChange();
  checkboxes[1].props.onChange();
  assert.deepEqual(calls, [{ ...draft, source_ids: [source.id] }]);
  assert.equal(checkboxes[1].props.disabled, true);
});

test('queue form requires explicit submit and cannot run busy or with stale bundle metadata', () => {
  let calls = 0;
  for (const override of [{ busy: true }, { bundleHash: 'different' }, { sourceLoading: true }, { loading: true }, { draft: { ...draft, source_ids: [] } }]) {
    elements(Panel(props({ ...override, onQueue() { calls++; } })), 'form')[0].props.onSubmit({ preventDefault() {} });
  }
  assert.equal(calls, 0);
  elements(Panel(props({ onQueue() { calls++; } })), 'form')[0].props.onSubmit({ preventDefault() {} });
  assert.equal(calls, 1);
});

test('cancel and recovery controls follow observed states and never imply unknown requests are retried', () => {
  for (const state of ['BLOCKED', 'COMPLETED', 'CANCELLED', 'NEEDS_RECONCILIATION']) assert.equal(canCancelAuthoringJob({ ...job, state }), false);
  for (const state of ['QUEUED', 'BLOCKED', 'COMPLETED', 'CANCELLED']) assert.equal(canRecoverAuthoringJob({ ...job, state }), false);
  const unknown = { ...job, state: 'NEEDS_RECONCILIATION', revision: 7 };
  assert.equal(canRecoverAuthoringJob(unknown), true);
  const html = render({ job: unknown });
  assert.match(html, /不会重发结果未知的请求/);
  assert.doesNotMatch(html, /取消未完成任务/);
  const calls = [];
  const tree = Panel(props({ job: unknown, onRecover(value) { calls.push(value); } }));
  elements(tree, 'button').find(node => node.props.children === '仅恢复未发送步骤').props.onClick();
  assert.deepEqual(calls, [unknown]);
});

test('changing selected task hides prior details and all mutation controls disable during writes', () => {
  assert.doesNotMatch(render({ selectedJobId: '999' }), /记录版本 1/);
  let calls = 0;
  const tree = Panel(props({ busy: true, job: { ...job, state: 'RUNNING' }, onCancel() { calls++; }, onRecover() { calls++; } }));
  for (const action of elements(tree, 'button').filter(node => ['取消未完成任务', '仅恢复未发送步骤'].includes(node.props.children))) {
    assert.equal(action.props.disabled, true);
    action.props.onClick();
  }
  assert.equal(calls, 0);
});

test('compiler blockers and model audit findings render sources without exposing full packages or becoming approval', () => {
  const refs = [{ source_id: 'observed-source', anchor: 'L2-L4' }];
  const compiled = { id: 1, step: 'COMPILE', status: 'SUCCEEDED', prepared: { reservation: { cost_cny: '0.020' } }, receipt: { estimated_cost_cny: '0.012', charged_cost_cny: '0.010' }, output: { status: 'BLOCKED', package: { secret: 'PRIVATE_PACKAGE_SENTINEL' }, blockers: [{ code: 'SOURCE_GAP', message: '虚构来源缺口', sources: refs }] }, error_code: null };
  const audited = { id: 2, step: 'AUDIT', status: 'SUCCEEDED', prepared: {}, receipt: null, output: { schema_version: 'script-audit/1.0', summary: '虚构模型审核总结', coverage: ['PROVENANCE'], findings: [{ id: 'finding-a', severity: 'WARNING', category: 'PROVENANCE', target: { collection: 'knowledge', id: 'knowledge-a' }, message: '虚构引用问题', sources: refs }] }, error_code: null };
  const html = render({ job: { ...job, state: 'COMPLETED', candidate_version_id: 31, attempts: [compiled, audited] } });
  assert.match(html, /模型审核建议/);
  assert.match(html, /不代表人工批准/);
  assert.match(html, /虚构来源缺口/);
  assert.match(html, /虚构引用问题/);
  assert.match(html, /observed-source/);
  assert.match(html, /L2-L4/);
  assert.match(html, /href="\/admin\/script-reviews\?version_id=31"/);
  assert.match(html, /步骤估算：¥0.020/);
  assert.doesNotMatch(html, /PRIVATE_PACKAGE_SENTINEL/);
});

test('model text and file names are inert, and unknown error payloads remain hidden', () => {
  const evil = '<script>FICTIONAL_PAYLOAD()</script>';
  const html = render({ bundle: { ...bundle, sources: [{ ...source, relative_path: evil }] }, job: { ...job, title: evil, error_code: 'PRIVATE_ERROR_SENTINEL', attempts: [{ id: 1, step: 'COMPILE', status: 'FAILED', prepared: {}, receipt: null, error_code: null, output: { status: 'BLOCKED', blockers: [{ code: 'SOURCE_GAP', message: evil, sources: [{ source_id: evil, anchor: evil }] }] } }] } });
  assert.match(html, /&lt;script&gt;FICTIONAL_PAYLOAD/);
  assert.doesNotMatch(html, /<script>|PRIVATE_ERROR_SENTINEL/);
  assert.match(html, /break-all/);
});

test('failed receipt diagnostics identify only safe field positions and fixed explanations', () => {
  const attempt = { id: 1, step: 'COMPILE', status: 'FAILED', prepared: {}, output: null, error_code: 'COMPILER_TEXT_NOT_EXTRACTIVE', receipt: { output_diagnostics: [
    { code: 'TEXT_NOT_IN_MATERIALS', entity_path: '/knowledge/0/text' },
    { code: 'TEXT_OUTSIDE_REFERENCES', entity_path: '/characters/1/name' },
  ] } };
  const html = render({ job: { ...job, state: 'BLOCKED', attempts: [attempt] } });
  assert.match(html, /正文未原样出现/);
  assert.match(html, /正文不在引用范围/);
  assert.match(html, /\/knowledge\/0\/text/);
  assert.match(html, /\/characters\/1\/name/);
  assert.match(html, /aria-label="模型输出核验诊断"/);
  assert.match(html, /max-w-full/);
  assert.match(html, /break-all/);
});

test('input binding diagnostics expose only the six fixed metadata paths', () => {
  const paths = ['/title', '/schema_version', '/script_key', '/content_version', '/player_count', '/sources'];
  const attempt = { id: 1, step: 'COMPILE', status: 'FAILED', prepared: {}, output: null, error_code: 'COMPILER_INPUT_BINDING_MISMATCH', receipt: { output_diagnostics: [
    ...paths.map(entity_path => ({ code: 'INPUT_BINDING_MISMATCH', entity_path })),
    { code: 'INPUT_BINDING_MISMATCH', entity_path: '/knowledge/0/text' },
    { code: 'INPUT_BINDING_MISMATCH', entity_path: '/sources/PRIVATE_SOURCE_ID' },
  ] } };
  const html = render({ job: { ...job, state: 'BLOCKED', attempts: [attempt] } });
  assert.equal((html.match(/候选元数据与冻结输入不一致/g) || []).length, 6);
  for (const path of paths) assert.ok(html.includes(`>${path}</code>`));
  assert.doesNotMatch(html, /\/knowledge\/0\/text|PRIVATE_SOURCE_ID/);
});

test('diagnostics cap ten positions and reject arbitrary paths or provider error content', () => {
  const valid = Array.from({ length: 11 }, (_, index) => ({ code: 'TEXT_NOT_IN_MATERIALS', entity_path: `/knowledge/${index}/text` }));
  const attempt = { id: 1, step: 'COMPILE', status: 'FAILED', prepared: {}, output: null, error_code: null, receipt: { output_diagnostics: valid } };
  const html = render({ job: { ...job, attempts: [attempt] } });
  assert.match(html, /\/knowledge\/9\/text/);
  assert.doesNotMatch(html, /\/knowledge\/10\/text/);
  const unsafe = render({ job: { ...job, attempts: [{ ...attempt, receipt: { output_diagnostics: [
    { code: 'TEXT_NOT_IN_MATERIALS', entity_path: '<script>PRIVATE_PATH</script>' },
    { code: 'PRIVATE_PROVIDER_ERROR', entity_path: '/knowledge/0/text' },
    { code: 'TEXT_OUTSIDE_REFERENCES', entity_path: '/knowledge/private-id/text' },
    { code: 'TEXT_OUTSIDE_REFERENCES', entity_path: '/characters/8/name' },
  ] } }] } });
  assert.doesNotMatch(unsafe, /PRIVATE_PATH|PRIVATE_PROVIDER_ERROR|private-id|模型输出核验诊断|<script>/);
});

const renderDiagnostics = output_diagnostics => render({ job: { ...job, state: 'BLOCKED', error_code: 'COMPILER_PACKAGE_INVALID', attempts: [{
  id: 1, step: 'COMPILE', status: 'FAILED', prepared: {}, output: null, error_code: 'COMPILER_PACKAGE_INVALID', receipt: { output_diagnostics },
}] } });

test('package rule diagnostics give fixed Chinese reasons for allowed structural and rule positions', () => {
  const examples = [
    ['PACKAGE_SCHEMA_INVALID', '/', '候选包字段缺失、类型错误或包含不支持的字段值'],
    ['PACKAGE_DUPLICATE_ID', '/sources/499/id', '同类实体的标识重复'],
    ['PACKAGE_SOURCE_NOT_FOUND', '/knowledge/4999/sources/99', '引用的来源不在候选清单中'],
    ['PACKAGE_SOURCE_PAGE_OUT_OF_RANGE', '/truth/999/sources/99', '引用页码超出来源页数'],
    ['PACKAGE_SOURCE_LOCATION_MISSING', '/settlement/instructions/sources/99', '来源引用缺少页码或锚点'],
    ['PACKAGE_DUPLICATE_SOURCE_PATH', '/sources/499', '同一文件路径被重复声明为来源'],
    ['PACKAGE_ORIGINAL_SOURCE_MISSING', '/sources/0', '规范化来源缺少有效的原件关联'],
    ['PACKAGE_INVALID_SOURCE_LINK', '/sources/1', '原件不应再关联上游原件'],
    ['PACKAGE_CHARACTER_COUNT_MISMATCH', '/player_count', '角色数量与冻结人数不一致'],
    ['PACKAGE_PHASE_NOT_FOUND', '/phases/99/next_phase_id', '引用的阶段不存在'],
    ['PACKAGE_PHASE_CYCLE', '/phases', '阶段存在循环，当前契约仅支持无环的线性阶段'],
    ['PACKAGE_UNREACHABLE_PHASE', '/phases', '存在无法从初始阶段到达的阶段'],
    ['PACKAGE_INVALID_SETTLEMENT_PHASE', '/settlement/phase_id', '结算没有绑定最后阶段'],
    ['PACKAGE_DUPLICATE_REFERENCE', '/knowledge/4999/release', '结算或解锁条件存在重复引用'],
    ['PACKAGE_TRUTH_NOT_FOUND', '/settlement/truth_ids', '结算引用的后台真相不存在'],
    ['PACKAGE_INVALID_PUBLIC_SCOPE', '/knowledge/0', '公共材料绑定了私有角色或私有披露规则'],
    ['PACKAGE_INVALID_PRIVATE_SCOPE', '/evidence/4999', '私有材料缺少有效角色归属或使用了公开披露规则'],
    ['PACKAGE_EVIDENCE_NOT_FOUND', '/evidence/4999/release', '解锁条件引用的线索不存在'],
    ['PACKAGE_UNSATISFIABLE_RELEASE', '/knowledge/0/release', '禁止公开的线索被用作公开解锁条件'],
    ['PACKAGE_UNREACHABLE_EVIDENCE', '/evidence', '线索依赖存在循环或缺失引用，无法全部解锁'],
    ['PACKAGE_INITIAL_KNOWLEDGE_MISSING', '/characters/7', '角色缺少无前置线索条件的初始私有材料'],
  ];
  for (const [code, entity_path, explanation] of examples) {
    const html = renderDiagnostics([{ code, entity_path }]);
    assert.ok(html.includes(explanation), code);
    assert.ok(html.includes(`>${entity_path}</code>`), `${code}: ${entity_path}`);
    assert.match(html, /尚未批准发布/);
    assert.doesNotMatch(html, /<script>|PACKAGE_[A-Z_]+/);
  }
});

test('package diagnostic paths cover source references, release references and bounded collection positions', () => {
  const examples = [
    ['PACKAGE_DUPLICATE_ID', '/characters/7/id'], ['PACKAGE_DUPLICATE_ID', '/phases/99/id'],
    ['PACKAGE_DUPLICATE_ID', '/knowledge/4999/id'], ['PACKAGE_DUPLICATE_ID', '/evidence/4999/id'], ['PACKAGE_DUPLICATE_ID', '/truth/999/id'],
    ['PACKAGE_SOURCE_NOT_FOUND', '/introduction/sources/0'], ['PACKAGE_SOURCE_NOT_FOUND', '/characters/7/sources/99'],
    ['PACKAGE_SOURCE_NOT_FOUND', '/phases/99/sources/0'], ['PACKAGE_SOURCE_NOT_FOUND', '/evidence/4999/sources/99'],
    ['PACKAGE_PHASE_NOT_FOUND', '/initial_phase_id'], ['PACKAGE_PHASE_NOT_FOUND', '/knowledge/4999/release/phase_id'],
    ['PACKAGE_PHASE_NOT_FOUND', '/evidence/4999/release/phase_id'], ['PACKAGE_DUPLICATE_REFERENCE', '/settlement/truth_ids'],
  ];
  for (const [code, entity_path] of examples) assert.ok(renderDiagnostics([{ code, entity_path }]).includes(`>${entity_path}</code>`), entity_path);
});

test('package diagnostics reject mismatched reasons, over-limit indices, private fields and unknown codes', () => {
  const rejected = [
    { code: 'PACKAGE_DUPLICATE_ID', entity_path: '/sources/500/id' },
    { code: 'PACKAGE_DUPLICATE_ID', entity_path: '/characters/8/id' },
    { code: 'PACKAGE_DUPLICATE_ID', entity_path: '/phases/100/id' },
    { code: 'PACKAGE_DUPLICATE_ID', entity_path: '/knowledge/5000/id' },
    { code: 'PACKAGE_DUPLICATE_ID', entity_path: '/evidence/5000/id' },
    { code: 'PACKAGE_DUPLICATE_ID', entity_path: '/truth/1000/id' },
    { code: 'PACKAGE_SOURCE_NOT_FOUND', entity_path: '/introduction/sources/100' },
    { code: 'PACKAGE_SOURCE_NOT_FOUND', entity_path: '/knowledge/0/sources/100' },
    { code: 'PACKAGE_SOURCE_NOT_FOUND', entity_path: '/sources/0' },
    { code: 'PACKAGE_INITIAL_KNOWLEDGE_MISSING', entity_path: '/characters/07' },
    { code: 'PACKAGE_INITIAL_KNOWLEDGE_MISSING', entity_path: '/characters/-1' },
    { code: 'PACKAGE_INITIAL_KNOWLEDGE_MISSING', entity_path: '/characters/1\n' },
    { code: 'PACKAGE_INITIAL_KNOWLEDGE_MISSING', entity_path: '/characters/private_person' },
    { code: 'PACKAGE_PHASE_NOT_FOUND', entity_path: '/knowledge/0' },
    { code: 'PACKAGE_CHARACTER_COUNT_MISMATCH', entity_path: '/phases' },
    { code: 'PACKAGE_SCHEMA_INVALID', entity_path: '/title' },
    { code: 'PACKAGE_SCHEMA_INVALID', entity_path: '/private_field_sentinel' },
    { code: 'PACKAGE_SCHEMA_INVALID', entity_path: '/<script>PRIVATE_BODY_SENTINEL</script>' },
    { code: 'PACKAGE_SCHEMA_INVALID', entity_path: '/', message: 'PRIVATE_MESSAGE_SENTINEL' },
    { code: 'PRIVATE_UNKNOWN_CODE', entity_path: '/' },
    { code: 'constructor', entity_path: '/' },
  ];
  for (const item of rejected) {
    const html = renderDiagnostics([item]);
    assert.doesNotMatch(html, /模型输出核验诊断|PRIVATE_|private_person|private_field_sentinel|<script>/, JSON.stringify(item));
  }
});

test('malformed diagnostic containers remain hidden without preventing task details from rendering', () => {
  for (const items of [null, undefined, 'PRIVATE_RECEIPT_SENTINEL', {}, [null], [[]], [{ code: null, entity_path: '/' }], [{ code: 'PACKAGE_SCHEMA_INVALID', entity_path: {} }]]) {
    const html = renderDiagnostics(items);
    assert.match(html, /候选包未通过确定性校验/);
    assert.doesNotMatch(html, /模型输出核验诊断|PRIVATE_RECEIPT_SENTINEL/);
  }
});

test('invalid compiler draft has a fixed actionable explanation without exposing the raw error code', () => {
  const html = render({ job: { ...job, state: 'BLOCKED', error_code: 'COMPILER_DRAFT_INVALID', attempts: [{
    id: 1, step: 'COMPILE', status: 'FAILED', prepared: {}, output: null, error_code: 'COMPILER_DRAFT_INVALID', receipt: null,
  }] } });
  assert.match(html, /模型内容草稿未通过结构校验，请核对内容字段与阻断说明/);
  assert.doesNotMatch(html, /COMPILER_DRAFT_INVALID|模型输出核验诊断/);
});

const renderAuditDiagnostics = receipt => render({ job: { ...job, state: 'BLOCKED', step: 'AUDIT', candidate_version_id: 31, error_code: 'AUDIT_OUTPUT_INVALID', attempts: [{
  id: 2, step: 'AUDIT', status: 'FAILED', prepared: {}, output: null, error_code: 'AUDIT_OUTPUT_INVALID', receipt,
}] } });

test('audit failure diagnostics show fixed Chinese reasons while retaining the saved candidate and manual boundary', () => {
  const examples = [
    ['AUDIT_CANDIDATE_INVALID', '/', '模型审核所用候选未通过确定性校验'],
    ['AUDIT_COVERAGE_INVALID', '/coverage', '模型审核未完整、唯一覆盖规定的五个维度'],
    ['AUDIT_DUPLICATE_FINDING_ID', '/findings/199/id', '模型审核问题的标识重复'],
    ['AUDIT_TARGET_INVALID', '/findings/0/target', '模型审核问题的目标类型与标识不符合约定'],
    ['AUDIT_TARGET_NOT_FOUND', '/findings/199/target', '模型审核问题引用的候选实体不存在'],
    ['AUDIT_REFERENCE_MISMATCH', '/findings/199/sources/99', '模型审核问题的来源定位与目标实体声明不一致'],
    ['AUDIT_SCHEMA_INVALID', '/summary', '模型审核报告字段缺失、类型错误或包含不支持的字段值'],
  ];
  for (const [code, entity_path, explanation] of examples) {
    const html = renderAuditDiagnostics({ output_diagnostics: [{ code, entity_path }] });
    assert.ok(html.includes(explanation), code);
    assert.ok(html.includes(`>${entity_path}</code>`), entity_path);
    assert.match(html, /aria-label="模型输出核验诊断"/);
    assert.match(html, /href="\/admin\/script-reviews\?version_id=31"/);
    assert.match(html, /尚未批准发布/);
    assert.doesNotMatch(html, /AUDIT_[A-Z_]+|编译核验诊断|<script>/);
  }
});

test('audit schema diagnostics allow only known report, target and bounded reference field positions', () => {
  const paths = ['/', '/schema_version', '/summary', '/findings', '/findings/0', '/findings/199',
    ...['id', 'category', 'severity', 'target', 'message', 'sources'].map(field => `/findings/199/${field}`),
    '/findings/199/target/collection', '/findings/199/target/id', '/findings/199/sources/99',
    ...['source_id', 'page', 'anchor'].map(field => `/findings/199/sources/99/${field}`)];
  for (const entity_path of paths) {
    const html = renderAuditDiagnostics({ output_diagnostics: [{ code: 'AUDIT_SCHEMA_INVALID', entity_path }] });
    assert.ok(html.includes(`>${entity_path}</code>`), entity_path);
  }
});

test('audit diagnostics hide unknown fields, private payloads, mismatched reasons and out-of-bounds positions', () => {
  const rejected = [
    { code: 'AUDIT_CANDIDATE_INVALID', entity_path: '/findings' },
    { code: 'AUDIT_COVERAGE_INVALID', entity_path: '/' },
    { code: 'AUDIT_DUPLICATE_FINDING_ID', entity_path: '/findings/0/target' },
    { code: 'AUDIT_TARGET_INVALID', entity_path: '/findings/0/target/id' },
    { code: 'AUDIT_TARGET_NOT_FOUND', entity_path: '/findings/0/id' },
    { code: 'AUDIT_REFERENCE_MISMATCH', entity_path: '/findings/0/sources/0/anchor' },
    { code: 'AUDIT_REFERENCE_MISMATCH', entity_path: '/findings/199/sources/100' },
    { code: 'AUDIT_REFERENCE_MISMATCH', entity_path: '/findings/200/sources/0' },
    { code: 'AUDIT_SCHEMA_INVALID', entity_path: '/coverage' },
    { code: 'AUDIT_SCHEMA_INVALID', entity_path: '/findings/200' },
    { code: 'AUDIT_SCHEMA_INVALID', entity_path: '/findings/-1' },
    { code: 'AUDIT_SCHEMA_INVALID', entity_path: '/findings/0199' },
    { code: 'AUDIT_SCHEMA_INVALID', entity_path: '/findings/0\n' },
    { code: 'AUDIT_SCHEMA_INVALID', entity_path: '/findings/0/sources/100/page' },
    { code: 'AUDIT_SCHEMA_INVALID', entity_path: '/findings/0/sources/00/anchor' },
    { code: 'AUDIT_SCHEMA_INVALID', entity_path: '/findings/0/target/private_target' },
    { code: 'AUDIT_SCHEMA_INVALID', entity_path: '/findings/private_id/target' },
    { code: 'AUDIT_SCHEMA_INVALID', entity_path: '/findings/0/sources/0/private_source' },
    { code: 'AUDIT_SCHEMA_INVALID', entity_path: '/findings/0/message', input: 'PRIVATE_INPUT_SENTINEL' },
    { code: 'AUDIT_SCHEMA_INVALID', entity_path: '/<script>PRIVATE_TEXT_SENTINEL</script>' },
    { code: 'AUDIT_PRIVATE_UNKNOWN_CODE', entity_path: '/' },
  ];
  for (const item of rejected) {
    const html = renderAuditDiagnostics({ output_diagnostics: [item], provider_error: 'PRIVATE_PROVIDER_SENTINEL' });
    assert.match(html, /模型审核建议未通过结构或来源校验/);
    assert.doesNotMatch(html, /模型输出核验诊断|PRIVATE_|private_target|private_id|private_source|<script>/, JSON.stringify(item));
  }
});

test('audit receipts without recorded diagnostics do not invent causes and diagnostic lists remain bounded', () => {
  for (const receipt of [null, {}, { output_diagnostics: [] }]) {
    const html = renderAuditDiagnostics(receipt);
    assert.match(html, /模型审核建议未通过结构或来源校验/);
    assert.doesNotMatch(html, /模型输出核验诊断|目标类型与标识不符合|来源定位与目标实体声明不一致/);
  }
  const html = renderAuditDiagnostics({ output_diagnostics: Array.from({ length: 11 }, (_, index) => ({ code: 'AUDIT_TARGET_NOT_FOUND', entity_path: `/findings/${index}/target` })) });
  assert.equal((html.match(/模型审核问题引用的候选实体不存在/g) || []).length, 10);
  assert.ok(html.includes('>/findings/9/target</code>'));
  assert.ok(!html.includes('>/findings/10/target</code>'));
});

test('list is bounded to 100 entries and empty/search states are usable', () => {
  const jobs = Array.from({ length: 101 }, (_, index) => ({ ...job, id: index + 1, title: `任务${index + 1}` }));
  const html = render({ jobs, job: undefined, selectedJobId: '' });
  assert.match(html, /任务100/);
  assert.doesNotMatch(html, /任务101/);
  assert.match(render({ jobs: [] }), /还没有编译任务/);
  assert.match(render({ sourceQuery: 'unmatched' }), /没有匹配的材料/);
});

test('failed queue submission reuses its key until payload changes or prior attempt succeeds', () => {
  const payload = { ...draft, bundle_hash: bundle.bundle_hash };
  const first = prepareAuthoringQueueAttempt(payload);
  assert.strictEqual(prepareAuthoringQueueAttempt({ ...payload }, first), first);
  assert.notEqual(prepareAuthoringQueueAttempt({ ...payload, title: '修改后的虚构标题' }, first).key, first.key);
  assert.notEqual(prepareAuthoringQueueAttempt(payload).key, first.key);
});

test('queue service calls only the enqueue route with auth, JSON, no-store and abort signal', async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async (...args) => { calls.push(args); return response(job); };
  try {
    const controller = new AbortController();
    const request = { ...draft, bundle_hash: bundle.bundle_hash, idempotency_key: 'fictional-key' };
    assert.deepEqual(await service.create(request, controller.signal), job);
    assert.equal(calls.length, 1);
    const [url, options] = calls[0];
    assert.equal(url, 'https://fixture.invalid/api/admin/fusion/authoring-jobs');
    assert.equal(options.method, 'POST');
    assert.equal(options.cache, 'no-store');
    assert.equal(options.signal, controller.signal);
    assert.equal(options.headers.Authorization, 'Bearer fictional-authoring-token');
    assert.equal(options.headers['Content-Type'], 'application/json');
    assert.deepEqual(JSON.parse(options.body), request);
  } finally { global.fetch = originalFetch; }
});

test('cancel and recovery carry the exact observed revision', async () => {
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async (...args) => { calls.push(args); return response(job); };
  try {
    await service.cancel(12, 0);
    await service.recover(12, 8);
    assert.equal(calls[0][0], 'https://fixture.invalid/api/admin/fusion/authoring-jobs/12/cancel');
    assert.deepEqual(JSON.parse(calls[0][1].body), { expected_revision: 0 });
    assert.equal(calls[1][0], 'https://fixture.invalid/api/admin/fusion/authoring-jobs/12/recover');
    assert.deepEqual(JSON.parse(calls[1][1].body), { expected_revision: 8 });
  } finally { global.fetch = originalFetch; }
});

test('409 is actionable and never echoes private error response data', async () => {
  const originalFetch = global.fetch;
  global.fetch = async () => ({ ok: false, status: 409, json: async () => ({ detail: 'PRIVATE_SERVER_SENTINEL' }) });
  try {
    await assert.rejects(service.recover(12, 1), error => {
      assert.ok(error instanceof AuthoringJobError);
      assert.equal(error.status, 409);
      assert.match(error.message, /刷新/);
      assert.match(error.message, /未知请求不会重发/);
      assert.doesNotMatch(error.message, /PRIVATE_SERVER_SENTINEL/);
      return true;
    });
  } finally { global.fetch = originalFetch; }
});

test('active jobs poll every three seconds with reads only, then stop when complete', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const originalFetch = global.fetch;
  let current = { ...job, state: 'RUNNING' };
  const calls = [];
  const snapshots = [];
  global.fetch = async (url, options) => { calls.push({ url, options }); return response(url.endsWith('/12') ? current : [current]); };
  const stop = watchAuthoringJobs({ selectedId: 12, onJobs(values) { snapshots.push(values); }, onJob() {}, onError() {}, onReady() {} });
  try {
    await settle();
    assert.equal(calls.length, 2);
    t.mock.timers.tick(2999); await settle();
    assert.equal(calls.length, 2);
    current = { ...current, state: 'COMPLETED' };
    t.mock.timers.tick(1); await settle();
    assert.equal(calls.length, 4);
    assert.equal(snapshots.at(-1)[0].state, 'COMPLETED');
    t.mock.timers.tick(30000); await settle();
    assert.equal(calls.length, 4);
    assert.ok(calls.every(call => call.options.method === undefined && call.options.cache === 'no-store'));
  } finally { stop(); global.fetch = originalFetch; }
});

test('unmount stops scheduled polling and aborts requests', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const originalFetch = global.fetch;
  const calls = [];
  global.fetch = async (url, options) => { calls.push(options); return response([job]); };
  const stop = watchAuthoringJobs({ onJobs() {}, onJob() {}, onError() {}, onReady() {} });
  try {
    await settle();
    assert.equal(calls.length, 1);
    stop();
    assert.equal(calls[0].signal.aborted, true);
    t.mock.timers.tick(30000); await settle();
    assert.equal(calls.length, 1);
  } finally { stop(); global.fetch = originalFetch; }
});

test('a late response after task switch cannot overwrite the new workspace', async () => {
  const originalFetch = global.fetch;
  const pending = [];
  const observed = [];
  global.fetch = (url, options) => new Promise(resolve => pending.push({ url, options, resolve }));
  const stop = watchAuthoringJobs({ selectedId: 12, onJobs(value) { observed.push(value); }, onJob(value) { observed.push(value); }, onError(value) { observed.push(value); }, onReady() { observed.push('ready'); } });
  try {
    stop();
    for (const item of pending) item.resolve(response(item.url.endsWith('/12') ? job : [job]));
    await settle();
    assert.deepEqual(observed, []);
    assert.ok(pending.every(item => item.options.signal.aborted));
  } finally { stop(); global.fetch = originalFetch; }
});

test('reconciliation state is displayed without polling or resending unknown calls', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const originalFetch = global.fetch;
  let calls = 0;
  global.fetch = async () => { calls++; return response([{ ...job, state: 'NEEDS_RECONCILIATION' }]); };
  const stop = watchAuthoringJobs({ onJobs() {}, onJob() {}, onError() {}, onReady() {} });
  try {
    await settle();
    t.mock.timers.tick(30000); await settle();
    assert.equal(calls, 1);
  } finally { stop(); global.fetch = originalFetch; }
});

test('bounded mode remains explicit and incomplete audit cannot look approved', () => {
  const value = { ...draft, package_contract: 'script-package/1.2', compiler_mode: 'CONFIRM_FROZEN_TEXT', audit_mode: 'BOUNDED_TARGET_SOURCE_INDEXES', rule_plan: { ...draft, schema_version: 'script-package/1.2' } };
  assert.equal(canQueueAuthoringJob(bundle, value), true);
  assert.equal(canQueueAuthoringJob(bundle, { ...value, compiler_mode: undefined }), false);
  const html = render({ job: { ...job, state: 'BLOCKED', model_snapshot: { ...job.model_snapshot, schema_version: 'authoring-model/1.6' }, error_code: 'AUDIT_INCOMPLETE' } });
  assert.match(html, /尚未完成全部审核/);
  assert.match(html, /草案仍需人工审核/);
});

test('finish diagnostics use fixed descriptions and hide arbitrary provider text', () => {
  assert.match(renderAuditDiagnostics({ response_finish: 'length' }), /达到输出长度上限/);
  assert.match(renderAuditDiagnostics({ response_finish: 'content_filter' }), /供应商过滤/);
  assert.match(renderAuditDiagnostics({ response_finish: 'OTHER' }), /未识别的结束原因/);
  for (const response_finish of [undefined, null, 'PRIVATE_RAW_FINISH', { raw:'PRIVATE_RAW_FINISH' }]) {
    const html = renderAuditDiagnostics({ response_finish });
    assert.doesNotMatch(html, /PRIVATE_RAW_FINISH|达到输出长度上限|未识别的结束原因/);
  }
});

test('bounded report diagnostics expose safe field locations only', () => {
  const diagnostic = entity_path => ({ code:'AUDIT_BOUNDED_SCHEMA_INVALID', entity_path });
  const html = renderAuditDiagnostics({ output_diagnostics: ['/status','/findings/0/message','/findings/9/source_indexes/99'].map(diagnostic) });
  assert.match(html, /简短审核的字段/);
  assert.match(html, /\/status/);
  const unsafe = renderAuditDiagnostics({ output_diagnostics: ['/findings/10/message','/findings/0/PRIVATE_FIELD','/findings/0/source_indexes/100'].map(diagnostic) });
  assert.doesNotMatch(unsafe, /PRIVATE_FIELD|\/findings\/10|source_indexes\/100/);
});

test('direct schema mode queues only with frozen text and displays the new saved version', () => {
  const value = { ...draft, package_contract:'script-package/1.2', compiler_mode:'CONFIRM_FROZEN_TEXT', audit_mode:'DIRECT_BOUNDED_SOURCE_INDEXES', rule_plan:{ ...draft, schema_version:'script-package/1.2' } };
  assert.equal(canQueueAuthoringJob(bundle, value), true);
  assert.equal(canQueueAuthoringJob(bundle, { ...value, compiler_mode:undefined }), false);
  const html = render({ job:{ ...job, model_snapshot:{ ...job.model_snapshot, schema_version:'authoring-model/1.7' } } });
  assert.match(html, /草案仍需人工审核/);
});

test('strict schema mode requires the frozen draft and keeps manual approval boundary', () => {
  const value = { ...draft, package_contract:'script-package/1.2', compiler_mode:'CONFIRM_FROZEN_TEXT', audit_mode:'STRICT_BOUNDED_SOURCE_INDEXES', rule_plan:{ ...draft, schema_version:'script-package/1.2' } };
  assert.equal(canQueueAuthoringJob(bundle, value), true);
  assert.equal(canQueueAuthoringJob(bundle, { ...value, rule_plan:null }), false);
  assert.match(render({ job:{ ...job, model_snapshot:{ ...job.model_snapshot, schema_version:'authoring-model/1.8' } } }), /草案仍需人工审核/);
});

test('portable strict mode requires the complete frozen draft', () => {
  const value={ ...draft, package_contract:'script-package/1.2',compiler_mode:'CONFIRM_FROZEN_TEXT',audit_mode:'PORTABLE_STRICT_SOURCE_INDEXES',rule_plan:{ ...draft,schema_version:'script-package/1.2' } };
  assert.equal(canQueueAuthoringJob(bundle,value),true);
  assert.equal(canQueueAuthoringJob(bundle,{ ...value,compiler_mode:undefined }),false);
  assert.match(render({ job:{ ...job,model_snapshot:{ ...job.model_snapshot,schema_version:'authoring-model/1.9' } } }),/草案仍需人工审核/);
});

test('typed strict mode still requires frozen text and shows the manual review boundary', () => {
  const value={ ...draft, package_contract:'script-package/1.2',compiler_mode:'CONFIRM_FROZEN_TEXT',audit_mode:'TYPED_STRICT_SOURCE_INDEXES',rule_plan:{ ...draft,schema_version:'script-package/1.2' } };
  assert.equal(canQueueAuthoringJob(bundle,value),true);
  assert.equal(canQueueAuthoringJob(bundle,{ ...value,compiler_mode:undefined }),false);
  assert.match(render({ job:{ ...job,model_snapshot:{ ...job.model_snapshot,schema_version:'authoring-model/1.10' } } }),/草案仍需人工审核/);
});

test('runtime context mode is explicit and does not claim semantic approval', () => {
  const value = { ...draft, package_contract:'script-package/1.2', compiler_mode:'CONFIRM_FROZEN_TEXT', audit_mode:'RUNTIME_CONTEXT_SOURCE_INDEXES', rule_plan:{ ...draft, schema_version:'script-package/1.2' } };
  assert.equal(canQueueAuthoringJob(bundle, value), true);
  assert.equal(canQueueAuthoringJob(bundle, { ...value, compiler_mode:undefined }), false);
  assert.equal(canQueueAuthoringJob(bundle, { ...value, rule_plan:undefined }), false);
  const html = render({ job:{ ...job, model_snapshot:{ ...job.model_snapshot, schema_version:'authoring-model/1.11' } } });
  assert.match(html, /审核已提供当前程序规则供核对/);
  assert.match(html, /仍可能误判/);
  assert.match(html, /不会自动批准或发布/);
  assert.doesNotMatch(render({ job }), /审核已提供当前程序规则供核对/);
});

test('source-index diagnostics show safe positions and hide raw or unbounded values', () => {
  for (const entity_path of ['/findings/0/source_indexes/0', '/findings/199/source_indexes/99']) {
    const html = renderAuditDiagnostics({ output_diagnostics:[{ code:'AUDIT_SOURCE_INDEX_UNAVAILABLE', entity_path }] });
    assert.match(html, /模型选择的来源编号不在该审核目标的引用范围内/);
    assert.ok(html.includes(`>${entity_path}</code>`));
  }
  for (const entity_path of ['/findings/200/source_indexes/0', '/findings/0/source_indexes/100', '/findings/0/sources/0', '/findings/0/source_indexes/private-value']) {
    const html = renderAuditDiagnostics({ output_diagnostics:[{ code:'AUDIT_SOURCE_INDEX_UNAVAILABLE', entity_path }] });
    assert.doesNotMatch(html, /模型选择的来源编号不在该审核目标的引用范围内/);
    assert.ok(!html.includes(`>${entity_path}</code>`));
  }
});

test('citation catalog mode preserves frozen text and states unverified model quality', () => {
  const value = { ...draft, package_contract:'script-package/1.2', compiler_mode:'CONFIRM_FROZEN_TEXT', audit_mode:'CITATION_CATALOG', rule_plan:{ ...draft, schema_version:'script-package/1.2' } };
  assert.equal(canQueueAuthoringJob(bundle,value),true);
  assert.equal(canQueueAuthoringJob(bundle,{ ...value,compiler_mode:undefined }),false);
  assert.equal(canQueueAuthoringJob(bundle,{ ...value,rule_plan:undefined }),false);
  const html = render({ job:{ ...job,model_snapshot:{ ...job.model_snapshot,schema_version:'authoring-model/1.12' } } });
  assert.match(html,/审核只选择已有引用/);
  assert.match(html,/真实模型效果尚待验证/);
  assert.match(html,/仍需人工审核/);
  assert.doesNotMatch(render({ job }),/审核只选择已有引用/);
});

test('citation diagnostics reject unbounded positions and never echo arbitrary data', () => {
  for (const entity_path of ['/findings/0/citation_indexes','/findings/9/citation_indexes/99']) {
    const html = renderAuditDiagnostics({ output_diagnostics:[{ code:'AUDIT_CITATION_REFERENCE_INVALID',entity_path }] });
    assert.match(html,/模型选择了目录外引用/);
    assert.ok(html.includes(`>${entity_path}</code>`));
  }
  for (const entity_path of ['/findings/10/citation_indexes','/findings/0/citation_indexes/100','/findings/0/citation_indexes/private-value','/findings/00/citation_indexes']) {
    const html = renderAuditDiagnostics({ output_diagnostics:[{ code:'AUDIT_CITATION_REFERENCE_INVALID',entity_path }] });
    assert.doesNotMatch(html,/模型选择了目录外引用/);
    assert.ok(!html.includes(`>${entity_path}</code>`));
  }
  assert.match(renderAuditDiagnostics({ output_diagnostics:[{ code:'AUDIT_CITATION_SCHEMA_INVALID',entity_path:'/' }] }),/模型返回的引用审核格式/);
  assert.doesNotMatch(renderAuditDiagnostics({ output_diagnostics:[{ code:'AUDIT_CITATION_SCHEMA_INVALID',entity_path:'/private-value' }] }),/private-value|模型返回的引用审核格式/);
});
