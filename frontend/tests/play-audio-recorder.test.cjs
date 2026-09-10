const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const ts = require('typescript');

const recorderSource = fs.readFileSync(path.join(__dirname, '../src/lib/playAudioRecorder.ts'), 'utf8');
const workletSource = fs.readFileSync(path.join(__dirname, '../public/audio/pcm-recorder-worklet.js'), 'utf8');
const compiled = ts.transpileModule(recorderSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const settle = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const tone = (length, rate = 16000, amplitude = 0.25) => Float32Array.from({ length }, (_, i) => amplitude * Math.sin(2 * Math.PI * 400 * i / rate));

function processor(rate = 16000, callback = () => {}) {
  let Processor;
  class Base {
    constructor() {
      this.port = { onmessage: null, postMessage: data => callback(structuredClone(data)) };
    }
  }
  vm.runInNewContext(workletSource, { AudioWorkletProcessor: Base, registerProcessor: (name, value) => {
    assert.equal(name, 'play-pcm-recorder'); Processor = value;
  }, sampleRate: rate, Float32Array, Int16Array, ArrayBuffer });
  return new Processor();
}

function harness(config = {}) {
  const timers = new Map(), errors = [], progress = [], contexts = [], nodes = [];
  let timerId = 0, micRequests = 0, constraints, limits = 0;
  class Track extends EventTarget {
    constructor() { super(); this.readyState = 'live'; this.stops = 0; }
    stop() { this.stops++; this.readyState = 'ended'; }
    end() { this.readyState = 'ended'; this.dispatchEvent(new Event('ended')); }
  }
  const track = new Track();
  const stream = { getTracks: () => [track], getAudioTracks: () => [track] };
  class Context {
    constructor(options) {
      this.options = options; this.state = 'suspended'; this.destination = {};
      this.closed = 0; this.sourceDisconnected = 0; this.onstatechange = null;
      this.audioWorklet = config.noWorklet ? undefined : { addModule: url => {
        assert.equal(url, '/audio/pcm-recorder-worklet.js'); return config.module?.promise || Promise.resolve();
      } };
      contexts.push(this);
    }
    resume() { this.state = 'running'; return Promise.resolve(); }
    close() { this.closed++; this.state = 'closed'; this.onstatechange?.(); return Promise.resolve(); }
    createMediaStreamSource(value) {
      assert.strictEqual(value, stream);
      return { connect: () => {}, disconnect: () => { this.sourceDisconnected++; } };
    }
    suspendExternally() { this.state = 'suspended'; this.onstatechange?.(); }
  }
  class WorkletNode {
    constructor(context, name, options) {
      assert.equal(name, 'play-pcm-recorder'); assert.equal(options.channelCount, 1);
      this.disconnected = 0; this.portClosed = 0;
      this.port = { onmessage: null, onmessageerror: null,
        postMessage: data => { if (!config.noFlush) queueMicrotask(() => this.processor.port.onmessage?.({ data })); },
        close: () => { this.portClosed++; },
      };
      this.processor = processor(config.rate || 16000, data => queueMicrotask(() => this.port.onmessage?.({ data })));
      nodes.push(this);
    }
    connect() {}
    disconnect() { this.disconnected++; }
    feed(samples, chunkSize = 128, other) {
      for (let i = 0; i < samples.length; i += chunkSize) {
        const output = new Float32Array(Math.min(chunkSize, samples.length - i)).fill(1);
        this.processor.process([[samples.subarray(i, i + chunkSize), ...(other ? [other.subarray(i, i + chunkSize)] : [])]], [[output]]);
        assert.ok(output.every(value => value === 0), 'microphone must never play through speakers');
      }
    }
  }
  const controller = new AbortController();
  const sandbox = { exports: {}, window: { isSecureContext: config.secure !== false },
    navigator: { mediaDevices: { getUserMedia: value => {
      micRequests++; constraints = value;
      return config.permission?.promise || (config.denied ? Promise.reject(config.denied) : Promise.resolve(stream));
    } } }, AudioContext: config.unsupported ? undefined : Context, AudioWorkletNode: WorkletNode,
    Blob, ArrayBuffer, DataView, Int16Array, Error,
    setTimeout: (fn, ms) => { const id = ++timerId; timers.set(id, { fn, ms }); return id; },
    clearTimeout: id => timers.delete(id),
  };
  vm.runInNewContext(compiled, sandbox);
  return {
    ...sandbox.exports, controller, track, stream, contexts, nodes, errors, progress, timers,
    get micRequests() { return micRequests; }, get constraints() { return constraints; }, get limits() { return limits; },
    start: (extra = {}) => sandbox.exports.startPlayAudioRecording({ ...extra, signal: controller.signal,
      onProgress: seconds => progress.push(seconds), onLimit: () => { limits++; }, onError: error => errors.push(error) }),
    fire: ms => { for (const [id, entry] of [...timers]) if (entry.ms === ms) { timers.delete(id); entry.fn(); } },
  };
}

function released(h) {
  assert.equal(h.track.readyState, 'ended');
  assert.ok(h.contexts.every(context => context.closed >= 1));
  assert.ok(h.nodes.every(node => node.disconnected >= 1 && node.portClosed >= 1 && node.port.onmessage === null));
  assert.equal(h.timers.size, 0);
}

test('module import has no microphone side effect; unavailable contexts fail without asking permission', async () => {
  for (const [options, code] of [[{ secure: false }, 'INSECURE_CONTEXT'], [{ unsupported: true }, 'UNSUPPORTED'], [{ noWorklet: true }, 'UNSUPPORTED']]) {
    const h = harness(options); assert.equal(h.micRequests, 0);
    await assert.rejects(h.start(), { code }); assert.equal(h.micRequests, 0);
    assert.ok(h.contexts.every(context => context.closed === 1));
  }
});

test('pre-aborted start allocates nothing and permission denial is friendly and releases context', async () => {
  const cancelled = harness(); cancelled.controller.abort();
  await assert.rejects(cancelled.start(), { code: 'CANCELLED' }); assert.equal(cancelled.micRequests, 0);
  const h = harness({ denied: Object.assign(new Error('private device detail'), { name: 'NotAllowedError' }) });
  await assert.rejects(h.start(), error => error.code === 'PERMISSION_DENIED' && !error.message.includes('private'));
  assert.equal(h.contexts[0].closed, 1); assert.equal(h.errors.length, 0);
});

test('abort rejects while permission is unanswered and disposes a later granted microphone', async () => {
  const permission = deferred(), h = harness({ permission });
  const starting = h.start(); h.controller.abort();
  await assert.rejects(starting, { code: 'CANCELLED' }); assert.equal(h.contexts[0].closed, 1);
  permission.resolve(h.stream); await settle();
  released(h); assert.equal(h.nodes.length, 0); assert.equal(h.errors.length, 0);
});

test('abort during worklet loading releases existing tracks and cannot start after a late module', async () => {
  const workletLoad = deferred(), h = harness({ module: workletLoad });
  const starting = h.start(); await settle(); h.controller.abort();
  await assert.rejects(starting, { code: 'CANCELLED' }); released(h);
  workletLoad.resolve(); await settle(); assert.equal(h.nodes.length, 0);
});

test('failed module also disposes microphone permission granted after the failure', async () => {
  const workletLoad = deferred(), permission = deferred(), h = harness({ module: workletLoad, permission });
  const starting = h.start(); workletLoad.reject(new Error('module unavailable'));
  await assert.rejects(starting, { code: 'RECORDING_FAILED' });
  permission.resolve(h.stream); await settle(); released(h); assert.equal(h.nodes.length, 0);
});

test('stop flushes the final partial chunk and returns canonical 44-byte-header mono PCM16LE WAV', async () => {
  const h = harness(), recording = await h.start();
  assert.equal(h.constraints.video, false); assert.equal(h.constraints.audio.channelCount, 1);
  const samples = tone(4801); h.nodes[0].feed(samples, 117); await settle();
  const stopping = recording.stop(); assert.strictEqual(recording.stop(), stopping);
  const blob = await stopping, bytes = Buffer.from(await blob.arrayBuffer());
  assert.equal(blob.type, 'audio/wav'); assert.equal(bytes.length, 44 + samples.length * 2);
  assert.equal(bytes.toString('ascii', 0, 4), 'RIFF'); assert.equal(bytes.readUInt32LE(4), bytes.length - 8);
  assert.equal(bytes.toString('ascii', 8, 16), 'WAVEfmt '); assert.equal(bytes.readUInt32LE(16), 16);
  assert.equal(bytes.readUInt16LE(20), 1); assert.equal(bytes.readUInt16LE(22), 1);
  assert.equal(bytes.readUInt32LE(24), 16000); assert.equal(bytes.readUInt32LE(28), 32000);
  assert.equal(bytes.readUInt16LE(32), 2); assert.equal(bytes.readUInt16LE(34), 16);
  assert.equal(bytes.toString('ascii', 36, 40), 'data'); assert.equal(bytes.readUInt32LE(40), samples.length * 2);
  for (const i of [1, 10, 20, 3000, 4800]) assert.equal(bytes.readInt16LE(44 + i * 2), Math.round(samples[i] * (samples[i] < 0 ? 32768 : 32767)) || 0);
  released(h); assert.equal(h.errors.length, 0); assert.equal(h.limits, 0);
});

test('short and silent recordings reject without retaining microphone resources', async () => {
  for (const [samples, code] of [[tone(3199), 'TOO_SHORT'], [new Float32Array(4000), 'SILENT'], [tone(4000, 16000, 0.00001), 'SILENT']]) {
    const h = harness(), recording = await h.start(); h.nodes[0].feed(samples);
    await assert.rejects(recording.stop(), { code }); released(h);
  }
  const h = harness(), recording = await h.start(); h.nodes[0].feed(tone(3200));
  assert.equal((await recording.stop()).size, 6444); released(h);
});

test('cancel during capture or flushing rejects and discards even already queued audio', async () => {
  for (const flush of [false, true]) {
    const h = harness(), recording = await h.start(); h.nodes[0].feed(tone(4800));
    const stopping = flush ? recording.stop() : undefined;
    recording.cancel(); recording.cancel();
    await assert.rejects(stopping || recording.stop(), { code: 'CANCELLED' }); await settle();
    released(h); assert.equal(h.errors.length, 0); assert.equal(h.limits, 0);
  }
});

test('signal abort during a recording tears down resources and suppresses late callbacks', async () => {
  const h = harness(), recording = await h.start();
  h.nodes[0].feed(tone(4800)); h.controller.abort(); await settle();
  await assert.rejects(recording.stop(), { code: 'CANCELLED' }); released(h);
  assert.deepEqual(h.progress, [0]); assert.equal(h.errors.length, 0);
});

test('track ending, processor errors, message errors and interrupted context fail once and release everything', async () => {
  for (const kind of ['track', 'processor', 'message', 'context']) {
    const h = harness(), recording = await h.start();
    if (kind === 'track') h.track.end();
    if (kind === 'processor') h.nodes[0].onprocessorerror();
    if (kind === 'message') h.nodes[0].port.onmessageerror();
    if (kind === 'context') h.contexts[0].suspendExternally();
    const code = kind === 'track' ? 'DEVICE_UNAVAILABLE' : 'RECORDING_FAILED';
    await assert.rejects(recording.stop(), { code }); released(h);
    assert.equal(h.errors.length, 1); assert.equal(h.errors[0].code, code);
  }
});

test('a missing worklet stop acknowledgment fails boundedly instead of returning truncated audio', async () => {
  const h = harness({ noFlush: true }), recording = await h.start();
  h.nodes[0].feed(tone(4800)); await settle(); const stopping = recording.stop();
  assert.equal(h.track.readyState, 'ended'); h.fire(1000);
  await assert.rejects(stopping, { code: 'RECORDING_FAILED' }); released(h);
});

test('wall-clock limit stops input without host cooperation and notifies only once', async () => {
  const h = harness(), recording = await h.start(); h.nodes[0].feed(tone(4800)); await settle();
  h.fire(60000); assert.equal(h.track.readyState, 'ended'); assert.equal(h.limits, 1);
  await settle(); assert.equal((await recording.stop()).size, 9644); released(h);
  h.fire(60000); assert.equal(h.limits, 1);
});

test('sample-count cap is exactly 60 seconds, even if main-thread wall timer is delayed', async () => {
  const h = harness(), recording = await h.start();
  h.nodes[0].feed(tone(16000 * 61), 997); await settle();
  assert.equal(h.limits, 1); assert.equal(h.progress.at(-1), 60);
  const blob = await recording.stop(); assert.equal(blob.size, 44 + 16000 * 60 * 2); released(h);
});

test('unexpected worklet payloads cannot allocate unbounded recorded audio', async () => {
  for (const data of [{ type: 'data', samples: new ArrayBuffer(2050) }, { type: 'data', samples: new ArrayBuffer(3) }, { type: 'other' }]) {
    const h = harness(), recording = await h.start(); h.nodes[0].port.onmessage({ data });
    await assert.rejects(recording.stop(), { code: 'RECORDING_FAILED' }); released(h);
  }
});

test('resampling preserves duration across noninteger rates and variable render blocks', () => {
  for (const rate of [8000, 16000, 44100, 48000]) {
    const messages = [], p = processor(rate, data => messages.push(data)), input = tone(rate, rate);
    for (let i = 0; i < input.length; i += 113) p.process([[input.subarray(i, i + 113)]], [[new Float32Array(Math.min(113, input.length - i))]]);
    p.port.onmessage({ data: { type: 'stop' } });
    const samples = messages.filter(data => data.type === 'data').flatMap(data => [...new Int16Array(data.samples)]);
    assert.equal(samples.length, 16000); assert.ok(Math.max(...samples) > 7000 && Math.min(...samples) < -7000);
    assert.equal(messages.at(-1).type, 'stopped');
  }
});

test('worklet downmixes channels, clips PCM safely and never outputs microphone sound', () => {
  const messages = [], p = processor(16000, data => messages.push(data));
  const output = new Float32Array(4).fill(5);
  p.process([[new Float32Array([2, -2, 1, 0.5]), new Float32Array([2, -2, -1, 0.5])]], [[output]]);
  p.port.onmessage({ data: { type: 'stop' } });
  assert.deepEqual([...new Int16Array(messages[0].samples)], [32767, -32768, 0, 16384]);
  assert.deepEqual([...output], [0, 0, 0, 0]);
  assert.equal(p.process([[]], [[output]]), false);
});

test('PCM stream receives each captured sample including stop flush exactly once', async()=>{
  const h=harness(),chunks=[];const recorder=await h.start({onPcmChunk:pcm=>chunks.push(new Int16Array(pcm))});
  h.nodes[0].feed(tone(3501));await settle();assert.equal(chunks.reduce((n,c)=>n+c.length,0),3072);
  const blob=await recorder.stop();const wav=new Int16Array((await blob.arrayBuffer()).slice(44));
  assert.equal(chunks.reduce((n,c)=>n+c.length,0),3501);assert.deepEqual(Array.from(wav),chunks.flatMap(c=>Array.from(c)));released(h);
});
