const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const React = require('react');
const ts = require('typescript');
const { renderToStaticMarkup } = require('react-dom/server');
function load(hooks) {
  const fileName = path.join(__dirname, '../src/components/PlayClueDeck.tsx');
  const { outputText } = ts.transpileModule(fs.readFileSync(fileName, 'utf8'), { fileName, compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } });
  const mod = { exports: {} };
  new Function('require', 'module', 'exports', outputText)(name => name === 'react' ? { ...React, ...hooks } : name.endsWith('.module.css') ? { default: new Proxy({}, { get: (_, name) => name }) } : require(name), mod, mod.exports);
  return mod.exports.default;
}
const nodes = (tree, type) => Array.isArray(tree) ? tree.flatMap(x => nodes(x, type)) : React.isValidElement(tree) ? [...(tree.type === type ? [tree] : []), ...nodes(tree.props.children, type)] : [];
const items = ['A', 'B', 'C'].map(id => ({ id, heading: `虚构线索${id}`, content: React.createElement('p', null, `完整的已授权内容${id}`) }));
function harness() {
  let index = 0; const slots = [];
  const hooks = { useState: initial => { const slot = index++; if (!(slot in slots)) slots[slot] = initial; return [slots[slot], value => { slots[slot] = value; }]; }, useRef: initial => { const slot = index++; return slots[slot] ||= { current: initial }; } };
  const Deck = load(hooks);
  return () => { index = 0; return Deck({ label: '测试线索', items }); };
}
test('clue deck keeps all supplied material readable in source order without changing it', () => {
  const html = renderToStaticMarkup(React.createElement(load(), { label: '测试线索', items }));
  assert.equal((html.match(/<article /g) || []).length, 3);
  for (const item of items) assert.ok(html.includes(item.content.props.children));
  assert.ok(html.indexOf('完整的已授权内容A') < html.indexOf('完整的已授权内容B'));
  assert.equal(renderToStaticMarkup(React.createElement(load(), { label: '测试线索', items: [] })), '');
});
test('deck arrows and keyboard scroll only its horizontal rail, with bounded ends and native scroll synchronization', () => {
  const run = harness(); let tree = run(); const calls = [];
  const railNode = () => nodes(tree, 'div').find(x => x.props['aria-label'] === '测试线索左右滚动');
  const rail = { offsetLeft: 20, scrollLeft: 0, children: [0, 324, 648].map(offsetLeft => ({ offsetLeft })), scrollTo: value => { calls.push(value); rail.scrollLeft = value.left; } };
  railNode().props.ref.current = rail;
  const btn = suffix => nodes(tree, 'button').find(x => x.props['aria-label'] === `测试线索${suffix}`);
  assert.equal(btn('上一张').props.disabled, true);
  btn('下一张').props.onClick(); tree = run(); assert.deepEqual(calls.at(-1), { left: 324, behavior: 'auto' });
  let prevented = false; railNode().props.onKeyDown({ target: rail, currentTarget: rail, key: 'End', preventDefault() { prevented = true; } });
  tree = run(); assert.ok(prevented); assert.equal(btn('下一张').props.disabled, true); assert.equal(calls.at(-1).left, 648);
  const count = calls.length; railNode().props.onKeyDown({ target: {}, currentTarget: rail, key: 'ArrowLeft', preventDefault() { throw Error('must not intercept card content keys'); } }); assert.equal(calls.length, count);
  rail.scrollLeft = 0; railNode().props.onScroll(); tree = run(); assert.equal(btn('上一张').props.disabled, true);
});
