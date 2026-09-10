const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const ts = require('typescript');

// Run the hand-written TSX with existing dependencies, without a browser,
// API calls, or a new test framework. Only the shared UI button is stubbed.
const sourcePath = path.join(__dirname, '../src/components/FusionEvidencePanel.tsx');
const { outputText } = ts.transpileModule(fs.readFileSync(sourcePath, 'utf8'), {
  fileName: sourcePath,
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2017 },
});
const compiledModule = { exports: {} };
const Button = ({ size, variant, ...props }) => React.createElement('button', props);
const componentRequire = name => name === '@/components/ui/button' ? { Button } : require(name);
new Function('require', 'module', 'exports', outputText)(componentRequire, compiledModule, compiledModule.exports);
const FusionEvidencePanel = compiledModule.exports.default;

const clue = (id, overrides = {}) => ({
  id, name: `测试线索 ${id}`, description: `线索正文 ${id}`, location: '测试地点', ...overrides,
});
const panel = (props = {}) => React.createElement(FusionEvidencePanel, {
  publicEvidence: [], privateEvidence: [], onReveal: () => {}, ...props,
});
const section = (html, name) => html.match(new RegExp(`<section aria-label="${name}">([\\s\\S]*?)</section>`))?.[1] || '';

function elementsOfType(element, type) {
  if (Array.isArray(element)) return element.flatMap(child => elementsOfType(child, type));
  if (!React.isValidElement(element)) return [];
  if (typeof element.type === 'function') return elementsOfType(element.type(element.props), type);
  return [
    ...(element.type === type ? [element] : []),
    ...elementsOfType(element.props.children, type),
  ];
}

test('renders public evidence from other participants with its body and location', () => {
  const html = renderToStaticMarkup(panel({ publicEvidence: [clue(1)] }));
  const publicSection = section(html, '公开线索');
  assert.match(publicSection, /测试线索 1/);
  assert.match(publicSection, /线索正文 1/);
  assert.match(publicSection, /发现地点：测试地点/);
  assert.match(publicSection, /已公开/);
  assert.doesNotMatch(publicSection, /公开这条线索/);
});

test('keeps undisclosed personal evidence separate from public evidence', () => {
  const html = renderToStaticMarkup(panel({ publicEvidence: [clue(1)], privateEvidence: [clue(2, { visibility: 'PRIVATE' })] }));
  assert.doesNotMatch(section(html, '公开线索'), /线索正文 2/);
  assert.doesNotMatch(section(html, '我的未公开线索'), /线索正文 1/);
  assert.match(section(html, '我的未公开线索'), /线索正文 2/);
  assert.match(section(html, '我的未公开线索'), /仅你可见/);
});

test('shows an owned shared clue once, using the public record', () => {
  const html = renderToStaticMarkup(panel({
    publicEvidence: [clue(1, { description: '已公开内容' })],
    privateEvidence: [clue(1, { visibility: 'PUBLIC', description: '旧内容' })],
  }));
  assert.equal((html.match(/测试线索 1/g) || []).length, 1);
  assert.match(section(html, '公开线索'), /已公开内容/);
  assert.doesNotMatch(html, /旧内容|公开这条线索/);
  assert.match(section(html, '我的未公开线索'), /暂无未公开线索/);
});

test('keeps a personal clue marked PUBLIC in the public section', () => {
  const html = renderToStaticMarkup(panel({ privateEvidence: [clue(1, { visibility: 'PUBLIC' })] }));
  assert.match(section(html, '公开线索'), /线索正文 1/);
  assert.doesNotMatch(section(html, '我的未公开线索'), /线索正文 1/);
  assert.doesNotMatch(html, /公开这条线索/);
});

test('does not automatically expand evidence or publish it during rendering', () => {
  let calls = 0;
  const element = panel({ publicEvidence: [clue(1)], privateEvidence: [clue(2)], onReveal: () => { calls += 1; } });
  const html = renderToStaticMarkup(element);
  assert.equal(elementsOfType(element, 'details').length, 2);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b/);
  assert.equal(calls, 0);
});

test('only the personal clue has a publish action, carrying the clicked clue ID', () => {
  const calls = [];
  const element = panel({ publicEvidence: [clue(1)], privateEvidence: [clue(2)], onReveal: id => calls.push(id) });
  const buttons = elementsOfType(element, 'button');
  assert.equal(buttons.length, 1);
  buttons[0].props.onClick();
  assert.deepEqual(calls, [2]);
});

test('disables publishing while an action is already running', () => {
  const buttons = elementsOfType(panel({ privateEvidence: [clue(2)], busy: true }), 'button');
  assert.equal(buttons[0].props.disabled, true);
});

test('shows distinct empty states', () => {
  const html = renderToStaticMarkup(panel());
  assert.match(section(html, '公开线索'), /尚无线索被公开/);
  assert.match(section(html, '我的未公开线索'), /暂无未公开线索/);
});

test('renders source text as plain text, not executable HTML', () => {
  const html = renderToStaticMarkup(panel({ publicEvidence: [clue(1, { description: '<script>secret()</script>' })] }));
  assert.match(html, /&lt;script&gt;secret\(\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
});
