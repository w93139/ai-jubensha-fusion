const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
function load(file, hooks) {
  const fileName = path.join(__dirname, '../src', file);
  const code = ts.transpileModule(fs.readFileSync(fileName, 'utf8'), { fileName, compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText;
  const mod = { exports: {} };
  new Function('require', 'module', 'exports', code)(name => name === 'react' ? { ...React, ...hooks }
    : name === './PlayText' ? load('components/PlayText.tsx', hooks)
      : name.startsWith('@/lib/') ? load(`lib/${name.slice(6)}.ts`, hooks) : require(name), mod, mod.exports);
  return mod.exports;
}
const { performanceReadingBlocks: group } = load('lib/playPerformance.ts');
const material = (id, text, changes = {}) => ({ id, text, kind: 'CLAIM', disclosure: 'KEEP_PRIVATE', can_share: false, ...changes });
const fixture = (count = 5) => [material('before', '这是虚构背景。'), material('heading', '你的表现'), material('intro', '以下表现是本阶段的角色要求。'),
  ...Array.from({ length: count }, (_, i) => material(`item-${i + 1}`, `${i + 1}${i === 1 ? '．' : '、'}虚构事项${i + 1}，\n\n完整续行${i + 1}。`, i === 0 ? { retelling: 'MUST_RETELL' } : i === 1 ? { retelling: 'MAY_RETELL' } : {})),
  material('after', '你的目的\n1、另一节的任务。')];
const Summary = load('components/PlayPerformanceSummary.tsx').default;

test('authorized performance groups retain source objects, full multiline text and order for all role counts', () => {
  for (const count of [3, 4, 5, 6, 7]) {
    const input = fixture(count), before = JSON.stringify(input), blocks = group(input);
    assert.equal(blocks.length, 3); assert.equal(blocks[0].material, input[0]); assert.equal(blocks[2].material, input.at(-1));
    assert.equal(blocks[1].items.length, count); assert.equal(blocks[1].introduction, input[2]);
    assert.deepEqual(blocks[1].items, input.slice(3, -1)); assert.equal(JSON.stringify(input), before);
    assert.equal(blocks[1].items[0].retelling, 'MUST_RETELL'); assert.equal(blocks[1].items[1].retelling, 'MAY_RETELL');
  }
});

test('missing heading, broken numbering and shareable material fall back without hiding or relabeling materials', () => {
  const variations = [
    items => items.slice(2),
    items => items.map(x => x.id === 'heading' ? { ...x, text: '他说你的表现很平静。' } : x),
    items => items.map(x => x.id === 'item-2' ? { ...x, text: '4、编号缺失。' } : x),
    items => items.map(x => x.id === 'item-2' ? { ...x, can_share: true } : x),
    items => items.map(x => x.id === 'intro' ? { ...x, disclosure: 'PUBLIC' } : x),
    items => items.map(x => ({ ...x, disclosure: 'MAY_SHARE' })),
  ];
  for (const change of variations) {
    const input = change(fixture()), blocks = group(input);
    assert.equal(blocks.length, input.length); assert.deepEqual(blocks.map(x => x.material), input);
  }
  const input = [material('h', '你的表现'), material('next', '你的目的'), material('one', '1、其他任务。')];
  assert.equal(group(input).length, input.length);
  assert.equal(group([material('h', '你的表现'), material('decimal', '1.5 元是金额。')]).length, 2);
});

test('the summary directly lists every complete item and image in one card without paging or collection controls', () => {
  const current = group(fixture())[1];
  const html = renderToStaticMarkup(React.createElement(Summary, { group: current, renderVisuals: item => React.createElement('span', null, `图片${item.id}`) }));
  assert.equal((html.match(/<h3/g) || []).length, 1);
  assert.equal((html.match(/以下表现是本阶段的角色要求。/g) || []).length, 1);
  assert.equal((html.match(/data-performance-item/g) || []).length, 5);
  for (let i = 1; i <= 5; i++) {
    assert.match(html, new RegExp(`虚构事项${i}`)); assert.match(html, new RegExp(`完整续行${i}`)); assert.match(html, new RegExp(`图片item-${i}`));
  }
  assert.match(html, /图片heading/); assert.match(html, /图片intro/);
  assert.doesNotMatch(html, /<button|tablist|tabpanel|收藏线索|上一条|下一条|max-h-|overflow-y-auto/);
  assert.equal((html.match(/原本不能出示/g) || []).length, 1);
  assert.equal((html.match(/需讲述/g) || []).length, 1); assert.equal((html.match(/可选择讲述/g) || []).length, 1);
});

test('a replacement view reads only its current items, with no retained selection or invented retelling requirement', () => {
  const current = group(fixture())[1];
  const before = renderToStaticMarkup(React.createElement(Summary, { group: current, renderVisuals: () => null }));
  assert.match(before, /虚构事项5/);
  const after = renderToStaticMarkup(React.createElement(Summary, { group: { ...current, items: [material('replacement', '1、替换后唯一允许读取的内容。')] }, renderVisuals: () => null }));
  assert.match(after, /替换后唯一/); assert.doesNotMatch(after, /虚构事项|需讲述|可选择讲述|原本不能出示/);
  assert.equal(renderToStaticMarkup(React.createElement(Summary, { group: { ...current, items: [] }, renderVisuals: () => null })), '');
});
