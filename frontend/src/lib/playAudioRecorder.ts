export const MAX_RECORDING_SECONDS = 60;
const SAMPLE_RATE = 16000;
const MAX_SAMPLES = SAMPLE_RATE * MAX_RECORDING_SECONDS;
const MIN_SAMPLES = SAMPLE_RATE / 5;

export type PlayAudioRecordingErrorCode = 'UNSUPPORTED' | 'INSECURE_CONTEXT' | 'PERMISSION_DENIED'
  | 'DEVICE_UNAVAILABLE' | 'CANCELLED' | 'RECORDING_FAILED' | 'TOO_SHORT' | 'SILENT';

const messages: Record<PlayAudioRecordingErrorCode, string> = {
  UNSUPPORTED: '当前浏览器不支持这种录音方式，请换用新版浏览器，或继续键盘输入。',
  INSECURE_CONTEXT: '语音输入需要 HTTPS 或本机安全页面，请继续键盘输入。',
  PERMISSION_DENIED: '未获得麦克风权限，请在浏览器中允许麦克风后重新点击录音。',
  DEVICE_UNAVAILABLE: '没有可用的麦克风，或麦克风正被其他应用占用。',
  CANCELLED: '录音已取消。',
  RECORDING_FAILED: '录音已中断，请检查麦克风后重新录音，或继续键盘输入。',
  TOO_SHORT: '录音太短，请按下录音后说完一句话再停止。',
  SILENT: '没有录到清晰声音，请靠近麦克风后重新录音。',
};

export class PlayAudioRecordingError extends Error {
  constructor(public readonly code: PlayAudioRecordingErrorCode) {
    super(messages[code]);
    this.name = 'PlayAudioRecordingError';
  }
}

export interface PlayAudioRecording {
  stop(): Promise<Blob>;
  cancel(): void;
}

export interface PlayAudioRecordingOptions {
  signal: AbortSignal;
  onProgress: (seconds: number) => void;
  onLimit: () => void;
  /** Runtime device/processor failures. Startup and invalid audio reject their promises. */
  onError?: (error: PlayAudioRecordingError) => void;
  /** Validated PCM16 mono 16kHz chunks, including the last stop-time flush. */
  onPcmChunk?: (samples: ArrayBuffer) => void;
}

function audioError(cause: unknown): PlayAudioRecordingError {
  if (cause instanceof PlayAudioRecordingError) return cause;
  const name = cause instanceof Error ? cause.name : '';
  if (name === 'NotAllowedError' || name === 'SecurityError') return new PlayAudioRecordingError('PERMISSION_DENIED');
  if (['NotFoundError', 'NotReadableError', 'OverconstrainedError'].includes(name)) return new PlayAudioRecordingError('DEVICE_UNAVAILABLE');
  return new PlayAudioRecordingError('RECORDING_FAILED');
}

function wav(chunks: Int16Array[], count: number): Blob {
  const buffer = new ArrayBuffer(44 + count * 2);
  const data = new DataView(buffer);
  const ascii = (offset: number, value: string) => {
    for (let i = 0; i < value.length; i++) data.setUint8(offset + i, value.charCodeAt(i));
  };
  ascii(0, 'RIFF'); data.setUint32(4, buffer.byteLength - 8, true); ascii(8, 'WAVE');
  ascii(12, 'fmt '); data.setUint32(16, 16, true); data.setUint16(20, 1, true);
  data.setUint16(22, 1, true); data.setUint32(24, SAMPLE_RATE, true);
  data.setUint32(28, SAMPLE_RATE * 2, true); data.setUint16(32, 2, true); data.setUint16(34, 16, true);
  ascii(36, 'data'); data.setUint32(40, count * 2, true);
  let offset = 44;
  for (const chunk of chunks) for (const sample of chunk) { data.setInt16(offset, sample, true); offset += 2; }
  return new Blob([buffer], { type: 'audio/wav' });
}

/** Only call from an explicit user action. No upload, recognition service or storage. */
export async function startPlayAudioRecording(options: PlayAudioRecordingOptions): Promise<PlayAudioRecording> {
  const { signal } = options;
  if (signal.aborted) throw new PlayAudioRecordingError('CANCELLED');
  if (typeof window === 'undefined') throw new PlayAudioRecordingError('UNSUPPORTED');
  if (!window.isSecureContext) throw new PlayAudioRecordingError('INSECURE_CONTEXT');
  if (!navigator.mediaDevices?.getUserMedia || typeof AudioContext === 'undefined' || typeof AudioWorkletNode === 'undefined') {
    throw new PlayAudioRecordingError('UNSUPPORTED');
  }

  let state: 'starting' | 'recording' | 'stopping' | 'stopped' | 'failed' = 'starting';
  let context: AudioContext | undefined;
  let stream: MediaStream | undefined;
  let source: MediaStreamAudioSourceNode | undefined;
  let node: AudioWorkletNode | undefined;
  let failure: PlayAudioRecordingError | undefined;
  let published = false;
  let chunks: Int16Array[] = [];
  let count = 0;
  let squareSum = 0;
  let limitTimer: ReturnType<typeof setTimeout> | undefined;
  let flushTimer: ReturnType<typeof setTimeout> | undefined;
  let stopPromise: Promise<Blob> | undefined;
  let resolveStop: ((blob: Blob) => void) | undefined;
  let rejectStop: ((cause: PlayAudioRecordingError) => void) | undefined;
  let rejectStart: (cause: PlayAudioRecordingError) => void = () => {};
  const interrupted = new Promise<never>((_, reject) => { rejectStart = reject; });
  // A view callback must not prevent resource cleanup if it throws during unmount.
  const notify = (callback: () => void) => { try { callback(); } catch { /* host callback */ } };
  const releaseInput = () => {
    clearTimeout(limitTimer);
    for (const track of stream?.getTracks() || []) {
      track.removeEventListener('ended', deviceEnded);
      try { track.stop(); } catch { /* continue releasing the other resources */ }
    }
    try { source?.disconnect(); } catch { /* already disconnected */ }
  };
  const cleanup = () => {
    releaseInput(); clearTimeout(flushTimer);
    signal.removeEventListener('abort', cancel);
    if (node) {
      node.onprocessorerror = null; node.port.onmessage = null; node.port.onmessageerror = null;
      try { node.disconnect(); } catch { /* already disconnected */ }
      try { node.port.close(); } catch { /* already closed */ }
    }
    if (context) {
      context.onstatechange = null;
      if (context.state !== 'closed') { try { void context.close().catch(() => {}); } catch { /* already closing */ } }
    }
  };
  const fail = (error: PlayAudioRecordingError, report = true) => {
    if (state === 'failed' || state === 'stopped') return;
    state = 'failed'; failure = error; chunks = []; cleanup();
    rejectStart(error); rejectStop?.(error);
    if (report && published) notify(() => options.onError?.(error));
  };
  function cancel() { fail(new PlayAudioRecordingError('CANCELLED'), false); }
  function deviceEnded() { fail(new PlayAudioRecordingError('DEVICE_UNAVAILABLE')); }
  const complete = () => {
    if (state !== 'stopping') return;
    state = 'stopped'; cleanup();
    // This is an amplitude floor, not a claim to detect speech or identify a speaker.
    const error = count < MIN_SAMPLES ? new PlayAudioRecordingError('TOO_SHORT')
      : squareSum / count < (32768 * 0.0005) ** 2 ? new PlayAudioRecordingError('SILENT') : undefined;
    if (error) rejectStop?.(error);
    else resolveStop?.(wav(chunks, count));
    chunks = [];
  };
  const stop = (): Promise<Blob> => {
    if (stopPromise) return stopPromise;
    stopPromise = new Promise<Blob>((resolve, reject) => { resolveStop = resolve; rejectStop = reject; });
    if (failure) rejectStop?.(failure);
    else {
      state = 'stopping'; releaseInput();
      // The port flush includes its last partial chunk. Never silently drop the tail on error.
      flushTimer = setTimeout(() => fail(new PlayAudioRecordingError('RECORDING_FAILED')), 1000);
      try { node!.port.postMessage({ type: 'stop' }); } catch { fail(new PlayAudioRecordingError('RECORDING_FAILED')); }
    }
    return stopPromise;
  };
  const limit = () => {
    if (state !== 'recording') return;
    // Stop the microphone immediately even if the host never calls stop().
    void stop().catch(() => {});
    notify(options.onLimit);
  };
  signal.addEventListener('abort', cancel, { once: true });

  try {
    // Resume before awaiting permission, while the initiating click still has activation.
    context = new AudioContext({ sampleRate: SAMPLE_RATE });
    if (!context.audioWorklet?.addModule) throw new PlayAudioRecordingError('UNSUPPORTED');
    const resumed = context.resume();
    const granted = navigator.mediaDevices.getUserMedia({ video: false, audio: { channelCount: 1, sampleRate: SAMPLE_RATE } }).then(value => {
      // Permission dialogs cannot be programmatically dismissed. Dispose a late grant.
      if (state !== 'starting' || signal.aborted) {
        for (const track of value.getTracks()) track.stop();
        throw failure || new PlayAudioRecordingError('CANCELLED');
      }
      stream = value;
      const tracks = value.getAudioTracks();
      if (!tracks.length || tracks.some(track => track.readyState !== 'live')) throw new PlayAudioRecordingError('DEVICE_UNAVAILABLE');
      for (const track of tracks) track.addEventListener('ended', deviceEnded);
    });
    await Promise.race([Promise.all([granted, resumed, context.audioWorklet.addModule('/audio/pcm-recorder-worklet.js')]), interrupted]);
    if (failure || signal.aborted) throw failure || new PlayAudioRecordingError('CANCELLED');
    if (context.state !== 'running') throw new PlayAudioRecordingError('RECORDING_FAILED');
    node = new AudioWorkletNode(context, 'play-pcm-recorder', { numberOfInputs: 1, numberOfOutputs: 1,
      outputChannelCount: [1], channelCount: 1, channelCountMode: 'explicit' });
    node.onprocessorerror = () => fail(new PlayAudioRecordingError('RECORDING_FAILED'));
    node.port.onmessageerror = () => fail(new PlayAudioRecordingError('RECORDING_FAILED'));
    node.port.onmessage = ({ data }) => {
      if (state !== 'recording' && state !== 'stopping') return;
      if (data?.type === 'stopped') { complete(); return; }
      if (data?.type === 'limit') { limit(); return; }
      if (data?.type !== 'data' || !(data.samples instanceof ArrayBuffer) || data.samples.byteLength % 2
        || data.samples.byteLength === 0 || data.samples.byteLength > 2048) {
        fail(new PlayAudioRecordingError('RECORDING_FAILED')); return;
      }
      const samples = new Int16Array(data.samples).slice(0, MAX_SAMPLES - count);
      if (samples.length) {
        chunks.push(samples); count += samples.length;
        for (const sample of samples) squareSum += sample * sample;
        if (options.onPcmChunk) {
          try { options.onPcmChunk(samples.buffer.slice(0)); }
          catch { fail(new PlayAudioRecordingError('RECORDING_FAILED')); return; }
        }
        if (state === 'recording') notify(() => options.onProgress(count / SAMPLE_RATE));
      }
      if (count === MAX_SAMPLES) limit();
    };
    context.onstatechange = () => {
      if (state === 'recording' && context?.state !== 'running') fail(new PlayAudioRecordingError('RECORDING_FAILED'));
    };
    source = context.createMediaStreamSource(stream!);
    source.connect(node); node.connect(context.destination); // Worklet output is always silence; no microphone feedback.
    state = 'recording'; published = true;
    limitTimer = setTimeout(limit, MAX_RECORDING_SECONDS * 1000);
    notify(() => options.onProgress(0));
    return { stop, cancel };
  } catch (cause) {
    const error = failure || audioError(cause);
    fail(error, false);
    // interrupted may have been rejected before reaching Promise.race (e.g. constructor failure).
    void interrupted.catch(() => {});
    throw error;
  }
}
