const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const ts = require('typescript');

function compile(relativePath) {
  const filename = path.join(__dirname, relativePath);
  const { outputText } = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    fileName: filename, compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
  });
  const module = { exports: {} };
  new Function('require', 'module', 'exports', outputText)(name => {
    if (name === '@/components/PlayText') return compile('../src/components/PlayText.tsx');
    if (name === '@/components/OpeningImage') return compile('../src/components/OpeningImage.tsx');
    if (name === '@/components/PlayReferencePanel') return { default: function PlayReferencePanel() { return null; } };
    if (name === '@/components/AuthGuard' || name === '@/components/AppLayout') return { default: ({ children }) => children };
    if (name === 'next/link') return { default: ({ children, prefetch, ...props }) => React.createElement('a', props, children) };
    if (name === 'next/router') return { useRouter: () => ({ isReady: true, asPath: '/fixture' }) };
    if (name === '@/stores/authStore') return { useAuthStore: select => select({ isAuthenticated: true, user: { id: 'account-a' } }) };
    if (name === '@/services/packagePlayService') return { default: {} };
    if (name === '@/services/packagePreviewService') return { default: {} };
    if (name === '@/lib/playPresentation' || name === '@/lib/playImageOrientation') return compile(`../src/lib/${name.slice(6)}.ts`);
    if (name === './PlayImageView') return compile('../src/components/PlayImageView.tsx');
    return require(name);
  }, module, module.exports);
  return module.exports;
}
const { OpeningReading } = compile('../src/pages/play/package-preview.tsx');
const item = (id, text) => ({ id, text, disclosure: 'KEEP_PRIVATE' });
const preview = {
  session_id: 'fictional-opening', selected_character_id: 'a',
  script: { title: '虚构测试剧本', content_version: 'internal-build-marker' },
  characters: [{ id: 'a', name: '虚构甲' }], introduction: { text: '# 故事介绍\n\n这是**测试简介**。' },
  public_knowledge: [item('rules', '# 玩家共用规则\n\n## 怎样调查\n\n每个目标单独处理。\n\n## 终局同时提交三项\n\n1. **主案正式指认**：填写人名。\n2. **信任票**：选择角色。\n3. **私密推理答卷**：写下依据。')],
  public_evidence: [item('evidence', '## 公开线索\n\n**先核对来源**。')],
  private_knowledge: [item('goals', '你的目的\n\n找到**虚构证物**。')],
  private_evidence: [item('private-evidence', '## 私有线索\n\n她不\n\n舍得你走。')],
};
const elements = (tree, type) => Array.isArray(tree) ? tree.flatMap(node => elements(node, type))
  : !React.isValidElement(tree) ? [] : [...(tree.type === type || tree.type.name === type ? [tree] : []), ...elements(tree.props.children, type)];

test('actual opening reading renders screenshot headings and numbered emphasis through the formatted renderer', () => {
  const html = renderToStaticMarkup(React.createElement(OpeningReading, { preview }));
  for (const title of ['怎样调查', '终局同时提交三项', '公开线索', '私有线索']) {
    assert.match(html, new RegExp(`<h[1-6][^>]*>${title}</h[1-6]>`));
  }
  for (const title of ['主案正式指认', '信任票', '私密推理答卷', '测试简介', '虚构证物']) {
    assert.match(html, new RegExp(`<strong[^>]*>${title}</strong>`));
  }
  assert.match(html, /她不舍得你走。/);
  assert.doesNotMatch(html, /##|\*\*|internal-build-marker/);
});

test('opening reference receives current private goals, independent public rules and no locked future memories', () => {
  const tree = OpeningReading({ preview });
  const reference = elements(tree, 'PlayReferencePanel')[0].props;
  assert.deepEqual(reference.materials, [...preview.private_knowledge, ...preview.private_evidence]);
  assert.deepEqual(reference.rulesMaterials, preview.public_knowledge);
  assert.deepEqual(reference.memories, []);
  assert.ok(reference.scope.includes(preview.session_id));
  assert.ok(reference.scope.endsWith(preview.selected_character_id));
  const nav = elements(tree, 'nav')[0];
  assert.equal(nav.props['aria-label'], '随时查阅');
  const header = elements(tree, 'header')[0];
  assert.equal(header.props['aria-label'], '开场顶部导航');
  assert.match(header.props.className, /sticky/);
  assert.ok(elements(header, 'nav').includes(nav));
  assert.equal(reference.toolbar, true);
});

test('opening start is a single explicit action with no player rules-inspection entry', () => {
  const html = renderToStaticMarkup(React.createElement(OpeningReading, { preview: { ...preview, session_id: 'opening/a?b', supports_rules_preview: true }, onStart() {} }));
  assert.match(html, /<button[^>]*class="opening-start[^>]*>开始游戏/);
  assert.doesNotMatch(html, /package-flow|检查阶段规则|开场预览|版本核验|自动升级/);
  assert.equal((html.match(/开始游戏<span/g) || []).length, 1);
  assert.match(html, /opening-start-arrow/);
});

test('opening start forwards one click and reflects busy or explicit recovery labels', () => {
  let starts = 0;
  const action = elements(OpeningReading({ preview, onStart() { starts++; } }), 'button').find(item => item.props.className.includes('opening-start'));
  assert.equal(starts, 0); action.props.onClick(); assert.equal(starts, 1);
  const html = renderToStaticMarkup(React.createElement(OpeningReading, { preview, onStart() {}, starting: true }));
  assert.match(html, /disabled=""/); assert.match(html, /正在进入/);
  const retry = renderToStaticMarkup(React.createElement(OpeningReading, { preview, onStart() {}, startLabel: '检查开始结果' }));
  assert.match(retry, /检查开始结果<span/);
});

test('start recovery feedback remains in the sticky opening header while materials stay readable', () => {
  const tree = OpeningReading({ preview, onStart() {}, startError: '请检查开始结果' });
  const header = elements(tree, 'header')[0];
  assert.equal(elements(header, 'p').find(item => item.props.role === 'alert').props.children, '请检查开始结果');
  assert.ok(elements(tree, 'article').length);
});
