const fs = require('node:fs');
const test = require('node:test');
const assert = require('node:assert/strict');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const mod = { exports: {} };
new Function('require', 'module', 'exports', ts.transpileModule(fs.readFileSync('src/components/PlayText.tsx', 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 } }).outputText)(name => { if (name !== '@/lib/playPresentation') return require(name); const helper = { exports: {} }; new Function('module', 'exports', ts.transpileModule(fs.readFileSync('src/lib/playPresentation.ts', 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText)(helper, helper.exports); return helper.exports; }, mod, mod.exports);
const { default: Text, normalizePlayText: normalize } = mod.exports;
test('OCR Chinese hard wraps and gaps repair only layout; complete paragraphs survive', () => {
  const raw = '几天后，她来道别\n\n她不\n\n舍得离开。\n\n如果你留\n下，我就告诉你。';
  assert.equal(normalize(raw), '几天后，她来道别她不舍得离开。\n\n如果你留下，我就告诉你。');
  assert.equal(raw.includes('她不\n\n舍得'), true);
});
test('headings, lists and complete paragraph boundaries are not joined', () => {
  assert.equal(normalize('# 第一部分\n\n这是故事。\n\n## 回忆\n- 小船\n- 灯光\n\n第二段。'), '# 第一部分\n\n这是故事。\n\n## 回忆\n- 小船\n- 灯光\n\n第二段。');
});
test('Latin words and numbers retain separation when OCR lines are joined', () => {
  assert.equal(normalize('我听见 now\nhere 的声音。'), '我听见 now here 的声音。');
  assert.equal(normalize('编号 1\n2 是两个数字。'), '编号 1 2 是两个数字。');
  assert.equal(normalize('编号１\n２是两个数字。'), '编号１ ２是两个数字。');
  assert.equal(normalize('我听到 café\nnoir 的名字。'), '我听到 café noir 的名字。');
});
test('renders hierarchy and emphasis safely without remote content or executable HTML', () => {
  const html = renderToStaticMarkup(React.createElement(Text, { text: '# 大标题\n## 小标题\n**重要内容**\n<script>alert(1)</script>\n![图](https://evil.invalid)\n[跳转](javascript:bad)' }));
  assert.match(html, /<h3[^>]*>大标题<\/h3>/); assert.match(html, /<strong[^>]*>重要内容<\/strong>/);
  assert.ok(!html.includes('<script>')); assert.ok(!html.includes('<img')); assert.ok(!html.includes('<a '));
});
test('player text renders once with no raw-layout tool even if a legacy caller passes original', () => {
  const text = '她不\n\n舍得。';
  for (const original of [undefined, false, true]) {
    const html = renderToStaticMarkup(React.createElement(Text, { text, original }));
    assert.equal((html.match(/她不舍得。/g) || []).length, 1);
    assert.doesNotMatch(html, /查看原始排版|<details|<summary|<pre|她不\n\n舍得。/);
  }
  assert.equal(text, '她不\n\n舍得。');
});
test('recognized plain reading labels retain hierarchy even when OCR omitted Markdown', () => {
  const html = renderToStaticMarkup(React.createElement(Text, { text: '你的目的\n1、 找到虚构的蓝色纸片。\n\n你的表现' }));
  assert.match(html, /<h3[^>]*>你的目的<\/h3>/);
  assert.match(html, /<h3[^>]*>你的表现<\/h3>/);
});
test('Chinese numbered tasks without spaces stay separate from OCR prose and do not turn decimals into list markers', () => {
  const text = '你的目的\n1、核对虚构票据。（2分）\n2、找到虚构钥匙。（3分）\n3 保留原件。\n4、核对时间。（1分）\n\n费用为 1.5 元。';
  assert.equal(normalize(text), text);
  const html = renderToStaticMarkup(React.createElement(Text, { text }));
  assert.equal((html.match(/-indent-4/g) || []).length, 3);
  assert.match(html, /<p[^>]*>3 保留原件。<\/p>/);
  assert.match(html, /费用为 1.5 元。/);
  assert.equal(normalize('3.14 是一个数。'), '3.14 是一个数。');
});

test('supplemental editorial header disappears while the location and exact evidence remain', () => {
  const body = '桌边有碎屑。仅凭这些残留，无法确定它们留下的准确时刻。';
  const raw = `73-R｜虚构房间·桌边残留\n编辑修订版 v1.0｜新增线索｜非原73号卡\n\n${body}`;
  const html = renderToStaticMarkup(React.createElement(Text, { text: raw }));
  assert.match(html, /<h3[^>]*>虚构房间·桌边残留<\/h3>/);
  assert.ok(html.includes(body)); assert.doesNotMatch(html, /73-R|编辑修订版|非原73号卡|新增线索/);
  assert.ok(raw.includes('非原73号卡'), 'original input stays unchanged');
});

test('only the version prefix is removed from public rules; quoted and narrative references stay intact', () => {
  const rules = '## 规则\n\n编辑修订版 v1.0。本页为开场可公开材料；仍按阶段停页。';
  assert.equal(normalize(rules), '## 规则\n\n本页为开场可公开材料；仍按阶段停页。');
  for (const text of ['他说：“这是新增线索。”', '并非原73号卡上的划痕。', '> 编辑修订版 v1.0', '73-R｜某人写下的编号']) assert.equal(normalize(text), text);
});
