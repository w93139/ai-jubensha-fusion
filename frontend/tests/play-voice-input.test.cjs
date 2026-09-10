const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const React = require('react');

function compile(file, imports = require) {
  const module = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, file), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  new Function('require', 'module', 'exports', code)(imports, module, module.exports);
  return module.exports;
}
const elements = (tree, type) => Array.isArray(tree) ? tree.flatMap(x => elements(x, type)) : !React.isValidElement(tree) ? []
  : [...(tree.type === type ? [tree] : []), ...elements(tree.props.children, type)];
const button = (tree, label) => elements(tree, 'button').find(n => n.props.children === label);
const settle = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
let token = 'voice-account-a';
const serviceModule = compile('../src/services/packageSpeechInputService.ts', name => {
  if (name === '@/services/authService') return { default: { getToken: () => token } };
  if (name === '@/stores/configStore') return { config: { api: { baseUrl: 'https://voice.invalid' } } };
  return require(name);
});
const service = serviceModule.default;
const response = data => ({ ok: true, status: 200, json: async () => ({ success: true, data }) });
const result = (request_id, patch = {}) => ({ request_id, state: 'OK', text: '请核对书房里的线索。', error_code: null, ...patch });

test('speech service sends only audio and current input binding with authorization and no-store', async t => {
  const calls = [], audio = new Blob(['synthetic'], { type: 'audio/wav' });
  t.mock.method(global, 'fetch', async (url, options) => { calls.push({ url, options }); return response(result('request-a')); });
  const signal = new AbortController().signal;
  await service.transcribe('play/a?', 'request-a', audio, { revision: 4, channel: 'PRIVATE', callId: 'call/?' }, signal);
  assert.match(calls[0].url, /play%2Fa%3F\/speech-input\/request-a\?expected_revision=4&channel=PRIVATE&call_id=call%2F%3F$/);
  assert.equal(calls[0].options.body, audio); assert.equal(calls[0].options.signal, signal);
  assert.equal(calls[0].options.cache, 'no-store'); assert.equal(calls[0].options.headers.Authorization, 'Bearer voice-account-a');
  assert.equal(calls[0].options.headers['Content-Type'], 'audio/wav');
  await service.receipt('play/a?', 'request-a', signal);
  assert.equal(calls[1].options.body, undefined); assert.equal(calls[1].options.method, undefined);
  assert.doesNotMatch(calls[1].url, /call_id|expected_revision/);
});

test('capability checks are read-only and malformed limits or unrelated receipts are refused', async t => {
  t.mock.method(global, 'fetch', async () => response({ available: true, reason: null, max_seconds: 60, min_seconds: .2, max_characters: 1000 }));
  assert.equal((await service.capability('play', new AbortController().signal)).available, true);
  t.mock.restoreAll();
  for (const data of [result('other-id'), result('request-a', { state: 'OK', text: '' }), result('request-a', { state: 'PENDING', text: 'PRIVATE_SENTINEL' }), result('request-a', { text: '字'.repeat(6001) })]) {
    t.mock.method(global, 'fetch', async () => response(data));
    await assert.rejects(service.receipt('play', 'request-a', new AbortController().signal), error => error.status === 502 && !error.message.includes('PRIVATE_SENTINEL'));
    t.mock.restoreAll();
  }
});

test('no key or provider error payload is exposed and an identity change during decoding rejects text', async t => {
  t.mock.method(global, 'fetch', async () => ({ ok: false, status: 503, json: async () => { throw Error('must not read provider details'); } }));
  await assert.rejects(service.capability('play', new AbortController().signal), error => error.status === 503);
  t.mock.restoreAll();
  t.mock.method(global, 'fetch', async () => ({ ok: true, json: async () => { token = 'voice-account-b'; return { success: true, data: result('request-a') }; } }));
  try { await assert.rejects(service.receipt('play', 'request-a', new AbortController().signal), error => error.status === 401); }
  finally { token = 'voice-account-a'; }
});

function runner() {
  const slots = [], effects = [], pending = []; let index = 0;
  const hooks = {
    useState(initial) { const slot = index++; if (!(slot in slots)) slots[slot] = typeof initial === 'function' ? initial() : initial;
      return [slots[slot], value => { slots[slot] = typeof value === 'function' ? value(slots[slot]) : value; }]; },
    useRef(initial) { const slot = index++; if (!(slot in slots)) slots[slot] = { current: initial }; return slots[slot]; },
    useCallback(callback, deps) { const slot = index++; if (!slots[slot] || deps.some((v,i) => v !== slots[slot].deps[i])) slots[slot] = { callback, deps }; return slots[slot].callback; },
    useEffect(callback, deps) { const slot = index++; if (!effects[slot] || deps.some((v,i) => v !== effects[slot].deps[i])) pending.push(() => {
      effects[slot]?.cleanup?.(); effects[slot] = { deps, cleanup: callback() };
    }); },
  };
  hooks.useLayoutEffect = hooks.useEffect;
  return { hooks, run(callback) { index = 0; const tree = callback(); while (pending.length) pending.shift()(); return tree; },
    close() { effects.forEach(e => e?.cleanup?.()); } };
}

function harness(overrides = {}) {
  const oldWindow = global.window, oldDocument = global.document;
  global.window = new EventTarget(); global.document = new EventTarget(); global.document.hidden = false;
  const hooks = runner(), calls = { capabilities: 0, starts: 0, cancels: 0, stops: 0, posts: [], receipts: [], changes: [] };
  let authCallback, authCurrent = true, captureOptions;
  const fake = {
    async capability() { calls.capabilities++; return { available: true }; },
    async transcribe(play, id, audio, context, signal) { calls.posts.push({ play, id, audio, context, signal }); return result(id); },
    async receipt(play, id) { calls.receipts.push({ play, id }); return result(id); },
  };
  const recorder = { async stop() { calls.stops++; return new Blob(['synthetic']); }, cancel() { calls.cancels++; } };
  const audio = { async startPlayAudioRecording(options) { calls.starts++; captureOptions = options; return recorder; }, PlayAudioRecordingError: class extends Error {} };
  const streamDone = deferred();
  const realtime = { startRealtimeSpeech(options) { calls.streamOptions = options; calls.streamStarts = (calls.streamStarts || 0)+1; return { result: streamDone.promise, push(pcm) { (calls.pcm ||= []).push(pcm); }, stop() { calls.streamStops = (calls.streamStops || 0)+1; }, cancel() { calls.streamCancels = (calls.streamCancels || 0)+1; } }; } };
  const Component = compile('../src/components/PlayVoiceInput.tsx', name => {
    if (name === 'react') return { ...React, ...hooks.hooks };
    if (name === '@/services/packageSpeechInputService') return { ...serviceModule, default: fake };
    if (name === '@/lib/playAudioRecorder') return audio;
    if (name === '@/services/packageRealtimeSpeechService') return realtime;
    if (name === '@/services/packagePlayService') return { watchPackagePlayAuth(callback) { authCallback = callback; return { isCurrent: () => authCurrent, dispose() {} }; } };
    return require(name);
  }).default;
  let props = { ownerId: 'owner-a', playId: 'play-a', phaseId: 'phase-a', revision: 4, channel: 'PUBLIC', value: '原有草稿', onChange: text => calls.changes.push(text), ...overrides };
  return { calls, fake, audio, recorder, streamDone, captureOptions: () => captureOptions,
    run: () => hooks.run(() => Component(props)), update: patch => { props = { ...props, ...patch }; },
    invalidateAuth() { authCurrent = false; authCallback(); }, hide() { global.document.hidden = true; global.document.dispatchEvent(new Event('visibilitychange')); },
    close() { hooks.close(); global.window = oldWindow; global.document = oldDocument; } };
}
async function open(h) { button(h.run(), '语音输入').props.onClick(); await settle(); assert.ok(button(h.run(), '开始录音')); }
async function record(h) { await open(h); button(h.run(), '开始录音').props.onClick(); await settle(); assert.ok(button(h.run(), '停止并转写')); }
async function transcribe(h) { await record(h); button(h.run(), '停止并转写').props.onClick(); await settle(); }

test('opening and explicit recording do not send speech; one stop returns editable review before append', async () => {
  const h = harness();
  try {
    h.run(); assert.equal(h.calls.capabilities, 0); assert.equal(h.calls.starts, 0);
    await open(h); assert.equal(h.calls.starts, 0);
    const start = button(h.run(), '开始录音'); start.props.onClick(); start.props.onClick(); await settle();
    assert.equal(h.calls.starts, 1);
    const stop = button(h.run(), '停止并转写'); stop.props.onClick(); stop.props.onClick(); await settle();
    assert.equal(h.calls.stops, 1); assert.equal(h.calls.posts.length, 1); assert.deepEqual(h.calls.changes, []);
    h.update({ value: '录音期间手动补充的草稿' });
    elements(h.run(), 'textarea')[0].props.onChange({ target: { value: '校对后的识别文字' } });
    button(h.run(), '填入草稿').props.onClick();
    assert.deepEqual(h.calls.changes, ['录音期间手动补充的草稿\n校对后的识别文字']);
    assert.ok(button(h.run(), '语音输入')); assert.equal(h.calls.posts[0].context.channel, 'PUBLIC');
  } finally { h.close(); }
});

test('missing service configuration never requests microphone permission or changes draft', async () => {
  const h = harness(); h.fake.capability = async () => ({ available: false });
  try { button(h.run(), '语音输入').props.onClick(); await settle(); assert.ok(button(h.run(), '关闭')); assert.equal(h.calls.starts, 0); assert.equal(h.calls.posts.length, 0); assert.deepEqual(h.calls.changes, []); }
  finally { h.close(); }
});

test('voice disclosure follows the configured provider through recording and review', async () => {
  const h = harness(); h.fake.capability = async () => ({ available: true, provider_name: '千问' });
  try {
    await open(h);
    assert.equal(elements(h.run(), 'section')[0].props['aria-label'], '千问语音输入');
    button(h.run(), '开始录音').props.onClick(); await settle();
    button(h.run(), '停止并转写').props.onClick(); await settle();
    assert.equal(elements(h.run(), 'section')[0].props['aria-label'], '千问语音输入');
    assert.deepEqual(h.calls.changes, []);
    button(h.run(), '关闭').props.onClick();
    assert.equal(elements(h.run(), 'section')[0].props['aria-label'], '语音输入');
  } finally { h.close(); }
});

test('provider disclosure accepts legacy capability and rejects arbitrary provider text', async t => {
  for (const name of ['千问','豆包',null,undefined]) {
    t.mock.method(global, 'fetch', async () => response({ available:true, reason:null, max_seconds:60, min_seconds:.2, max_characters:1000, provider_name:name }));
    assert.equal((await service.capability('play',new AbortController().signal)).provider_name, name ?? null);
    t.mock.restoreAll();
  }
  t.mock.method(global, 'fetch', async () => response({ available:true, reason:null, max_seconds:60, min_seconds:.2, max_characters:1000, provider_name:'PRIVATE_DETAIL' }));
  await assert.rejects(service.capability('play',new AbortController().signal), error => error.status===502 && !error.message.includes('PRIVATE_DETAIL'));
});

test('opening the controls does not require secure-context UUID support', async t => {
  const h = harness();
  t.mock.method(global.crypto, 'randomUUID', () => { throw new TypeError('unavailable'); });
  try { await open(h); assert.equal(h.calls.starts, 0); button(h.run(), '取消').props.onClick(); assert.ok(button(h.run(), '语音输入')); }
  finally { h.close(); }
});

test('cancel while microphone permission is pending aborts and releases a late recorder', async () => {
  const h = harness(), pending = deferred(); let signal;
  h.audio.startPlayAudioRecording = options => { signal = options.signal; return pending.promise; };
  try {
    await open(h); button(h.run(), '开始录音').props.onClick();
    button(h.run(), '取消').props.onClick(); assert.equal(signal.aborted, true);
    pending.resolve(h.recorder); await settle(); assert.equal(h.calls.cancels, 1);
    assert.ok(button(h.run(), '语音输入')); assert.equal(h.calls.posts.length, 0);
  } finally { h.close(); }
});

test('page hiding or closing input stops capture without uploading', async () => {
  for (const cancel of [h => h.hide(), h => { h.update({ active: false }); h.run(); }]) {
    const h = harness();
    try { await record(h); cancel(h); assert.ok(h.calls.cancels > 0); assert.equal(h.captureOptions().signal.aborted, true); assert.equal(h.calls.posts.length, 0); }
    finally { h.close(); }
  }
});

test('account, game, revision, channel, call or lock changes discard late recognition text', async () => {
  for (const patch of [{ ownerId: 'owner-b' }, { playId: 'play-b' }, { revision: 5 }, { channel: 'PRIVATE' }, { callId: 'call-b' }, { disabled: true }]) {
    const h = harness(), pending = deferred(); h.fake.transcribe = async (...args) => { h.calls.posts.push(args); return pending.promise; };
    try {
      await record(h); button(h.run(), '停止并转写').props.onClick(); await settle();
      const id = h.calls.posts[0][1]; h.update(patch); assert.equal(elements(h.run(), 'textarea').length, 0);
      pending.resolve(result(id)); await settle(); assert.equal(elements(h.run(), 'textarea').length, 0); assert.deepEqual(h.calls.changes, []);
    } finally { h.close(); }
  }
});

test('auth changes discard already recognized text before it can be appended', async () => {
  const h = harness();
  try { await transcribe(h); const apply = button(h.run(), '填入草稿'); h.invalidateAuth(); apply.props.onClick(); assert.deepEqual(h.calls.changes, []); assert.equal(elements(h.run(), 'textarea').length, 0); }
  finally { h.close(); }
});

test('old callbacks cannot operate on a newly opened speech session, including the same game', async () => {
  for (const patch of [{}, { ownerId: 'owner-b', playId: 'play-b', value: '新场景草稿' }]) {
    const h = harness();
    try {
      const oldOpen = button(h.run(), '语音输入');
      await open(h); const oldStart = button(h.run(), '开始录音');
      oldStart.props.onClick(); await settle();
      const oldStop = button(h.run(), '停止并转写'); oldStop.props.onClick(); await settle();
      const oldApply = button(h.run(), '填入草稿'), oldEdit = elements(h.run(), 'textarea')[0], oldClose = button(h.run(), '关闭');
      oldClose.props.onClick(); h.update(patch); h.run();
      if (patch.ownerId) { oldOpen.props.onClick(); await settle(); assert.equal(h.calls.capabilities, 1); }
      await open(h); oldStart.props.onClick(); await settle(); assert.equal(h.calls.starts, 1);
      button(h.run(), '开始录音').props.onClick(); await settle();
      oldStop.props.onClick(); oldClose.props.onClick(); await settle();
      assert.equal(h.calls.stops, 1); assert.ok(button(h.run(), '停止并转写'));
      button(h.run(), '停止并转写').props.onClick(); await settle();
      oldEdit.props.onChange({ target: { value: '旧场景文字' } }); oldApply.props.onClick();
      assert.deepEqual(h.calls.changes, []); assert.notEqual(elements(h.run(), 'textarea')[0].props.value, '旧场景文字');
      button(h.run(), '填入草稿').props.onClick(); assert.equal(h.calls.changes.length, 1);
    } finally { h.close(); }
  }
});

test('lost transcription is checked with GET only and never automatically re-uploaded', async () => {
  const h = harness(); h.fake.transcribe = async (...args) => { h.calls.posts.push(args); throw new TypeError('lost'); };
  try {
    await transcribe(h); assert.equal(h.calls.posts.length, 1); assert.deepEqual(h.calls.receipts, []);
    const check = button(h.run(), '检查本次转写结果'); check.props.onClick(); check.props.onClick(); await settle();
    assert.equal(h.calls.receipts.length, 1); assert.equal(h.calls.posts.length, 1); assert.ok(button(h.run(), '填入草稿'));
  } finally { h.close(); }
});

test('timer limit stops once and overlong merged recognition is never truncated or appended', async () => {
  const h = harness({ value: '原'.repeat(999) });
  try {
    await record(h); h.captureOptions().onLimit(); h.captureOptions().onLimit(); await settle();
    assert.equal(h.calls.stops, 1); assert.equal(h.calls.posts.length, 1);
    let apply = button(h.run(), '填入草稿'); assert.equal(apply.props.disabled, true); apply.props.onClick(); assert.deepEqual(h.calls.changes, []);
    h.update({ value: '' }); elements(h.run(), 'textarea')[0].props.onChange({ target: { value: '😀'.repeat(1000) } });
    apply = button(h.run(), '填入草稿'); assert.equal(apply.props.disabled, false); apply.props.onClick(); assert.equal(Array.from(h.calls.changes[0]).length, 1000);
  } finally { h.close(); }
});

test('empty, failed and expired results preserve the player draft without sending', async () => {
  for (const state of ['EMPTY','FAILED','UNKNOWN','EXPIRED']) {
    const h = harness(); h.fake.transcribe = async (play,id) => result(id, { state, text: null, error_code: 'PRIVATE_PROVIDER_DETAIL' });
    try { await transcribe(h); assert.ok(button(h.run(), '关闭')); assert.deepEqual(h.calls.changes, []); assert.equal(elements(h.run(), 'textarea').length, 0); }
    finally { h.close(); }
  }
});

async function realtimeRecord(h) {
  h.fake.capability=async()=>({available:true,provider_name:'千问',realtime:true});
  await open(h);button(h.run(),'开始录音').props.onClick();await settle();
  assert.ok(button(h.run(),'停止录音'));assert.equal(h.calls.streamStarts,1);
}
test('realtime shows sentence replacement while recording; stop flush precedes final editable append',async()=>{
  const h=harness();try{
    await realtimeRecord(h);
    h.captureOptions().onPcmChunk(new Int16Array([1,2]).buffer);
    h.calls.streamOptions.onTranscript('正在','');h.calls.streamOptions.onTranscript('正在核对。','正在核对。');
    assert.equal(elements(h.run(),'textarea').length,0);assert.deepEqual(h.calls.changes,[]);
    h.recorder.stop=async()=>{h.calls.stops++;h.captureOptions().onPcmChunk(new Int16Array([3]).buffer);return new Blob(['tail']);};
    button(h.run(),'停止录音').props.onClick();await settle();
    assert.equal(h.calls.pcm.length,2);assert.equal(h.calls.streamStops,1);assert.equal(h.calls.posts.length,0);
    h.streamDone.resolve(result(h.calls.streamOptions.requestId,{text:'正在核对。'}));await settle();
    assert.equal(elements(h.run(),'textarea')[0].props.value,'正在核对。');
    button(h.run(),'填入草稿').props.onClick();assert.deepEqual(h.calls.changes,['原有草稿\n正在核对。']);
  }finally{h.close();}
});
test('realtime partial failure can be reviewed; cancellation and scope changes discard late updates',async()=>{
  const h=harness();try{await realtimeRecord(h);h.streamDone.resolve(result(h.calls.streamOptions.requestId,{state:'PARTIAL',text:'已完成句。'}));await settle();
    assert.equal(elements(h.run(),'textarea')[0].props.value,'已完成句。');assert.deepEqual(h.calls.changes,[]);assert.ok(h.calls.cancels);
  }finally{h.close();}
  for(const mode of ['cancel','auth','phase','hide']){
    const h=harness();try{await realtimeRecord(h);
      if(mode==='cancel')button(h.run(),'取消').props.onClick();
      if(mode==='auth')h.invalidateAuth();if(mode==='phase'){h.update({revision:5});h.run();}if(mode==='hide')h.hide();
      h.calls.streamOptions.onTranscript('旧识别文字','旧识别文字');h.streamDone.resolve(result(h.calls.streamOptions.requestId));await settle();
      assert.equal(elements(h.run(),'textarea').length,0);assert.deepEqual(h.calls.changes,[]);assert.ok(h.calls.streamCancels);
    }finally{h.close();}
  }
});
test('realtime permission denial never creates ticket or provider connection',async()=>{
  const h=harness();try{h.fake.capability=async()=>({available:true,realtime:true});h.audio.startPlayAudioRecording=async()=>{throw Error('denied');};
    await open(h);button(h.run(),'开始录音').props.onClick();await settle();assert.equal(h.calls.streamStarts,undefined);assert.deepEqual(h.calls.changes,[]);
  }finally{h.close();}
});

test('realtime device or stop-flush failure offers receipt recovery and keeps the original failure state',async()=>{
  for(const mode of ['device','flush']){
    const h=harness();try{
      await realtimeRecord(h);h.calls.streamOptions.onTranscript('已完成句。','已完成句。');
      if(mode==='device')h.captureOptions().onError(new Error('device interrupted'));
      else {h.recorder.stop=async()=>{throw Error('flush interrupted');};button(h.run(),'停止录音').props.onClick();await settle();}
      h.streamDone.reject(new serviceModule.SpeechInputError(409,'internal cancellation'));await settle();
      assert.ok(button(h.run(),'检查本次转写结果'));assert.deepEqual(h.calls.changes,[]);
      h.fake.receipt=async(play,id)=>result(id,{state:'PARTIAL',text:'已完成句。'});
      button(h.run(),'检查本次转写结果').props.onClick();await settle();
      assert.equal(elements(h.run(),'textarea')[0].props.value,'已完成句。');assert.deepEqual(h.calls.posts,[]);
    }finally{h.close();}
  }
});
