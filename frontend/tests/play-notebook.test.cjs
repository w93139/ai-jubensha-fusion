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
const notebook = compile('../src/lib/playNotebook.ts');
const clip = (id, text = `仅本人可见的虚构材料 ${id}`) => ({ id, title: `虚构来源 ${id}`, text });
const key = (owner = 'account-a', play = 'play-a', character = 'role-a') => notebook.notebookKey(owner, play, character);
function storageFixture() {
  const data = new Map(), reads = [], writes = [];
  return { data, reads, writes, failRead: false, failWrite: false,
    getItem(name) { reads.push(name); if (this.failRead) throw new Error('blocked'); return data.get(name) ?? null; },
    setItem(name, value) { if (this.failWrite) throw new Error('quota'); writes.push({ name, value }); data.set(name, value); },
  };
}
function withStorage(storage, run) {
  const old = Object.getOwnPropertyDescriptor(global, 'window');
  Object.defineProperty(global, 'window', { configurable: true, value: { localStorage: storage } });
  try { return run(); } finally { if (old) Object.defineProperty(global, 'window', old); else delete global.window; }
}
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

function hookRunner() {
  const slots = [], effects = [], pending = []; let index = 0, dirty = false;
  const hooks = {
    useState(initial) {
      const slot = index++; if (!(slot in slots)) slots[slot] = typeof initial === 'function' ? initial() : initial;
      return [slots[slot], value => { const next = typeof value === 'function' ? value(slots[slot]) : value;
        if (!Object.is(next, slots[slot])) { slots[slot] = next; dirty = true; } }];
    },
    useRef(initial) { const slot = index++; if (!(slot in slots)) slots[slot] = { current: initial }; return slots[slot]; },
    useEffect(callback, deps) {
      const slot = index++;
      if (!effects[slot] || deps.some((value, offset) => !Object.is(value, effects[slot].deps[offset]))) pending.push(() => {
        effects[slot]?.cleanup?.(); effects[slot] = { deps, cleanup: callback() };
      });
    },
  };
  return { hooks, run(callback) { let value, count = 0;
    do { dirty = false; index = 0; value = callback(); while (pending.length) pending.shift()(); assert.ok(++count < 20, 'render settles'); } while (dirty);
    return value;
  }, unmount() { for (const effect of effects) effect?.cleanup?.(); } };
}
function harness(overrides = {}) {
  let activeRunner;
  const outer = hookRunner(); let child, childKey;
  const hooks = Object.fromEntries(['useState', 'useRef', 'useEffect'].map(name => [name, (...args) => activeRunner.hooks[name](...args)]));
  const Component = compile('../src/components/PlayNotebook.tsx', name => {
    if (name === 'react') return { ...React, ...hooks };
    if (name === '@/lib/playNotebook') return notebook;
    if (name === './PlayText') return compile('../src/components/PlayText.tsx');
    if (name === '@radix-ui/react-dialog') return Object.fromEntries(['Root', 'Trigger', 'Portal', 'Overlay', 'Content', 'Title', 'Description', 'Close'].map(key => [key, `dialog-${key}`]));
    if (name === '@radix-ui/react-tabs') return Object.fromEntries(['Root', 'List', 'Trigger', 'Content'].map(key => [key, `tabs-${key}`]));
    return require(name);
  }).default;
  const props = { ownerId: 'account-a', playId: 'play-a', characterId: 'role-a', locked: false, memories: [], ...overrides };
  return { props, render(patch = {}) {
    Object.assign(props, patch); activeRunner = outer;
    const tree = outer.run(() => Component(props));
    if (!tree || typeof tree.type !== 'function') { child?.unmount(); child = undefined; childKey = undefined; return tree; }
    if (!child || childKey !== tree.key) { child?.unmount(); child = hookRunner(); childKey = tree.key; }
    activeRunner = child;
    return child.run(() => tree.type(tree.props));
  }, unmount() { child?.unmount(); outer.unmount(); } };
}

test('storage namespace separates authenticated owner, play and character without delimiter collisions', () => {
  assert.equal(new Set([key(), key('account-b'), key('account-a', 'play-b'), key('account-a', 'play-a', 'role-b')]).size, 4);
  assert.notEqual(key('a:b', 'c'), key('a', 'b:c'));
  assert.notEqual(key('a/b', 'c'), key('a', 'b/c'));
  for (const args of [[null, 'p', 'c'], ['', 'p', 'c'], ['a', '', 'c'], ['a', 'p', ' ']]) assert.equal(notebook.notebookKey(...args), null);
});

test('missing identity never acquires storage even when its getter throws', () => {
  let acquired = 0; const blocked = () => { acquired++; throw new Error('must not access'); };
  assert.equal(notebook.loadNotebook(null, blocked).ok, false);
  assert.equal(notebook.saveNotebook(null, notebook.emptyNotebook(), blocked).ok, false);
  assert.equal(acquired, 0);
});

test('saved note and clips survive reload; memories and unknown fields are not accepted', () => {
  const storage = storageFixture(), document = { version: 1, note: '本人判断\n需要核对', clips: [clip('knowledge:mine')] };
  assert.equal(notebook.saveNotebook(key(), document, () => storage).ok, true);
  assert.deepEqual(notebook.loadNotebook(key(), () => storage), { ok: true, document, exists: true });
  assert.deepEqual(notebook.loadNotebook(key('another'), () => storage).document, notebook.emptyNotebook());
  assert.equal(notebook.validNotebookDocument({ ...document, memories: [clip('secret')] }), false);
});

test('corrupt, duplicate and unsupported saved records are rejected without overwrite', () => {
  const storage = storageFixture();
  for (const raw of ['{', JSON.stringify({ version: 2, note: '', clips: [] }), JSON.stringify({ version: 1, note: '', clips: [clip('same'), clip('same')] })]) {
    storage.data.set(key(), raw); assert.equal(notebook.loadNotebook(key(), () => storage).ok, false);
    assert.equal(storage.data.get(key()), raw);
  }
  assert.equal(storage.writes.length, 0);
});

test('storage getter, read and write failures never report success', () => {
  const blocked = () => { throw new Error('privacy mode'); }, storage = storageFixture();
  assert.equal(notebook.loadNotebook(key(), blocked).ok, false);
  assert.equal(notebook.saveNotebook(key(), notebook.emptyNotebook(), blocked).ok, false);
  storage.failRead = storage.failWrite = true;
  assert.equal(notebook.loadNotebook(key(), () => storage).ok, false);
  assert.equal(notebook.saveNotebook(key(), notebook.emptyNotebook(), () => storage).ok, false);
});

test('clips dedupe by source ID, retain original text and stop at 100 without dropping earlier clips', () => {
  let document = notebook.emptyNotebook(); const incoming = clip('first');
  const first = notebook.addNotebookClip(document, incoming); assert.equal(first.ok, true); document = first.document;
  incoming.text = 'later mutation'; assert.notEqual(document.clips[0].text, incoming.text);
  const duplicate = notebook.addNotebookClip(document, clip('first', 'replacement'));
  assert.equal(duplicate.duplicate, true); assert.equal(duplicate.document, document);
  for (let i = 1; i < 100; i++) document = notebook.addNotebookClip(document, clip(`source-${i}`)).document;
  const before = JSON.stringify(document);
  assert.equal(notebook.addNotebookClip(document, clip('overflow')).ok, false);
  assert.equal(JSON.stringify(document), before); assert.equal(document.clips.length, 100);
  assert.equal(notebook.addNotebookClip(document, clip('first')).duplicate, true);
});

test('note and clip size limits reject oversized content before any storage write', () => {
  const storage = storageFixture();
  const full = { version: 1, note: '字'.repeat(20_000), clips: [] };
  assert.equal(notebook.saveNotebook(key(), full, () => storage).ok, true);
  assert.equal(notebook.saveNotebook(key(), { ...full, note: full.note + '字' }, () => storage).ok, false);
  assert.equal(notebook.addNotebookClip(full, clip('too-long', '字'.repeat(20_001))).ok, false);
  assert.equal(storage.writes.length, 1);
});

test('export contains current notes and attributed clips as plain text, with no memory side channel', () => {
  const document = { version: 1, note: '<script>我的判断</script>', clips: [clip('fact', '虚构来源原文')] };
  const output = notebook.notebookExportText(document, 'play-export', 'role-export');
  assert.match(output, /play-export/); assert.match(output, /role-export/);
  assert.match(output, /<script>我的判断<\/script>/); assert.match(output, /虚构来源 fact\n虚构来源原文/);
  assert.match(output, /不会发给 AI/); assert.doesNotMatch(output, /account-a|memory|ownerId/);
});

test('null owner and locked projection do not read storage or render private memories or drafts', () => {
  const storage = storageFixture(); storage.data.set(key(), JSON.stringify({ version: 1, note: 'OLD_PRIVATE_NOTE', clips: [] }));
  withStorage(storage, () => {
    for (const patch of [{ ownerId: null }, { locked: true }]) {
      const app = harness({ ...patch, memories: [clip('private', 'PRIVATE_MEMORY')], clip: clip('private-clip') });
      const tree = app.render(); assert.equal(button(tree, '随身手记').props.disabled, true);
      assert.doesNotMatch(content(tree), /PRIVATE/); app.unmount();
    }
  });
  assert.equal(storage.reads.length, 0); assert.equal(storage.writes.length, 0);
});

test('component autosaves only current owner scope and remount restores it', () => {
  const storage = storageFixture();
  withStorage(storage, () => {
    let app = harness(); let tree = app.render();
    assert.match(content(tree), /本机保存/); assert.match(content(tree), /不会发给 AI/); assert.match(content(tree), /不同浏览器不同步/);
    elements(tree, 'textarea')[0].props.onChange({ target: { value: '本人的新笔记' } });
    tree = app.render(); assert.match(content(tree), /已保存到本机/); assert.equal(storage.writes.length, 1);
    assert.equal(storage.writes[0].name, key()); app.unmount();
    app = harness(); assert.equal(elements(app.render(), 'textarea')[0].props.value, '本人的新笔记'); app.unmount();
  });
});

test('switching owner, play or character clears displayed draft and stale event handlers cannot write', () => {
  const storage = storageFixture();
  withStorage(storage, () => {
    for (const change of [{ ownerId: 'account-b' }, { playId: 'play-b' }, { characterId: 'role-b' }, { ownerId: null }]) {
      const app = harness(); const oldInput = elements(app.render(), 'textarea')[0];
      oldInput.props.onChange({ target: { value: 'OLD_OWNER_PRIVATE' } });
      const tree = app.render(change); assert.doesNotMatch(content(tree), /OLD_OWNER_PRIVATE/);
      assert.equal(elements(tree, 'textarea')[0]?.props.value ?? '', '');
      const count = storage.writes.length; oldInput.props.onChange({ target: { value: 'late old event' } });
      assert.equal(storage.writes.length, count); app.unmount();
    }
  });
});

test('clip processing is once per object, stale clip is not imported after identity change, and memories stay out of storage', () => {
  const storage = storageFixture(); let handled = 0;
  withStorage(storage, () => {
    const incoming = clip('source-a');
    const app = harness({ clip: incoming, memories: [clip('memory-only', 'NEVER_PERSIST_MEMORY')], onClipHandled() { handled++; } });
    let tree = app.render(); assert.equal(storage.writes.length, 1); assert.equal(handled, 1);
    assert.equal(elements(tree, 'dialog-Root')[0].props.open, true); assert.equal(elements(tree, 'tabs-Root')[0].props.value, 'clips');
    app.render(); assert.equal(storage.writes.length, 1);
    tree = app.render({ ownerId: 'account-b' }); assert.equal(storage.writes.length, 1);
    assert.doesNotMatch(content(tree), /仅本人可见的虚构材料 source-a/);
    app.render({ clip: clip('source-b') }); assert.equal(storage.writes.length, 2); assert.equal(storage.writes[1].name, key('account-b'));
    assert.ok(storage.writes.every(write => !write.value.includes('NEVER_PERSIST_MEMORY'))); app.unmount();
  });
});

test('failed autosave keeps draft visible, does not say saved, and explicit retry persists exact draft', () => {
  const storage = storageFixture();
  withStorage(storage, () => {
    const app = harness(); let tree = app.render(); storage.failWrite = true;
    elements(tree, 'textarea')[0].props.onChange({ target: { value: 'UNSAVED_BUT_VISIBLE' } });
    tree = app.render(); assert.equal(elements(tree, 'textarea')[0].props.value, 'UNSAVED_BUT_VISIBLE');
    assert.match(content(tree), /本机保存失败/); assert.doesNotMatch(content(tree), /已保存到本机/);
    assert.equal(storage.writes.length, 0); storage.failWrite = false;
    button(tree, '重试保存').props.onClick(); tree = app.render();
    assert.match(content(tree), /已保存到本机/); assert.equal(JSON.parse(storage.data.get(key())).note, 'UNSAVED_BUT_VISIBLE'); app.unmount();
  });
});

test('unreadable old record blocks editing and incoming clip overwrite until an explicit successful reload', () => {
  const storage = storageFixture(); storage.data.set(key(), '{broken');
  withStorage(storage, () => {
    const app = harness({ clip: clip('new') }); let tree = app.render();
    assert.equal(elements(tree, 'textarea')[0].props.disabled, true); assert.equal(storage.writes.length, 0);
    assert.match(content(tree), /原数据未被覆盖/); assert.equal(storage.data.get(key()), '{broken');
    storage.data.set(key(), JSON.stringify({ version: 1, note: 'RECOVERED_NOTE', clips: [] }));
    button(tree, '重新读取').props.onClick(); tree = app.render();
    assert.equal(elements(tree, 'textarea')[0].props.value, 'RECOVERED_NOTE'); assert.equal(storage.writes.length, 0);
    app.render({ clip: clip('new') }); assert.equal(storage.writes.length, 1); app.unmount();
  });
});

test('drawer uses existing modal focus/Escape primitives and labelled keyboard tabs, with escaped React text', () => {
  const storage = storageFixture();
  withStorage(storage, () => {
    const app = harness({ memories: [clip('memory', '<img src=x onerror=LEAK()>')] }); const tree = app.render();
    assert.equal(elements(tree, 'dialog-Trigger').length, 1); assert.equal(elements(tree, 'dialog-Close').length, 1);
    assert.equal(elements(tree, 'dialog-Content').length, 1); assert.equal(elements(tree, 'dialog-Title').length, 1);
    assert.equal(elements(tree, 'tabs-List')[0].props['aria-label'], '笔记本内容');
    assert.deepEqual(elements(tree, 'tabs-Trigger').map(content), ['笔记', '收藏', '回忆']);
    assert.equal(elements(tree, 'img').length, 0); assert.match(content(tree), /<img src=x onerror=LEAK\(\)>/); app.unmount();
  });
});

test('notebook entry rises above the expanded composer without changing its stored scope', () => {
  const storage = storageFixture();
  withStorage(storage, () => {
    const app = harness(); let tree = app.render();
    assert.match(button(tree, '随身手记').props.className, /bottom-28/);
    elements(tree, 'textarea')[0].props.onChange({ target: { value: '仍是同一局笔记' } });
    tree = app.render({ lifted: true }); assert.match(button(tree, '随身手记').props.className, /42vh\+6rem/);
    assert.equal(elements(tree, 'textarea')[0].props.value, '仍是同一局笔记');
    assert.equal(storage.reads.length, 1); app.unmount();
  });
});

test('download exports the exact unsaved draft and saved clips as a text blob, without current memory cards', async t => {
  const storage = storageFixture(); let captured, clicked = 0, removed = 0, revoked;
  const anchor = { click() { clicked++; }, remove() { removed++; } };
  const previous = Object.getOwnPropertyDescriptor(global, 'document');
  Object.defineProperty(global, 'document', { configurable: true, value: { createElement: tag => { assert.equal(tag, 'a'); return anchor; }, body: { appendChild: value => assert.equal(value, anchor) } } });
  t.mock.method(URL, 'createObjectURL', blob => { captured = blob; return 'blob:local-notebook'; });
  t.mock.method(URL, 'revokeObjectURL', url => { revoked = url; });
  t.mock.method(global, 'setTimeout', callback => { callback(); return 1; });
  try {
    withStorage(storage, () => {
      const app = harness({ clip: clip('saved-source'), memories: [clip('memory-only', 'DO_NOT_EXPORT_MEMORY')] });
      let tree = app.render(); storage.failWrite = true;
      elements(tree, 'textarea')[0].props.onChange({ target: { value: '尚未保存的判断\n第二行' } });
      tree = app.render(); button(tree, '导出笔记和收藏 .txt').props.onClick();
      assert.match(content(app.render()), /已发起文本导出/); app.unmount();
    });
    assert.equal(captured.type, 'text/plain;charset=utf-8'); assert.match(anchor.download, /\.txt$/);
    assert.equal(anchor.href, 'blob:local-notebook'); assert.equal(clicked, 1); assert.equal(removed, 1);
    assert.equal(revoked, 'blob:local-notebook'); const text = await captured.text();
    assert.match(text, /尚未保存的判断\n第二行/); assert.match(text, /仅本人可见的虚构材料 saved-source/);
    assert.doesNotMatch(text, /DO_NOT_EXPORT_MEMORY/);
  } finally { if (previous) Object.defineProperty(global, 'document', previous); else delete global.document; }
});
