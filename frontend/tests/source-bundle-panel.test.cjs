const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const ts = require('typescript');

const filename = path.join(__dirname, '../src/components/SourceBundlePanel.tsx');
const { outputText } = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  fileName: filename, compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
});
const compiled = { exports: {} };
const image = ({ unoptimized, ...props }) => React.createElement('img', props);
const imports = name => name === 'next/image' ? { default: image } : require(name);
new Function('require', 'module', 'exports', outputText)(imports, compiled, compiled.exports);
const Panel = compiled.exports.default;
const entry = { id: 'source-a', relative_path: 'fictional/private.txt', kind: 'ocr', material_type: 'role', original_paths: ['fictional/original.jpg'], sha256: 'a'.repeat(64), size_bytes: 20, media_type: 'text/plain' };
const original = { ...entry, id: 'source-b', kind: 'original', relative_path: 'fictional/original.jpg', original_paths: [], media_type: 'image/jpeg' };
const bundle = { bundle_hash: 'b'.repeat(64), status: 'FROZEN', edition: '虚构修订版', file_count: 2, notes: ['编辑补充不得作为原件'], sources: [entry, original], publication_ready: false };
const props = overrides => ({ bundles: [bundle], bundle, selectedHash: bundle.bundle_hash, query: '', loading: false, verifying: false, previewLoading: false, error: '', onSelect() {}, onQuery() {}, onPreview() {}, onVerify() {}, onReload() {}, ...overrides });
const render = overrides => renderToStaticMarkup(React.createElement(Panel, props(overrides)));
const buttons = tree => {
  if (Array.isArray(tree)) return tree.flatMap(buttons);
  if (!React.isValidElement(tree)) return [];
  return [...(tree.type === 'button' ? [tree] : []), ...buttons(tree.props.children)];
};

test('lists source boundaries without automatically loading bodies', () => {
  let calls = 0;
  const html = render({ onPreview() { calls += 1; } });
  assert.match(html, /仅管理员可见|仅管理员/);
  assert.match(html, /编辑补充不得作为原件/);
  assert.match(html, /选择一份材料查看/);
  assert.equal(calls, 0);
});
test('renders source text as inert text including HTML and Markdown links', () => {
  const html = render({ preview: { sourceId: entry.id, text: '<script>PRIVATE()</script> [link](https://example.invalid)' } });
  assert.match(html, /&lt;script&gt;PRIVATE/);
  assert.doesNotMatch(html, /<script>|href="https:\/\/example/);
});
test('clicking source and original uses explicit source IDs', () => {
  const calls = [];
  const nodes = buttons(Panel(props({ preview: { sourceId: entry.id }, onPreview(source) { calls.push(source.id); } })));
  const originalButton = nodes.find(node => node.props.children === original.relative_path);
  assert.ok(originalButton);
  originalButton.props.onClick();
  assert.deepEqual(calls, [original.id]);
});
test('file checks never present a publication approval', () => {
  const html = render({ report: { bundle_hash: bundle.bundle_hash, valid: true, checked_files: 2, issues: [], report_hash: 'c'.repeat(64) } });
  assert.match(html, /文件一致性检查通过/);
  assert.match(html, /尚未批准发布/);
  assert.doesNotMatch(html, /<button[^>]*>发布/);
});
test('a report for another bundle is never displayed', () => {
  assert.doesNotMatch(render({ report: { bundle_hash: 'other', valid: true, checked_files: 2, issues: [] } }), /文件一致性检查通过/);
});
test('verification cannot run while loading or already checking', () => {
  for (const values of [{ loading: true }, { verifying: true }, { bundle: undefined }]) {
    const action = buttons(Panel(props(values))).find(node => typeof node.props.children === 'string' && /检查文件|正在检查/.test(node.props.children));
    assert.equal(action.props.disabled, true);
  }
});
test('search and empty/error states are visible', () => {
  assert.match(render({ query: 'nonexistent' }), /没有匹配的文件/);
  assert.match(render({ bundles: [], bundle: undefined }), /还没有保存的来源快照/);
  assert.match(render({ error: '无法读取' }), /role="alert"/);
});

test('failed verification identifies the file and opens only that source', () => {
  const report = { bundle_hash: bundle.bundle_hash, valid: false, checked_files: 1, issues: [{ code: 'SOURCE_FILE_INVALID', source_id: entry.id }] };
  const html = render({ report });
  assert.match(html, /待处理问题/);
  assert.match(html, /文件缺失、发生变化或格式不符合要求/);
  const calls = [];
  const action = buttons(Panel(props({ report, onPreview(source) { calls.push(source.id); } }))).find(node => node.props.children === entry.relative_path);
  assert.ok(action);
  action.props.onClick();
  assert.deepEqual(calls, [entry.id]);
});

test('unrecognized failure codes never render arbitrary error text as HTML', () => {
  const html = render({ report: { bundle_hash: bundle.bundle_hash, valid: false, checked_files: 0, issues: [{ code: '<script>PRIVATE_FAILURE</script>', source_id: '<b>unknown</b>' }] } });
  assert.match(html, /来源检查未通过/);
  assert.match(html, /&lt;b&gt;unknown/);
  assert.doesNotMatch(html, /PRIVATE_FAILURE|<b>unknown/);
});
