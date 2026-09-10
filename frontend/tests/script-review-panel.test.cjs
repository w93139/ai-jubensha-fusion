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
const { default: Panel, canSubmitDisposition, auditSchemaForPackage, auditExampleForPackage } = compile('../src/components/ScriptReviewPanel.tsx', name => name === 'next/link'
  ? { default: ({ children, ...props }) => React.createElement('a', props, children) }
  : require(name));
const { default: service, prepareReviewAttempt, ScriptReviewError } = compile('../src/services/scriptReviewService.ts', name => {
  if (name === '@/stores/configStore') return { config: { api: { baseUrl: 'https://fixture.invalid' } } };
  if (name === '@/services/authService') return { default: { getToken: () => 'fictional-test-token' } };
  return require(name);
});

const candidate = { id: 17, script_key: 'fictional', content_version: '0.1', title: '虚构候选', package_hash: 'a'.repeat(64), contract_version: 'script-package/1.0' };
const finding = { id: 'finding-a', category: 'PROVENANCE', severity: 'BLOCKER', target: { collection: 'introduction', id: null }, message: '虚构来源问题', sources: [{ source_id: 'source-a', anchor: 'L1' }] };
const audit = { id: 23, audit_hash: 'c'.repeat(64), version_id: candidate.id, package_hash: candidate.package_hash, bundle_hash: 'b'.repeat(64), source_report_hash: 'd'.repeat(64), submitted_by: 1, report: { schema_version: 'script-audit/1.0', summary: '虚构人工核对记录', coverage: ['PROVENANCE'], findings: [finding] }, revision: 4, dispositions: [], open_blockers: 1, open_warnings: 0, publication_ready: false };
const bundle = { bundle_hash: audit.bundle_hash, script_key: candidate.script_key, status: 'FROZEN', edition: '虚构来源版', file_count: 1 };
const props = overrides => ({ candidates: [candidate], selectedId: String(candidate.id), bundles: [bundle], bundleHash: bundle.bundle_hash, review: { candidate, audits: [audit] }, reportText: '', drafts: {}, loading: false, busy: false, error: '', notice: '', onSelect() {}, onBundle() {}, onReport() {}, onReload() {}, onSubmitReport() {}, onDraft() {}, onSubmitDisposition() {}, ...overrides });
const render = overrides => renderToStaticMarkup(React.createElement(Panel, props(overrides)));
const elements = (tree, type) => {
  if (Array.isArray(tree)) return tree.flatMap(child => elements(child, type));
  if (!React.isValidElement(tree)) return [];
  return [...(tree.type === type ? [tree] : []), ...elements(tree.props.children, type)];
};

test('investigation audit example uses its own report version and preserves old examples', () => {
  const previous = auditExampleForPackage('script-package/1.1');
  const current = auditExampleForPackage('script-package/1.2');
  assert.equal(auditSchemaForPackage('script-package/1.0'), 'script-audit/1.0');
  assert.equal(previous.schema_version, 'script-audit/1.0');
  assert.equal(current.schema_version, 'script-audit/1.1');
  assert.deepEqual(auditExampleForPackage('script-package/1.1'), previous);
  const newer = { ...candidate, contract_version: 'script-package/1.2' };
  const html = render({ candidates: [newer], review: { candidate: newer, audits: [], references: [
    { target: { collection: 'mechanics.actions', id: 'search-desk' }, sources: finding.sources },
  ] } });
  assert.match(html, /script-audit\/1.1/);
  assert.match(html, /共享行动点和调查前置条件/);
  assert.match(html, /mechanics.actions/);
  assert.doesNotMatch(html, /script-audit\/1.0/);
});

test('memory candidate requires the new human report version and exposes sourced memory targets', () => {
  const current = { ...candidate, contract_version: 'script-package/1.3' };
  assert.equal(auditSchemaForPackage(current.contract_version), 'script-audit/1.2');
  const html = render({ candidates: [current], review: { candidate: current, audits: [], references: [
    { target: { collection: 'memories', id: 'recall-a' }, sources: finding.sources },
  ] } });
  assert.match(html, /script-audit\/1.2/); assert.match(html, /memories/);
  assert.doesNotMatch(html, /script-audit\/1.0/);
});

test('shows manual boundaries and fictional example without automatic writes', () => {
  let calls = 0;
  const html = render({ onSubmitReport() { calls++; }, onSubmitDisposition() { calls++; } });
  assert.equal(calls, 0);
  assert.match(html, /未运行自动 Audit/);
  assert.match(html, /尚未批准发布/);
  assert.match(html, /完全虚构/);
  assert.doesNotMatch(html, /<button[^>]*>发布/);
  const textarea = elements(Panel(props()), 'textarea').find(node => node.props.id === 'audit-report');
  assert.equal(textarea.props.value, '');
});

test('renders report, locations and notes as inert text', () => {
  const evil = '<script>FICTIONAL()</script> [link](https://example.invalid)';
  const item = { ...audit, report: { ...audit.report, summary: evil, findings: [{ ...finding, message: evil, sources: [{ source_id: evil, anchor: evil }] }] }, dispositions: [{ id: 1, revision: 1, finding_id: finding.id, status: 'OPEN', note: evil }] };
  const html = render({ review: { candidate, audits: [item] } });
  assert.match(html, /&lt;script&gt;FICTIONAL/);
  assert.doesNotMatch(html, /<script>|href="https:\/\/example/);
});

test('reports belonging to another candidate or package are hidden', () => {
  assert.doesNotMatch(render({ review: { candidate: { ...candidate, id: 99 }, audits: [audit] } }), /虚构人工核对记录/);
  assert.doesNotMatch(render({ review: { candidate, audits: [{ ...audit, package_hash: 'other' }] } }), /虚构人工核对记录/);
});

test('report submission requires loaded review, same-script source and explicit content', () => {
  for (const override of [{}, { reportText: '{}', review: undefined }, { reportText: '{}', loading: true }, { reportText: '{}', busy: true }, { reportText: '{}', bundleHash: 'foreign' }]) {
    let calls = 0;
    const tree = Panel(props({ ...override, onSubmitReport() { calls++; } }));
    const form = elements(tree, 'form').find(node => !node.props['aria-label']);
    form.props.onSubmit({ preventDefault() {} });
    assert.equal(calls, 0);
  }
  let calls = 0;
  const tree = Panel(props({ reportText: JSON.stringify(audit.report), onSubmitReport() { calls++; } }));
  elements(tree, 'form').find(node => !node.props['aria-label']).props.onSubmit({ preventDefault() {} });
  assert.equal(calls, 1);
});

test('blocker cannot be acknowledged and every handling action needs a note', () => {
  assert.equal(canSubmitDisposition(finding, { status: 'ACKNOWLEDGED', note: '虚构知悉说明' }), false);
  assert.equal(canSubmitDisposition(finding, { status: 'DISMISSED', note: '  ' }), false);
  assert.equal(canSubmitDisposition(finding, { status: 'DISMISSED', note: '虚构误报依据' }), true);
  assert.equal(canSubmitDisposition({ ...finding, severity: 'WARNING' }, { status: 'ACKNOWLEDGED', note: '虚构知悉说明' }), true);
  const select = elements(Panel(props()), 'select').find(node => node.props['aria-label'] === `问题 ${finding.id} 的处理结果`);
  assert.equal(elements(select, 'option').find(node => node.props.value === 'ACKNOWLEDGED').props.disabled, true);
});

test('saving a disposition forwards exactly the observed audit, finding and note', () => {
  const draft = { status: 'DISMISSED', note: '虚构材料对照后的误报依据' };
  const calls = [];
  const tree = Panel(props({ drafts: { '23:finding-a': draft }, onSubmitDisposition(...args) { calls.push(args); } }));
  const form = elements(tree, 'form').find(node => node.props['aria-label'] === `处理问题 ${finding.id}`);
  form.props.onSubmit({ preventDefault() {} });
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], [audit, finding, draft]);
});

test('stale dispositions are not shown as the latest state', () => {
  const item = { ...audit, dispositions: [
    { id: 2, revision: 3, finding_id: finding.id, status: 'DISMISSED', note: '最新误报说明', submitted_by: 12 },
    { id: 1, revision: 1, finding_id: finding.id, status: 'OPEN', note: '旧说明', submitted_by: 11 },
    { id: 3, revision: 4, finding_id: 'unrelated', status: 'OPEN', note: '无关说明', submitted_by: 13 },
  ] };
  const html = render({ review: { candidate, audits: [item] } });
  assert.match(html, /最新状态：判定误报/);
  assert.match(html, /最新误报说明/);
  assert.doesNotMatch(html, /无关说明/);
  const tree = Panel(props({ review: { candidate, audits: [item] } }));
  const history = elements(tree, 'ol').find(node => node.props['aria-label'] === `问题 ${finding.id} 的处理历史`);
  const historyHtml = renderToStaticMarkup(history);
  assert.match(historyHtml, /旧说明/);
  assert.match(historyHtml, /提交人 #11/);
  assert.match(historyHtml, /提交人 #12/);
  assert.ok(historyHtml.indexOf('处理版本 1') < historyHtml.indexOf('处理版本 3'));
});

test('candidate metadata exposes observed identifiers and anchors without reading bodies', () => {
  const metadata = {
    references: [{ target: { collection: 'knowledge', id: 'observed-knowledge-id' }, sources: [{ source_id: 'observed-source-id', anchor: 'L3-L5' }] }],
    source_files: [{ id: 'observed-source-id', relative_path: 'fictional/role.txt', kind: 'ocr' }],
  };
  const html = render({ review: { candidate, audits: [], ...metadata } });
  assert.match(html, /候选实体与来源标识/);
  assert.match(html, /observed-knowledge-id/);
  assert.match(html, /observed-source-id/);
  assert.match(html, /L3-L5/);
  assert.match(html, /fictional\/role.txt/);
  assert.match(html, /href="\/admin\/source-bundles"/);
  assert.doesNotMatch(html, /<iframe|<img|\/sources\//);
});

test('candidate metadata and disposition history escape untrusted text', () => {
  const evil = '<script>FICTIONAL_HISTORY()</script>';
  const item = { ...audit, dispositions: [{ id: 1, revision: 1, finding_id: finding.id, status: 'OPEN', note: evil, submitted_by: 11 }] };
  const html = render({ review: { candidate, audits: [item], references: [{ target: { collection: 'knowledge', id: evil }, sources: [{ source_id: evil, anchor: evil }] }], source_files: [{ id: evil, relative_path: evil, kind: 'original' }] } });
  assert.match(html, /&lt;script&gt;FICTIONAL_HISTORY/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /max-h-80 max-w-full overflow-auto/);
});

test('busy state prevents duplicate writes even through form callback', () => {
  let calls = 0;
  const tree = Panel(props({ busy: true, drafts: { '23:finding-a': { status: 'OPEN', note: '虚构说明' } }, onSubmitDisposition() { calls++; } }));
  elements(tree, 'form').find(node => node.props['aria-label']).props.onSubmit({ preventDefault() {} });
  assert.equal(calls, 0);
});

test('empty and refresh-conflict states offer actionable feedback', () => {
  assert.match(render({ candidates: [], selectedId: '', review: undefined }), /还没有候选剧本包/);
  assert.match(render({ error: '记录已变化，请刷新记录。' }), /role="alert"/);
  assert.match(render({ error: '记录已变化，请刷新记录。' }), /刷新记录/);
});

test('same failed payload reuses its key; edited payload or completed attempt gets a new key', () => {
  const payload = { expected_revision: 4, finding_id: finding.id, note: '虚构说明' };
  const first = prepareReviewAttempt(payload);
  assert.strictEqual(prepareReviewAttempt({ ...payload }, first), first);
  assert.notEqual(prepareReviewAttempt({ ...payload, note: '新的虚构说明' }, first).key, first.key);
  assert.notEqual(prepareReviewAttempt({ ...payload, expected_revision: 5 }, first).key, first.key);
  assert.notEqual(prepareReviewAttempt(payload).key, first.key);
});

test('service sends exact version preconditions with authentication, cancellation and no-store', async () => {
  const originalFetch = global.fetch;
  let observed;
  global.fetch = async (...args) => { observed = args; return { ok: true, json: async () => ({ success: true, data: audit }) }; };
  try {
    const controller = new AbortController();
    const body = { idempotency_key: 'fictional-key', expected_package_hash: candidate.package_hash, expected_audit_hash: audit.audit_hash, expected_revision: 4, finding_id: finding.id, status: 'DISMISSED', note: '虚构误报依据' };
    assert.deepEqual(await service.submitDisposition(audit.id, body, controller.signal), audit);
    assert.equal(observed[0], 'https://fixture.invalid/api/admin/fusion/script-audits/23/dispositions');
    assert.equal(observed[1].headers.Authorization, 'Bearer fictional-test-token');
    assert.equal(observed[1].headers['Content-Type'], 'application/json');
    assert.equal(observed[1].cache, 'no-store');
    assert.equal(observed[1].signal, controller.signal);
    assert.deepEqual(JSON.parse(observed[1].body), body);
  } finally { global.fetch = originalFetch; }
});

test('service conflict messages never echo arbitrary private backend errors', async () => {
  const originalFetch = global.fetch;
  global.fetch = async () => ({ ok: false, status: 409, json: async () => ({ detail: 'PRIVATE_ERROR_SENTINEL' }) });
  try {
    await assert.rejects(service.review(candidate.id), error => {
      assert.ok(error instanceof ScriptReviewError);
      assert.equal(error.status, 409);
      assert.match(error.message, /刷新记录/);
      assert.doesNotMatch(error.message, /PRIVATE_ERROR_SENTINEL/);
      return true;
    });
  } finally { global.fetch = originalFetch; }
});

const { RuleReviewContent, matchesRuleReview } = compile('../src/components/RuleReviewPanel.tsx', name =>
  name === '@/services/scriptReviewService' ? { default: service } : require(name));
const ruleReport = () => ({
  schema_version: 'rule-review/1.0', package_hash: candidate.package_hash, bundle_hash: bundle.bundle_hash,
  semantic_status: 'UNREVIEWED', publication_ready: false, row_count: 1, offset: 0, limit: 20, next_offset: null,
  rows: [{ target: { collection: 'evidence', id: 'fictional-seal' }, label: 'fictional-seal',
    rules: { visibility: 'CHARACTER_PRIVATE', character_id: 'b', disclosure: 'MAY_SHARE',
      release: { phase_id: 'first', required_action_ids: ['open-box'], required_public_evidence_ids: ['key'] } },
    source_count: 1, sources_truncated: false,
    sources: [{ source_id: 'fictional-source', kind: 'original', anchor: 'Evidence', page: null,
      status: 'AVAILABLE', text: 'Independent fictional source declares no extra public-evidence prerequisite.', truncated: false }],
    notice_count: 1, notices_truncated: false,
    notices: [{ code: 'PUBLIC_PREREQUISITE_ALREADY_REQUIRED_BY_ACTION', action_id: 'open-box', evidence_id: 'key' }],
  }],
});

test('rule comparison shows direct material conditions beside its exact source without declaring approval', () => {
  const html = renderToStaticMarkup(React.createElement(RuleReviewContent, { report: ruleReport() }));
  assert.match(html, /可能重复添加条件/);
  assert.match(html, /不要自动删除/);
  assert.match(html, /必须已完成的动作/);
  assert.match(html, /必须已公开的证据/);
  assert.match(html, /Independent fictional source/);
  assert.match(html, /尚未完成语义审核/);
  assert.doesNotMatch(html, /<button|审核通过|自动修复/);
});

test('rule excerpts remain inert text and explicitly show truncation and unavailable sources', () => {
  const report = ruleReport();
  report.rows[0].sources[0].text = '<script>private()</script> [link](https://example.invalid)';
  report.rows[0].sources[0].truncated = true;
  report.rows[0].source_count = 4; report.rows[0].sources_truncated = true;
  let html = renderToStaticMarkup(React.createElement(RuleReviewContent, { report }));
  assert.match(html, /&lt;script&gt;private/);
  assert.doesNotMatch(html, /<script>|href="https:/);
  assert.match(html, /仅显示开头/); assert.match(html, /共 4 处引用/);
  for (const status of ['NON_TEXT', 'TEXT_LIMIT', 'LOCATION_UNAVAILABLE']) {
    report.rows[0].sources[0] = { ...report.rows[0].sources[0], status, text: null, truncated: false };
    html = renderToStaticMarkup(React.createElement(RuleReviewContent, { report }));
    assert.match(html, /来源材料页核对/); assert.doesNotMatch(html, /private\(\)/);
  }
});

test('rule result binding rejects stale candidate, changed bundle and invalid or approving payloads', () => {
  const report = ruleReport();
  assert.equal(matchesRuleReview(report, candidate.package_hash, bundle.bundle_hash), true);
  assert.equal(matchesRuleReview(report, 'other-candidate', bundle.bundle_hash), false);
  assert.equal(matchesRuleReview(report, candidate.package_hash, 'other-bundle'), false);
  for (const change of [{ schema_version: 'unknown' }, { semantic_status: 'APPROVED' }, { publication_ready: true },
    { next_offset: 0 }, { rows: [null] }, { rows: [{ ...report.rows[0], sources: null }] },
    { rows: [{ ...report.rows[0], notices: [{ code: 'UNKNOWN' }] }] }]) {
    assert.equal(matchesRuleReview({ ...report, ...change }, candidate.package_hash, bundle.bundle_hash), false);
  }
});

test('rule comparison GET binds both hashes and pagination and forwards cancellation without writes', async () => {
  const previous = global.fetch;
  const controller = new AbortController();
  let calls = 0;
  global.fetch = async (url, options) => {
    calls++;
    const parsed = new URL(url);
    assert.equal(parsed.pathname, '/api/admin/fusion/script-packages/17/rule-review');
    assert.equal(parsed.searchParams.get('expected_package_hash'), candidate.package_hash);
    assert.equal(parsed.searchParams.get('bundle_hash'), bundle.bundle_hash);
    assert.equal(parsed.searchParams.get('offset'), '20');
    assert.equal(parsed.searchParams.get('limit'), '20');
    assert.equal(options.cache, 'no-store'); assert.equal(options.signal, controller.signal);
    assert.equal(options.method, undefined); assert.equal(options.body, undefined);
    return { ok: true, json: async () => ({ data: ruleReport() }) };
  };
  try { await service.rules(17, candidate.package_hash, bundle.bundle_hash, 20, controller.signal); }
  finally { global.fetch = previous; }
  assert.equal(calls, 1);
});

const runtimeFacts = { package_contract:'script-package/1.2',human_players:1,investigation_actor:'SELECTED_HUMAN_ONLY',action_success_limit:'ONCE_PER_SESSION',phase_budget:'SHARED_NO_CARRY',material_recipient:'DECLARED_OWNER' };
test('rule review explains fixed runtime facts and actual settlement references without approving', () => {
  const base=ruleReport();
  const report={ ...base,schema_version:'rule-review/1.1',runtime_facts:runtimeFacts,rows:[{ ...base.rows[0],target:{ collection:'settlement',id:null },label:'本版结尾',rules:{ phase_id:'end',truth_ids:['truth-existing'] } }] };
  assert.equal(matchesRuleReview(report,candidate.package_hash,bundle.bundle_hash),true);
  const html=renderToStaticMarkup(React.createElement(RuleReviewContent,{ report }));
  assert.match(html,/只有一名真人/); assert.match(html,/整局最多成功一次/);
  assert.match(html,/结尾引用的真相编号/); assert.match(html,/truth-existing/);
  assert.match(html,/不是 AI 审核结论/); assert.doesNotMatch(html,/<button|审核通过/);
});
test('runtime review facts reject forged capabilities and preserve legacy response handling', () => {
  const report={ ...ruleReport(),schema_version:'rule-review/1.1',runtime_facts:runtimeFacts };
  for (const change of [undefined,{ ...runtimeFacts,human_players:2 },{ ...runtimeFacts,investigation_actor:'AI' },{ ...runtimeFacts,approved:true }]) {
    assert.equal(matchesRuleReview({ ...report,runtime_facts:change },candidate.package_hash,bundle.bundle_hash),false);
  }
  assert.equal(matchesRuleReview(ruleReport(),candidate.package_hash,bundle.bundle_hash),true);
  assert.equal(matchesRuleReview({ ...report,schema_version:'rule-review/1.0' },candidate.package_hash,bundle.bundle_hash),false);
});
