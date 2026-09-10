const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const ts = require('typescript');

function compile(relative, imports = require) {
  const filename = path.join(__dirname, relative);
  const { outputText } = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    fileName: filename,
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
  });
  const compiled = { exports: {} };
  new Function('require', 'module', 'exports', outputText)(imports, compiled, compiled.exports);
  return compiled.exports;
}
const logic = compile('../src/lib/playSpeech.ts');
const { createPlaySpeechState, syncPlaySpeech, playSpeechReducer, pendingSpeechSequences, splitSpeechLines } = logic;
const scope = 'viewer:fictional-play:character-a';
const entry = (sequence, text = '甲'.repeat(49)) => ({ id: `speech-${sequence}`, sequence, text });
const sync = (state, entries, instant = false) => syncPlaySpeech(state, scope, entries, instant);
const baseline = () => sync(createPlaySpeechState(scope), []);
const tick = state => playSpeechReducer(state, {
  type: 'tick', scope: state.scope, id: state.queue[0]?.entry.id, shown: state.queue[0]?.shown,
});

test('legacy reducer: undefined waits for a trusted view; loaded empty history animates its first reply', () => {
  const waiting = sync(createPlaySpeechState(scope), undefined);
  assert.equal(waiting.initialized, false);
  const empty = sync(waiting, []);
  assert.equal(empty.initialized, true);
  const first = sync(empty, [entry(3)]);
  assert.equal(first.queue[0].shown, 1);
  assert.deepEqual(pendingSpeechSequences(first), [3]);
});

test('legacy reducer: restored nonempty history is visible without replay; only later sequences queue', () => {
  const historical = sync(createPlaySpeechState(scope), [entry(8), entry(12)]);
  assert.deepEqual(historical.queue, []);
  assert.strictEqual(sync(historical, [entry(8), entry(12)]), historical);
  assert.deepEqual(pendingSpeechSequences(sync(historical, [entry(8), entry(12), entry(14)])), [14]);
});

test('legacy reducer: line splitting respects existing paragraphs and Unicode code points', () => {
  assert.deepEqual(splitSpeechLines('甲'.repeat(24) + '乙\n丙'), ['甲'.repeat(24), '乙', '丙']);
  assert.deepEqual(splitSpeechLines('甲'.repeat(23) + '😀乙'), ['甲'.repeat(23) + '😀', '乙']);
  assert.equal(logic.SPEECH_LINE_DELAY_MS, 5000);
});

test('legacy reducer: new replies queue by sequence and only one line advances per tick', () => {
  let state = sync(baseline(), [entry(7), entry(5)]);
  assert.deepEqual(state.queue.map(item => [item.entry.sequence, item.shown]), [[5, 1], [7, 0]]);
  state = tick(state);
  assert.deepEqual(state.queue.map(item => item.shown), [2, 0]);
  state = tick(state);
  assert.deepEqual(pendingSpeechSequences(state), [7]);
  assert.equal(state.queue[1].shown, 0);
  state = tick(state);
  assert.deepEqual(state.queue.map(item => [item.entry.sequence, item.shown]), [[7, 1]]);
});

test('legacy reducer: a batch of one-line replies still waits one interval between replies', () => {
  let state = sync(baseline(), [entry(1, '一'), entry(2, '二'), entry(3, '三')]);
  assert.deepEqual(pendingSpeechSequences(state), [2, 3]);
  state = tick(state);
  assert.deepEqual(pendingSpeechSequences(state), [3]);
  state = tick(state);
  assert.deepEqual(state.queue, []);
});

test('legacy reducer: causal memories remain pending until their full utterance is visible', () => {
  let state = sync(baseline(), [entry(2)]);
  assert.deepEqual(pendingSpeechSequences(state), [2]);
  state = tick(state);
  assert.deepEqual(pendingSpeechSequences(state), [2]);
  state = tick(state);
  assert.deepEqual(pendingSpeechSequences(state), []);
});

test('legacy reducer: pause and resume preserve progress; appending and refreshing do not restart the head', () => {
  let state = tick(sync(baseline(), [entry(2)]));
  state = playSpeechReducer(state, { type: 'pause', scope });
  assert.strictEqual(tick(state), state);
  const next = sync(state, [entry(2), entry(4)]);
  assert.equal(next.queue[0].shown, 2);
  assert.equal(next.paused, true);
  assert.strictEqual(sync(next, [entry(2), entry(4)]), next);
  state = playSpeechReducer(next, { type: 'resume', scope });
  assert.deepEqual(pendingSpeechSequences(tick(state)), [4]);
});

test('legacy reducer: skip reveals every pending entry, releases grants, and does not replay on refresh', () => {
  let state = sync(baseline(), [entry(2), entry(4)]);
  state = playSpeechReducer(state, { type: 'skip', scope });
  assert.deepEqual(pendingSpeechSequences(state), []);
  assert.deepEqual(sync(state, [entry(2), entry(4)]).queue, []);
  assert.deepEqual(pendingSpeechSequences(sync(state, [entry(2), entry(4), entry(6)])), [6]);
});

test('legacy reducer: reduced motion immediately reveals pending and future entries', () => {
  let state = sync(baseline(), [entry(2)]);
  state = sync(state, [entry(2)], true);
  assert.deepEqual(state.queue, []);
  assert.deepEqual(sync(state, [entry(2), entry(4)], true).queue, []);
});

test('legacy reducer: loading preserves progress and stale snapshots cannot lower the historical watermark', () => {
  const state = tick(sync(baseline(), [entry(10)]));
  assert.strictEqual(sync(state, undefined), state);
  const older = sync(state, [entry(2)]);
  assert.equal(older.seenThrough, 10);
  assert.deepEqual(sync(older, [entry(2), entry(10)]).queue, []);
});

test('legacy reducer: scope changes reset history; stale timers and controls cannot affect the new scope', () => {
  const previous = sync(baseline(), [entry(2, '旧角色秘密'.repeat(20))]);
  const next = syncPlaySpeech(previous, 'other-play', undefined);
  assert.equal(next.initialized, false);
  assert.deepEqual(next.queue, []);
  assert.strictEqual(playSpeechReducer(next, { type: 'tick', scope, id: 'speech-2', shown: 1 }), next);
  assert.strictEqual(playSpeechReducer(next, { type: 'skip', scope }), next);
  assert.deepEqual(syncPlaySpeech(next, 'other-play', [entry(2)]).queue, []);
});

test('legacy reducer: a replaced or removed entry cannot keep old text queued', () => {
  const previous = sync(baseline(), [entry(2, '旧内容'.repeat(20))]);
  const next = sync(previous, [entry(2, '校验后的当前内容')]);
  assert.deepEqual(next.queue, []);
});

function playback(entries, nextScope = scope) {
  const { usePlaySpeech } = compile('../src/components/PlaySpeech.tsx', name => {
    if (name === '../lib/playSpeech') return logic;
    return require(name);
  });
  return usePlaySpeech(nextScope, entries);
}

test('current authorized replies render fully on the first render without waiting or playback controls', () => {
  const first = entry(2, '甲'.repeat(24) + '完整后文\n第二段😀');
  const second = entry(4, '同一响应的下一条');
  const view = playback([first, second]);
  const html = renderToStaticMarkup(React.createElement(React.Fragment, null, view.render(first), view.render(second)));
  assert.deepEqual(view.pendingSequences, []);
  assert.match(html, /完整后文\n第二段😀/);
  assert.match(html, /同一响应的下一条/);
  assert.doesNotMatch(html, /暂停播放|继续播放|全部显示|等待前面的发言|发言显示控制/);
  const restored = playback([first, second]);
  assert.equal(renderToStaticMarkup(restored.render(first)), renderToStaticMarkup(view.render(first)));
});

test('appended replies are complete immediately and the compatibility skip never grants unseen entries', () => {
  const first = entry(2, '完整回答'.repeat(50));
  const next = entry(4, '随后回答'.repeat(50));
  const before = playback([first]);
  before.skipAll();
  assert.equal(before.render(next), null);
  assert.deepEqual(before.pendingSequences, []);
  const after = playback([first, next]);
  assert.match(renderToStaticMarkup(after.render(next)), new RegExp(next.text));
  assert.deepEqual(after.pendingSequences, []);
});

test('render uses only current trusted entries and escapes text as text', () => {
  const old = entry(2, '其他角色旧秘密'.repeat(20));
  assert.equal(playback(undefined).render(old), null);
  assert.equal(playback([]).render(old), null);
  const current = entry(2, '<img src=x onerror=leak()>');
  const view = playback([current], 'other-play');
  assert.equal(view.render(old), null);
  assert.equal(view.render({ ...current, id: 'other-id' }), null);
  assert.equal(view.render({ ...current, sequence: 3 }), null);
  const html = renderToStaticMarkup(view.render(current));
  assert.doesNotMatch(html, /<img|其他角色旧秘密/);
  assert.match(html, /&lt;img/);
  assert.equal(playback(undefined, 'third-play').render(current), null);
  assert.deepEqual(playback(undefined, 'third-play').pendingSequences, []);
});

test('render schedules no timers or subscriptions and needs no React playback state', t => {
  const calls = [];
  t.mock.method(global, 'setTimeout', (...args) => calls.push(args));
  t.mock.method(global, 'setInterval', (...args) => calls.push(args));
  const view = playback([entry(2), entry(4)]);
  renderToStaticMarkup(view.render(entry(2)));
  view.skipAll();
  assert.deepEqual(calls, []);
  assert.deepEqual(view.pendingSequences, []);
});
