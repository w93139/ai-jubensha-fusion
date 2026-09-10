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
const clues = compile('../src/lib/playClueCollection.ts');
const clue = (id = 'map', text = '虚构地图文字') => ({ collection: 'evidence', materialId: id, title: `虚构线索 ${id}`, text, visualIds: ['map-image'], visuals: [{ id: 'map-image', label: '虚构地图' }] });
const key = (owner = 'account-a', play = 'play-a', character = 'role-a') => clues.clueCollectionKey(owner, play, character);
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
  const Component = compile('../src/components/PlayClueCollection.tsx', name => {
    if (name === 'react') return { ...React, ...hooks };
    if (name === '@/lib/playClueCollection') return clues;
    if (name === './PlayText') return compile('../src/components/PlayText.tsx');
    if (name === '@radix-ui/react-dialog') return Object.fromEntries(['Root', 'Trigger', 'Portal', 'Overlay', 'Content', 'Title', 'Description', 'Close'].map(key => [key, `dialog-${key}`]));
    if (name === '@radix-ui/react-tabs') return Object.fromEntries(['Root', 'List', 'Trigger', 'Content'].map(key => [key, `tabs-${key}`]));
    return require(name);
  }).default;
  const props = { ownerId: 'account-a', playId: 'play-a', characterId: 'role-a', locked: false, available: [clue()], renderVisual: visual => React.createElement('authorized-image', { visualId: visual.id, label: visual.label }), ...overrides };
  return { props, render(patch = {}) {
    Object.assign(props, patch); activeRunner = outer;
    const tree = outer.run(() => Component(props));
    if (!tree || typeof tree.type !== 'function') { child?.unmount(); child = undefined; childKey = undefined; return tree; }
    if (!child || childKey !== tree.key) { child?.unmount(); child = hookRunner(); childKey = tree.key; }
    activeRunner = child;
    return child.run(() => tree.type(tree.props));
  }, unmount() { child?.unmount(); outer.unmount(); } };
}

test('clue collection persists explicit material and image references in isolated owner/play/role keys', () => {
  const storage = storageFixture();
  assert.equal(new Set([key(), key('other'), key('account-a', 'other'), key('account-a', 'play-a', 'other')]).size, 4);
  assert.notEqual(key('a:b', 'c'), key('a', 'b:c'));
  const result = clues.addCollectedClue(clues.emptyClueCollection(), clue());
  assert.equal(clues.saveClueCollection(key(), result.document, () => storage).ok, true);
  assert.deepEqual(clues.loadClueCollection(key(), () => storage).document, result.document);
  assert.deepEqual(result.document.entries[0].visualIds, ['map-image']);
  assert.deepEqual(clues.loadClueCollection(key('other'), () => storage).document.entries, []);
  assert.doesNotMatch(storage.writes[0].value, /blob:|https:|file:/);
});

test('unknown, changed and tampered saved contents cannot authorize text or images', () => {
  const document = clues.addCollectedClue(clues.emptyClueCollection(), clue()).document;
  const saved = document.entries[0];
  assert.equal(clues.resolveCollectedClue(saved, []), undefined);
  assert.equal(clues.resolveCollectedClue({ ...saved, text: 'forged secret' }, [clue()]), undefined);
  assert.equal(clues.resolveCollectedClue({ ...saved, visualIds: ['foreign-image'] }, [clue()]), undefined);
  assert.equal(clues.resolveCollectedClue({ ...saved, title: 'untrusted title' }, [clue()]).title, clue().title);
});

test('a new explicit collection refreshes changed authorized text without resurrecting old snapshots', () => {
  const previous = clues.addCollectedClue(clues.emptyClueCollection(), clue()).document;
  const corrected = clue('map', '已核对的虚构地图文字');
  assert.equal(clues.resolveCollectedClue(previous.entries[0], [corrected]), undefined);
  const result = clues.addCollectedClue(previous, corrected);
  assert.equal(result.ok, true); assert.equal(result.duplicate, false);
  assert.equal(result.document.entries.length, 1); assert.equal(result.document.entries[0].text, corrected.text);
  assert.equal(previous.entries[0].text, clue().text);
});

test('malformed storage is preserved; absent identity never touches browser storage', () => {
  const storage = storageFixture();
  for (const raw of ['{', '{"version":2,"entries":[]}', JSON.stringify({ version: 1, entries: [{ ...clue(), url: 'https://foreign.invalid' }] })]) {
    storage.data.set(key(), raw);
    assert.equal(clues.loadClueCollection(key(), () => storage).ok, false);
    assert.equal(storage.data.get(key()), raw);
  }
  const reads = storage.reads.length;
  assert.equal(clues.loadClueCollection(null, () => storage).ok, false);
  assert.equal(clues.saveClueCollection(null, clues.emptyClueCollection(), () => storage).ok, false);
  assert.equal(storage.reads.length, reads); assert.equal(storage.writes.length, 0);
});

test('only explicit collection adds a clue; duplicate selection retains one and cancel survives remount', () => {
  const storage = storageFixture();
  withStorage(storage, () => {
    let app = harness(); let tree = app.render();
    assert.match(content(tree), /还没有收藏线索/); assert.equal(storage.writes.length, 0);
    tree = app.render({ selection: { id: 'evidence:map' } });
    assert.match(content(tree), /已加入我收藏的线索/);
    assert.equal(elements(tree, 'authorized-image')[0].props.visualId, 'map-image');
    tree = app.render({ selection: { id: 'evidence:map' } });
    assert.match(content(tree), /已在收藏中/); assert.equal(storage.writes.length, 1);
    app.unmount(); app = harness(); tree = app.render();
    assert.match(content(tree), /虚构地图文字/);
    assert.equal(elements(tree, 'authorized-image').length, 0, 'closed drawer must not request images');
    button(tree, '取消收藏').props.onClick(); app.render(); app.unmount();
    app = harness(); assert.match(content(app.render()), /还没有收藏线索/); app.unmount();
  });
});

test('lost authorization immediately hides saved text and images without deleting the saved record', () => {
  const storage = storageFixture();
  withStorage(storage, () => {
    const app = harness({ selection: { id: 'evidence:map' } });
    app.render(); const saved = storage.data.get(key());
    const tree = app.render({ available: [], selection: undefined });
    assert.doesNotMatch(content(tree), /虚构地图文字|虚构线索 map/);
    assert.equal(elements(tree, 'authorized-image').length, 0);
    assert.match(content(tree), /当前不可读取的收藏/); assert.equal(storage.data.get(key()), saved);
    app.unmount();
  });
});

test('switch owner/play/role or lock clears displayed collection and ignores stale incoming selections', () => {
  const storage = storageFixture();
  withStorage(storage, () => {
    for (const patch of [{ ownerId: 'other' }, { playId: 'other' }, { characterId: 'other' }, { locked: true }, { ownerId: null }]) {
      const app = harness({ selection: { id: 'evidence:map' } }); const before = app.render();
      const remove = button(before, '取消收藏'); const writes = storage.writes.length;
      const after = app.render(patch);
      assert.doesNotMatch(content(after), /虚构地图文字/); assert.equal(elements(after, 'authorized-image').length, 0);
      remove.props.onClick(); assert.equal(storage.writes.length, writes); app.unmount();
    }
  });
});

test('failed writes keep an unsaved draft and explicit retry succeeds without touching old notebooks', () => {
  const storage = storageFixture(); storage.data.set('fusion:play-notebook:legacy', 'OLD_NOTES');
  withStorage(storage, () => {
    const app = harness(); app.render(); storage.failWrite = true;
    let tree = app.render({ selection: { id: 'evidence:map' } });
    assert.match(content(tree), /本机保存失败/); assert.doesNotMatch(content(tree), /已保存到本机/);
    assert.match(content(tree), /虚构地图文字/); storage.failWrite = false;
    button(tree, '重试保存').props.onClick(); tree = app.render();
    assert.match(content(tree), /已保存到本机/); assert.equal(storage.data.get('fusion:play-notebook:legacy'), 'OLD_NOTES'); app.unmount();
  });
});

test('unreadable collection cannot be overwritten by a new selection before successful explicit read', () => {
  const storage = storageFixture(); storage.data.set(key(), '{broken');
  withStorage(storage, () => {
    const app = harness({ selection: { id: 'evidence:map' } }); let tree = app.render();
    assert.equal(storage.writes.length, 0); assert.equal(storage.data.get(key()), '{broken');
    storage.data.delete(key()); button(tree, '重新读取').props.onClick(); app.render();
    tree = app.render({ selection: { id: 'evidence:map' } }); assert.match(content(tree), /已加入我收藏的线索/); app.unmount();
  });
});

test('collection uses focus-safe modal controls and clamps size without dropping older entries', () => {
  let document = clues.emptyClueCollection();
  for (let i = 0; i < 100; i++) document = clues.addCollectedClue(document, clue(`card-${i}`)).document;
  assert.equal(clues.addCollectedClue(document, clue('overflow')).ok, false); assert.equal(document.entries.length, 100);
  withStorage(storageFixture(), () => {
    const app = harness(); const tree = app.render();
    assert.equal(elements(tree, 'dialog-Trigger').length, 1); assert.equal(elements(tree, 'dialog-Close').length, 1);
    assert.match(elements(tree, 'dialog-Content')[0].props.className, /max-h-\[85dvh\]/);
    app.unmount();
  });
});

test('compact collection switches one detail at a time, searches locally and preserves other entries on removal', () => {
  withStorage(storageFixture(), () => {
    const first = clue('door', '门边的脚印。'), second = { ...clue('clock', '钟表停在九点。'), visualIds: ['clock-image'], visuals: [{ id: 'clock-image', label: '虚构钟表' }] };
    const app = harness({ available: [first, second] });
    app.render(); app.render({ selection: { id: 'evidence:door' } });
    let tree = app.render({ selection: { id: 'evidence:clock' } });
    assert.equal(elements(tree, 'article').length, 1);
    assert.equal(elements(tree, 'button').filter(n => n.props['aria-controls'] === 'play-collected-detail').length, 2);
    assert.deepEqual(elements(tree, 'authorized-image').map(n => n.props.visualId), ['clock-image']);
    elements(tree, 'button').find(n => n.props['aria-label'] === '查看收藏：虚构线索 door').props.onClick(); tree = app.render({ selection: undefined });
    assert.deepEqual(elements(tree, 'authorized-image').map(n => n.props.visualId), ['map-image']);
    elements(tree, 'input')[0].props.onChange({ target: { value: '九点' } }); tree = app.render();
    assert.equal(elements(tree, 'button').filter(n => n.props['aria-controls'] === 'play-collected-detail').length, 1);
    assert.match(content(elements(tree, 'article')[0]), /钟表停在九点/);
    button(tree, '取消收藏').props.onClick(); tree = app.render(); assert.match(content(tree), /没有匹配/);
    elements(tree, 'input')[0].props.onChange({ target: { value: '' } }); tree = app.render();
    assert.match(content(tree), /门边的脚印/); assert.doesNotMatch(content(tree), /钟表停在九点/); app.unmount();
  });
});

test('collection search and compact previews do not reveal unavailable or editor-only material', () => {
  withStorage(storageFixture(), () => {
    const item = clue('card', '编辑修订版 v2\n窗边有一封信。');
    const app = harness({ available: [item], selection: { id: 'evidence:card' } }); let tree = app.render();
    assert.doesNotMatch(content(elements(tree, 'div').find(n => n.props['aria-label'] === '收藏索引')), /修订版/);
    tree = app.render({ available: [], selection: undefined }); elements(tree, 'input')[0].props.onChange({ target: { value: '窗边' } }); tree = app.render();
    assert.match(content(tree), /没有匹配/); assert.doesNotMatch(content(tree), /窗边有一封信/); assert.equal(elements(tree, 'authorized-image').length, 0); app.unmount();
  });
});
