const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const React = require('react');
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
const reference = compile('../src/lib/playReference.ts');
const material = (id, text) => ({ id, text });
const memory = (id, text) => ({ id, title: `回忆 ${id}`, text });

test('Markdown task sections stop before the next unrelated section, even when nested', () => {
  const text = '# 虚构角色\n## 你的目标\n1. 找回遗失的票据。\n### 人物背景\n这是背景，不是任务。\n## 本阶段任务\n- 核对钟表。\n## 其他资料\n不是任务。';
  const tasks = reference.extractTaskSections([material('mine', text)]);
  assert.deepEqual(tasks.map(row => row.text), ['## 你的目标\n1. 找回遗失的票据。', '## 本阶段任务\n- 核对钟表。']);
  assert.deepEqual(tasks.map(row => row.materialId), ['mine', 'mine']);
  assert.equal(new Set(tasks.map(row => row.id)).size, 2);
});

test('OCR full headings extract all task sections without annexing behavior or memories', () => {
  const text = '你的目的\n1、查清铃声来源。\n你的表现\n保持耐心。\n你的回忆（共2条）\n尚未触发。\n当前阶段任务：\n寻找交班记录。\n背景资料\n港口背景。';
  assert.deepEqual(reference.extractTaskSections([material('private', text)]).map(row => row.text), [
    '你的目的\n1、查清铃声来源。', '当前阶段任务：\n寻找交班记录。',
  ]);
});

test('standalone bold and Setext task headings are supported', () => {
  const tasks = reference.extractTaskSections([
    material('bold', '**你的任务**\n确认列车到站次序。\n**你的秘密**\n一段秘密。'),
    material('setext', '你的目标\n---\n核对签名。\n其他资料\n---\n资料正文。'),
  ]);
  assert.deepEqual(tasks.map(row => row.text), ['**你的任务**\n确认列车到站次序。', '你的目标\n---\n核对签名。']);
});

test('original task text, punctuation, line breaks and numbering remain exact', () => {
  const source = '前言\r\n\r\n### 你的目标：\r\n７、 核对 17:25 的记录；不能推定完成。\r\n续行保留。\r\n\r\n### 其他资料\r\n不属于目标。';
  const rows = reference.extractTaskSections([material('exact', source)]);
  assert.equal(rows[0].text, '### 你的目标：\r\n７、 核对 17:25 的记录；不能推定完成。\r\n续行保留。');
  assert.ok(source.includes(rows[0].text));
});

test('IDs, quoted fragments, inline mentions and another role target are not objectives', () => {
  const rows = reference.extractTaskSections([
    material('goals-0', '你的目的”)。\n下一阶段开始。'),
    material('goals-1', '请重新看看“你的目标”。\n这里是普通叙述。'),
    material('goals-2', '## 韩医生的目标\n这是另一角色的目标。'),
    material('goals-3', '你的目标：别人告诉了你一件事。'),
    material('goals-4', '> 你的目的\n> 这是转述。'),
    material('goals-5', '```text\n你的目标\n示例内容。\n```'),
  ]);
  assert.deepEqual(rows, []);
});

test('projection consumes only supplied private materials and does not alter its input', () => {
  const materials = Object.freeze([Object.freeze(material('mine', '你的目的\n验证一份收据。'))]);
  assert.equal(reference.extractTaskSections(materials).length, 1);
  assert.deepEqual(reference.extractTaskSections([]), []);
  assert.equal(materials[0].text, '你的目的\n验证一份收据。');
});

test('explicit rule document titles keep subsections and exclude a later peer document', () => {
  const rules = reference.extractRuleSections([material('public', '# 《虚构航站》玩家共用规则\n共同规则。\n## 怎样交流\n轮流发言。\n## 怎样结束\n交回答卷。\n# 某人的经历\n无关故事。')]);
  assert.equal(rules.length, 1);
  assert.equal(rules[0].text, '# 《虚构航站》玩家共用规则\n共同规则。\n## 怎样交流\n轮流发言。\n## 怎样结束\n交回答卷。');
  assert.equal(reference.extractRuleSections([material('plain', '游戏规则\n先听完对方说话。')]).length, 1);
  assert.equal(reference.extractRuleSections([material('bold', '**玩家规则**\n每次选择一个目标。')]).length, 1);
});

test('rule mentions or IDs never fabricate rules, and unrelated materials return empty', () => {
  assert.deepEqual(reference.extractRuleSections([
    material('rules', '请遵守游戏规则。'),
    material('later', '# 人物故事\n故事正文。\n## 游戏规则\n这是故事内的一张纸。'),
    material('quoted', '> 玩家规则\n> 引文。'),
  ]), []);
});

const elements = (tree, type) => {
  if (Array.isArray(tree)) return tree.flatMap(child => elements(child, type));
  if (!React.isValidElement(tree)) return [];
  return [...(tree.type === type ? [tree] : []), ...elements(tree.props.children, type)];
};
function content(tree) {
  if (Array.isArray(tree)) return tree.map(content).join('');
  if (!React.isValidElement(tree)) return typeof tree === 'string' || typeof tree === 'number' ? String(tree) : '';
  if (typeof tree.type === 'function' && tree.type.name === 'PlayText') return content(tree.type(tree.props));
  return content(tree.props.children);
}
const button = (tree, label) => elements(tree, 'button').find(item => content(item) === label);
function harness(overrides = {}) {
  let slots = [], index = 0, previousKey;
  const hooks = {
    useState(initial) { const slot = index++; if (!(slot in slots)) slots[slot] = initial;
      return [slots[slot], value => { slots[slot] = typeof value === 'function' ? value(slots[slot]) : value; }]; },
    useRef(initial) { const slot = index++; if (!(slot in slots)) slots[slot] = { current: initial }; return slots[slot]; },
    useId() { const slot = index++; if (!(slot in slots)) slots[slot] = `reference-${slot}`; return slots[slot]; },
  };
  const Component = compile('../src/components/PlayReferencePanel.tsx', name => {
    if (name === 'react') return { ...React, ...hooks };
    if (name === '../lib/playReference') return reference;
    if (name === './PlayText') return compile('../src/components/PlayText.tsx');
    if (name === '@radix-ui/react-dialog') return Object.fromEntries(['Root', 'Portal', 'Overlay', 'Content', 'Title', 'Description', 'Close'].map(key => [key, `dialog-${key}`]));
    if (name === '@radix-ui/react-tabs') return Object.fromEntries(['Root', 'List', 'Trigger', 'Content'].map(key => [key, `tabs-${key}`]));
    return require(name);
  }).default;
  const props = { scope: 'owner/opening/role', characterName: '林队长', materials: [], memories: [], ...overrides };
  return { render(patch = {}) {
    Object.assign(props, patch); const tree = Component(props);
    if (typeof tree.type !== 'function') { slots = []; previousKey = undefined; return tree; }
    if (tree.key !== previousKey) { slots = []; previousKey = tree.key; }
    index = 0; return tree.type(tree.props);
  } };
}

test('preview needs no play ID or storage and has direct task and memory buttons', () => {
  const app = harness(); const tree = app.render();
  assert.ok(button(tree, '任务')); assert.ok(button(tree, '回忆（0）'));
  assert.equal(button(tree, '任务').props['aria-haspopup'], 'dialog');
  assert.equal(elements(tree, 'a').length, 0);
  assert.match(content(tree), /暂时没有可单独查看的任务/);
  assert.match(content(tree), /当前没有可查看的玩家规则/);
});

test('task and memory buttons open the corresponding tab in one dialog', () => {
  const app = harness({ memories: [memory('one', '一段已授权回忆。')] }); let tree = app.render();
  button(tree, '回忆（1）').props.onClick({ currentTarget: {} }); tree = app.render();
  assert.equal(elements(tree, 'dialog-Root')[0].props.open, true);
  assert.equal(elements(tree, 'tabs-Root')[0].props.value, 'memories');
  elements(tree, 'dialog-Root')[0].props.onOpenChange(false); tree = app.render();
  button(tree, '任务').props.onClick({ currentTarget: {} }); tree = app.render();
  assert.equal(elements(tree, 'tabs-Root')[0].props.value, 'tasks');
  assert.equal(elements(tree, 'dialog-Content').length, 1);
});

test('close and Escape autofocus return to the specific opener without scrolling', () => {
  for (const label of ['任务', '回忆（0）']) {
    const app = harness(); let tree = app.render(); const focused = [];
    const opener = { isConnected: true, focus(options) { focused.push(options); } };
    button(tree, label).props.onClick({ currentTarget: opener }); tree = app.render();
    elements(tree, 'dialog-Root')[0].props.onOpenChange(false); tree = app.render();
    let prevented = 0;
    elements(tree, 'dialog-Content')[0].props.onCloseAutoFocus({ preventDefault() { prevented++; } });
    assert.equal(prevented, 1); assert.deepEqual(focused, [{ preventScroll: true }]);
    opener.isConnected = false;
    elements(tree, 'dialog-Content')[0].props.onCloseAutoFocus({ preventDefault() {} });
    assert.equal(focused.length, 1);
  }
});

test('open autofocus stays in the drawer and each tab scrolls independently', () => {
  const tree = harness().render(); const focused = [];
  button(tree, '关闭').props.ref.current = { focus(options) { focused.push(options); } };
  let prevented = false;
  elements(tree, 'dialog-Content')[0].props.onOpenAutoFocus({ preventDefault() { prevented = true; } });
  assert.equal(prevented, true); assert.deepEqual(focused, [{ preventScroll: true }]);
  assert.ok(elements(tree, 'dialog-Content')[0].props.className.includes('fixed'));
  for (const tab of elements(tree, 'tabs-Content')) assert.match(tab.props.className, /overflow-y-auto overscroll-contain/);
});

test('scope changes or locking discard old content and open state in the first render', () => {
  const app = harness({ materials: [material('task', '你的目的\nOLD_PRIVATE_TASK')], memories: [memory('old', 'OLD_PRIVATE_MEMORY')] });
  let tree = app.render(); button(tree, '任务').props.onClick({ currentTarget: {} });
  tree = app.render({ scope: 'another/preview/role', characterName: '周编辑', materials: [], memories: [] });
  assert.doesNotMatch(content(tree), /OLD_PRIVATE|林队长/);
  assert.equal(elements(tree, 'dialog-Root')[0].props.open, false);
  tree = app.render({ locked: true, materials: [material('old', '你的目的\nLOCKED_PRIVATE')], memories: [memory('old', 'LOCKED_PRIVATE')] });
  assert.doesNotMatch(content(tree), /LOCKED_PRIVATE|周编辑/);
  assert.equal(elements(tree, 'dialog-Content').length, 0);
  assert.equal(button(tree, '回忆（0）').props.disabled, true);
  assert.equal(harness({ scope: ' ', memories: [memory('old', 'PRIVATE')] }).render().props.children.length, 2);
});

test('same-scope updates use only current visible memories with no retained originals', () => {
  const app = harness({ memories: [memory('one', 'PREVIOUS_MEMORY')] });
  assert.match(content(app.render()), /PREVIOUS_MEMORY/);
  let tree = app.render({ memories: [memory('two', 'CURRENT_MEMORY'), memory('three', 'NEW_MEMORY')] });
  assert.doesNotMatch(content(tree), /PREVIOUS_MEMORY/);
  assert.ok(button(tree, '回忆（2）')); assert.match(content(tree), /CURRENT_MEMORY/);
  tree = app.render({ memories: [] }); assert.ok(button(tree, '回忆（0）'));
  assert.doesNotMatch(content(tree), /CURRENT_MEMORY|NEW_MEMORY/);
});

test('public rules input never contributes tasks and private rule-like text never becomes rules', () => {
  const app = harness({
    materials: [material('mine', '你的目的\nCURRENT_TASK'), material('private-paper', '# 玩家规则\nPRIVATE_RULE_LIKE_TEXT')],
    rulesMaterials: [material('common', '# 玩家规则\nPUBLIC_RULE'), material('shared-other', '你的目的\nOTHER_ROLE_TASK')],
  });
  const tree = app.render(); const panels = elements(tree, 'tabs-Content');
  assert.match(content(panels.find(row => row.props.value === 'tasks')), /CURRENT_TASK/);
  assert.doesNotMatch(content(tree), /OTHER_ROLE_TASK/);
  const rules = content(panels.find(row => row.props.value === 'rules'));
  assert.match(rules, /PUBLIC_RULE/); assert.doesNotMatch(rules, /PRIVATE_RULE_LIKE_TEXT/);
  assert.match(content(panels.find(row => row.props.value === 'materials')), /PRIVATE_RULE_LIKE_TEXT/);
});

test('memory trigger hints require the exact counted title and keep the authorized slice unchanged', () => {
  const original = '前言\r\n## 你的回忆（共2条）\r\n1、纸鸢。\r\n2、汽笛。\r\n## 你的表现\r\n保持冷静。';
  const hints = reference.extractMemoryTriggerSections([material('mine', original),
    material('ascii', '你的回忆(共1条)\n1、邮戳。\n其他资料\n背景。')]);
  assert.deepEqual(hints.map(row => row.text), ['## 你的回忆（共2条）\r\n1、纸鸢。\r\n2、汽笛。', '你的回忆(共1条)\n1、邮戳。']);
  assert.ok(original.includes(hints[0].text));
  assert.deepEqual(reference.extractMemoryTriggerSections([
    material('trigger-list', '你想到了回忆。'), material('quote', '她说“你的回忆（共2条）”。'),
    material('uncounted', '你的回忆\n旧事。'), material('broken', '你的回忆（共2条)\n残片。'),
    material('other', '其他角色的回忆（共2条）\n别人的提示。'),
  ]), []);
});

test('visible trigger hints never increase unlocked memory count or fabricate cards', () => {
  const app = harness({ materials: [material('hints', '你的回忆（共5条）\n1、纸鸢。\n2、汽笛。')], memories: [] });
  const tree = app.render(); const memories = elements(tree, 'tabs-Content').find(row => row.props.value === 'memories');
  assert.ok(button(tree, '回忆（0）'));
  assert.match(content(memories), /触发提示，不是已解锁回忆/);
  assert.match(content(memories), /阅读提示不会解锁回忆/);
  assert.match(content(memories), /纸鸢/); assert.match(content(memories), /还没有已解锁的回忆/);
  assert.equal(elements(memories, 'h4').length, 0);
});

test('memory empty state opens all current actor materials, including unclassified background', () => {
  const sources = [material('background', 'UNCLASSIFIED_AUTHORIZED_BACKGROUND'), material('hints', '你的回忆（共1条）\n1、邮戳。'), material('evidence', 'AUTHORIZED_PRIVATE_EVIDENCE')];
  const app = harness({ materials: sources, rulesMaterials: [material('public', '# 玩家规则\nPUBLIC_RULE_ONLY')], memories: [] });
  let tree = app.render(); button(tree, '查看角色资料').props.onClick(); tree = app.render();
  assert.equal(elements(tree, 'tabs-Root')[0].props.value, 'materials');
  const materials = elements(tree, 'tabs-Content').find(row => row.props.value === 'materials');
  for (const source of sources) assert.match(content(materials), new RegExp(source.text.split('\n').at(-1)));
  assert.doesNotMatch(content(materials), /PUBLIC_RULE_ONLY/);
  assert.deepEqual(elements(materials, 'section').length, sources.length);
  const tabs = elements(tree, 'tabs-List')[0];
  assert.match(tabs.props.className, /grid-cols-4/);
  assert.deepEqual(elements(tabs, 'tabs-Trigger').map(row => content(row)), ['任务', '回忆', '角色资料', '规则']);
});

test('current grants, trigger hints and role material update independently in the same scope', () => {
  const app = harness({ materials: [material('old', '你的回忆（共4条）\nOLD_TRIGGER')], memories: [memory('one', 'ACTUAL_GRANTED_CARD')] });
  let tree = app.render(); assert.ok(button(tree, '回忆（1）')); assert.match(content(tree), /OLD_TRIGGER/);
  tree = app.render({ materials: [material('new', 'NEW_AUTHORIZED_BACKGROUND')], memories: [] });
  assert.ok(button(tree, '回忆（0）')); assert.doesNotMatch(content(tree), /OLD_TRIGGER|ACTUAL_GRANTED_CARD/);
  assert.match(content(tree), /NEW_AUTHORIZED_BACKGROUND/);
  tree = app.render({ locked: true });
  assert.doesNotMatch(content(tree), /NEW_AUTHORIZED_BACKGROUND/);
  assert.equal(elements(tree, 'dialog-Content').length, 0);
});
