import authService from '@/services/authService';
import { config } from '@/stores/configStore';
import { request, result, SpeechInputError, type SpeechInputChannel, type SpeechInputResult } from '@/services/packageSpeechInputService';

type Options = {
  playId: string; requestId: string; revision: number; channel: SpeechInputChannel; callId?: string;
  signal: AbortSignal; onReady: () => void; onTranscript: (text: string, confirmed: string) => void;
};
export type RealtimeSpeech = { push: (pcm: ArrayBuffer) => void; stop: () => void; cancel: () => void; result: Promise<SpeechInputResult> };
const failure = () => new SpeechInputError(502, '实时转写连接已中断。可以检查本次结果，或继续输入文字。');

/** One connection, bounded in-memory PCM queue, no replay or automatic fallback. */
export function startRealtimeSpeech(options: Options): RealtimeSpeech {
  const token = authService.getToken();
  let socket: WebSocket | undefined, ready = false, stopping = false, ended = false;
  let queued: ArrayBuffer[] = [], queuedBytes = 0, totalBytes = 0;
  let resolve!: (data: SpeechInputResult) => void, reject!: (cause: unknown) => void;
  const completion = new Promise<SpeechInputResult>((a, b) => { resolve = a; reject = b; });
  let timer = setTimeout(() => fail(failure()), 15000);
  const controller = new AbortController();
  const identity = () => !options.signal.aborted && token && authService.getToken() === token;
  const cleanup = () => {
    ended = true; clearTimeout(timer); queued = []; queuedBytes = 0;
    controller.abort(); options.signal.removeEventListener('abort', cancel);
    if (socket) {
      socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null;
      socket.close();
    }
  };
  function fail(cause: unknown) { if (!ended) { cleanup(); reject(cause); } }
  function cancel() { fail(new SpeechInputError(409, '本次语音输入已取消，原文字草稿仍保留。')); }
  function send(value: ArrayBuffer | string) {
    if (!identity() || !socket || socket.readyState !== WebSocket.OPEN || socket.bufferedAmount > 160000) throw failure();
    socket.send(value);
  }
  const finish = () => {
    send(JSON.stringify({ type: 'STOP' }));
    clearTimeout(timer); timer = setTimeout(() => fail(failure()), 50000);
  };
  options.signal.addEventListener('abort', cancel, { once: true });
  void (async () => {
    try {
      if (!identity()) throw new SpeechInputError(401, '请重新登录后使用语音输入。');
      const path = `/package-plays/${encodeURIComponent(options.playId)}/speech-stream/${encodeURIComponent(options.requestId)}`;
      const query = new URLSearchParams({ expected_revision: String(options.revision), channel: options.channel });
      if (options.channel === 'PRIVATE' && options.callId) query.set('call_id', options.callId);
      const ticket = await request(`${path}/ticket?${query}`, { method: 'POST', signal: controller.signal }) as { ticket: string; request_id: string; expires_in: number };
      if (ended) return;
      if (!identity() || ticket.request_id !== options.requestId || ticket.expires_in !== 30 || !/^[A-Za-z0-9_-]{43}$/.test(ticket.ticket)) throw failure();
      const url = new URL(`${config.api.baseUrl}/api/fusion${path}`);
      if (!['http:', 'https:'].includes(url.protocol)) throw failure();
      url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
      socket = new WebSocket(url.toString());
      socket.onopen = () => { try { send(JSON.stringify({ ticket: ticket.ticket })); } catch (cause) { fail(cause); } };
      socket.onerror = socket.onclose = () => fail(failure());
      socket.onmessage = event => {
        if (ended) return;
        try {
          if (!identity() || typeof event.data !== 'string' || event.data.length > 50000) throw failure();
          const data = JSON.parse(event.data);
          if (data.request_id !== options.requestId) throw failure();
          if (data.type === 'READY' && !ready) {
            ready = true; clearTimeout(timer); timer = setTimeout(() => fail(failure()), 110000);
            for (const chunk of queued) send(chunk);
            queued = []; queuedBytes = 0; options.onReady();
            if (stopping) finish();
          } else if (data.type === 'TRANSCRIPT' && ready) {
            if (typeof data.text !== 'string' || typeof data.confirmed_text !== 'string'
              || Array.from(data.text).length > 6000 || Array.from(data.confirmed_text).length > 6000) throw failure();
            options.onTranscript(data.text, data.confirmed_text);
          } else if (data.type === 'RESULT' && ready) {
            const receipt = result(data, options.requestId); cleanup(); resolve(receipt);
          } else throw failure();
        } catch (cause) { fail(cause instanceof SpeechInputError ? cause : failure()); }
      };
    } catch (cause) { fail(cause); }
  })();
  return {
    result: completion, cancel,
    push(pcm) {
      if (ended) return;
      try {
        if (stopping || !(pcm instanceof ArrayBuffer) || !pcm.byteLength || pcm.byteLength > 16384 || pcm.byteLength % 2
          || totalBytes + pcm.byteLength > 1920000) throw failure();
        totalBytes += pcm.byteLength;
        if (ready) send(pcm);
        else {
          if (!identity() || queuedBytes + pcm.byteLength > 160000) throw failure();
          queued.push(pcm.slice(0)); queuedBytes += pcm.byteLength;
        }
      } catch (cause) { fail(cause); }
    },
    stop() {
      if (ended || stopping) return;
      stopping = true;
      if (ready) { try { finish(); } catch (cause) { fail(cause); } }
    },
  };
}
