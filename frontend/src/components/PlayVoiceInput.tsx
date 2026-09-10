import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import speechInput, { SpeechInputError, type SpeechInputChannel, type SpeechInputResult } from '@/services/packageSpeechInputService';
import { watchPackagePlayAuth } from '@/services/packagePlayService';
import { startPlayAudioRecording, PlayAudioRecordingError } from '@/lib/playAudioRecorder';
import { startRealtimeSpeech, type RealtimeSpeech } from '@/services/packageRealtimeSpeechService';

type Recorder = Awaited<ReturnType<typeof startPlayAudioRecording>>;
type Stage = 'CHECKING' | 'READY' | 'REQUESTING' | 'RECORDING' | 'TRANSCRIBING' | 'REVIEW' | 'WAITING' | 'ERROR';
type Session = { id: number; scope: string; controller: AbortController; providerName?: string; recorder?: Recorder; requestId?: string; stopping?: boolean; recordingStarted?: boolean; checking?: boolean; realtime?: boolean; stream?: RealtimeSpeech; earlyPcm?: ArrayBuffer[]; earlyBytes?: number; failureHandled?: boolean };
// A document has one microphone owner, even with several speech inputs mounted.
let microphoneOwner: Session | undefined;
let nextSessionId = 0;
const buttonStyle = 'min-h-11 rounded-lg border border-brass/60 px-3 py-2 text-sm text-paper disabled:opacity-40';

export type PlayVoiceInputProps = {
  ownerId?: string | null; playId: string; phaseId: string; revision: number;
  channel: SpeechInputChannel; callId?: string; active?: boolean; disabled?: boolean;
  value: string; onChange: (text: string) => void;
};

export default function PlayVoiceInput(props: PlayVoiceInputProps) {
  const scope = JSON.stringify([props.ownerId, props.playId, props.phaseId, props.revision, props.channel, props.callId]);
  const enabled = Boolean(props.ownerId && props.active !== false && !props.disabled);
  const [state, setState] = useState<{ sessionId: number; scope: string; stage: Stage; message: string; seconds: number; text: string; confirmed?: string; providerName?: string; realtime?: boolean }>();
  const identity = JSON.stringify([scope, enabled]);
  const [previousIdentity, setPreviousIdentity] = useState(identity);
  if (previousIdentity !== identity) { setPreviousIdentity(identity); setState(undefined); }
  const run = useRef<Session | undefined>(undefined);
  const latest = useRef({ scope, enabled, value: props.value, onChange: props.onChange });
  useLayoutEffect(() => { latest.current = { scope, enabled, value: props.value, onChange: props.onChange }; }, [scope, enabled, props.value, props.onChange]);
  const auth = useRef<ReturnType<typeof watchPackagePlayAuth> | undefined>(undefined);
  const previewInput = useRef<HTMLTextAreaElement>(null);
  const opener = useRef<HTMLButtonElement>(null);

  const cleanup = useCallback(() => {
    const session = run.current;
    run.current = undefined;
    if (microphoneOwner === session) microphoneOwner = undefined;
    session?.controller.abort(); session?.recorder?.cancel(); session?.stream?.cancel();
    if (session) session.earlyPcm = [];
  }, []);
  const current = (session: Session) => run.current === session && !session.controller.signal.aborted
    && session.scope === scope && latest.current.enabled && latest.current.scope === session.scope && auth.current?.isCurrent() !== false;
  const show = (session: Session, stage: Stage, message = '', text = '') => {
    if (current(session)) setState(previous => ({ sessionId: session.id, scope: session.scope, stage, message,
      text: session.realtime && stage === 'TRANSCRIBING' ? previous?.text ?? '' : text,
      confirmed: stage === 'TRANSCRIBING' ? previous?.confirmed : undefined, realtime: session.realtime,
      providerName: session.providerName, seconds: previous?.sessionId === session.id ? previous.seconds : 0 }));
  };

  useEffect(() => {
    const guard = watchPackagePlayAuth(() => { cleanup(); setState(undefined); });
    auth.current = guard;
    const hidden = () => { if (document.hidden) { cleanup(); setState(undefined); } };
    const leave = () => { cleanup(); setState(undefined); };
    document.addEventListener('visibilitychange', hidden); window.addEventListener('pagehide', leave);
    return () => { guard.dispose(); cleanup(); document.removeEventListener('visibilitychange', hidden); window.removeEventListener('pagehide', leave); };
  }, [cleanup, scope, enabled]);

  const fail = (session: Session, cause: unknown, uploaded = false) => {
    if (!current(session) || session.failureHandled) return;
    session.failureHandled = true; session.stopping = true;
    session.recorder?.cancel(); session.recorder = undefined;
    session.stream?.cancel(); session.stream = undefined; session.earlyPcm = [];
    if (microphoneOwner === session) microphoneOwner = undefined;
    const denied = cause instanceof SpeechInputError && [401, 403, 404, 409, 413, 422, 429, 503].includes(cause.status);
    show(session, uploaded && !denied ? 'WAITING' : 'ERROR', cause instanceof SpeechInputError || cause instanceof PlayAudioRecordingError
      ? cause.message : uploaded ? '未能确认转写结果。请检查本次结果；不会自动重新上传录音。' : '无法开始语音输入，请检查麦克风权限，或继续输入文字。');
  };
  const accept = (session: Session, data: SpeechInputResult) => {
    if (!current(session)) return;
    session.recorder?.cancel(); session.recorder = undefined;
    session.stopping = true;
    if (microphoneOwner === session) microphoneOwner = undefined;
    if (data.state === 'OK' || data.state === 'PARTIAL') {
      show(session, 'REVIEW', data.state === 'PARTIAL' ? '转写已中断，已保留识别完整的句子。请校对后填入草稿。' : '请校对识别文字，再填入当前草稿。', data.text!);
      setTimeout(() => { if (current(session)) previewInput.current?.focus({ preventScroll: true }); }, 0);
    } else if (data.state === 'PENDING') show(session, 'WAITING', '这段语音仍在转写。稍后可检查本次结果，不会重新上传。');
    else show(session, 'ERROR', data.state === 'EMPTY' ? '没有识别到清晰语音，请靠近麦克风重新录制。'
      : data.state === 'EXPIRED' ? '本次识别稿已过期，请重新录制；原文字草稿仍保留。'
      : '本次转写未取得可用结果，请重新录制或继续输入文字。');
  };
  const open = async () => {
    if (!latest.current.enabled || latest.current.scope !== scope || auth.current?.isCurrent() === false || run.current) return;
    const session: Session = { id: ++nextSessionId, scope, controller: new AbortController() };
    run.current = session; show(session, 'CHECKING', '正在检查语音输入…');
    try {
      const capability = await speechInput.capability(props.playId, session.controller.signal);
      if (!current(session)) return;
      session.providerName = capability.provider_name ?? undefined;
      session.realtime = capability.realtime === true;
      show(session, capability.available ? 'READY' : 'ERROR', capability.available ? '' : '语音输入暂未就绪，请先使用文字输入。');
    } catch (cause) { fail(session, cause); }
  };
  const finish = async (session: Session) => {
    if (!current(session) || session.stopping || !session.recorder) return;
    session.stopping = true;
    show(session, 'TRANSCRIBING', session.realtime ? '正在整理最后一句…' : '正在转成文字，可以继续浏览资料…');
    let uploaded = false;
    try {
      const audio = await session.recorder.stop(); session.recorder = undefined;
      if (microphoneOwner === session) microphoneOwner = undefined;
      if (!current(session)) return;
      if (session.realtime) { session.stream?.stop(); return; }
      session.requestId = crypto.randomUUID(); uploaded = true;
      const data = await speechInput.transcribe(props.playId, session.requestId, audio,
        { revision: props.revision, channel: props.channel, callId: props.callId }, session.controller.signal);
      accept(session, data);
    } catch (cause) { fail(session, cause, uploaded || Boolean(session.realtime && session.requestId)); }
  };
  const record = async () => {
    const session = run.current;
    if (!session || session.id !== state?.sessionId || !current(session) || state?.stage !== 'READY' || session.recordingStarted) return;
    if (microphoneOwner) { show(session, 'READY', '请先结束其他输入处的录音。'); return; }
    microphoneOwner = session;
    session.recordingStarted = true;
    session.earlyPcm = []; session.earlyBytes = 0;
    show(session, 'REQUESTING', '请允许浏览器使用麦克风。');
    try {
      const recorder = await startPlayAudioRecording({ signal: session.controller.signal,
        onProgress: seconds => { if (current(session)) setState(value => value && ({ ...value, seconds })); },
        onLimit: () => { if (current(session)) void finish(session); },
        onError: cause => fail(session, cause, Boolean(session.realtime && session.requestId)),
        onPcmChunk: session.realtime ? pcm => {
          if (!current(session)) return;
          if (session.stream) session.stream.push(pcm);
          else {
            session.earlyBytes = (session.earlyBytes ?? 0) + pcm.byteLength;
            if (session.earlyBytes > 160000) throw new PlayAudioRecordingError('RECORDING_FAILED');
            session.earlyPcm?.push(pcm);
          }
        } : undefined,
      });
      if (!current(session) || session.stopping) { recorder.cancel(); return; }
      session.recorder = recorder;
      show(session, 'RECORDING', session.realtime ? '正在连接实时转写，录音已开始…' : '正在录音，说完后点击停止并转写。');
      if (session.realtime) {
        session.requestId = crypto.randomUUID();
        const stream = startRealtimeSpeech({ playId: props.playId, requestId: session.requestId,
          revision: props.revision, channel: props.channel, callId: props.callId, signal: session.controller.signal,
          onReady: () => { if (current(session) && !session.stopping) setState(value => value && ({ ...value, message: '边说边显示，识别中的文字可能调整。' })); },
          onTranscript: (text, confirmed) => { if (current(session)) setState(value => value && ({ ...value, text, confirmed })); },
        });
        session.stream = stream;
        for (const pcm of session.earlyPcm ?? []) stream.push(pcm);
        session.earlyPcm = [];
        void stream.result.then(data => { if (!session.failureHandled) accept(session, data); }).catch(cause => fail(session, cause, true));
      }
    } catch (cause) { fail(session, cause); }
  };
  const check = async () => {
    const session = run.current;
    if (!session?.requestId || session.id !== state?.sessionId || !current(session) || state?.stage !== 'WAITING' || session.checking) return;
    session.checking = true;
    session.failureHandled = false;
    show(session, 'TRANSCRIBING', '正在检查本次转写结果…');
    try { accept(session, await speechInput.receipt(props.playId, session.requestId, session.controller.signal)); }
    catch (cause) { fail(session, cause, true); }
    finally { session.checking = false; }
  };
  const close = () => {
    const session = run.current;
    if (!session || session.id !== state?.sessionId || !current(session)) return;
    cleanup(); setState(undefined);
    setTimeout(() => {
      if (latest.current.scope === scope && latest.current.enabled && !run.current && auth.current?.isCurrent() !== false) opener.current?.focus({ preventScroll: true });
    }, 0);
  };
  const visible = enabled && state?.scope === scope ? state : undefined;
  const candidate = visible?.text.trim() || '';
  const merged = props.value.trim() ? `${props.value}\n${candidate}` : candidate;
  const tooLong = Array.from(merged.trim()).length > 1000;

  const providerName = visible?.providerName;
  return <section aria-label={`${providerName ?? ''}语音输入`} className="space-y-2">
    {!visible ? <button ref={opener} type="button" disabled={!enabled} className={buttonStyle} onClick={() => void open()}>语音输入</button>
      : <div className="space-y-3 rounded-lg border border-brass/40 bg-ink p-3">
        <div className="flex items-center justify-between gap-3"><p className="text-sm font-semibold">{providerName}{visible.realtime ? '实时转写' : '语音输入'}</p><button type="button" className="min-h-11 px-2 text-sm text-mist underline" onClick={close}>{visible.stage === 'REVIEW' || visible.stage === 'ERROR' ? '关闭' : '取消'}</button></div>
        <p className="text-xs leading-6 text-mist">最长 60 秒。录音将交给{providerName ?? '语音识别服务'}转成文字；校对后填入草稿，由你确认发送。</p>
        {visible.message && <p role={visible.stage === 'ERROR' ? 'alert' : 'status'} className="text-sm leading-6 text-brass">{visible.message}</p>}
        {visible.stage === 'READY' && <button type="button" className={buttonStyle} onClick={() => void record()}>开始录音</button>}
        {visible.stage === 'RECORDING' && <div className="flex flex-wrap items-center gap-3"><span role="timer" className="text-sm text-paper">录音 {Math.floor(visible.seconds)} / 60 秒</span><button type="button" className={buttonStyle} onClick={() => { const session = run.current; if (session?.id === visible.sessionId) void finish(session); }}>{visible.realtime ? '停止录音' : '停止并转写'}</button></div>}
        {visible.realtime && ['RECORDING', 'TRANSCRIBING'].includes(visible.stage) && <div className="rounded-lg border border-line bg-panel p-3" aria-label="实时识别文字" aria-live="off">
          <p className="whitespace-pre-wrap break-words text-sm leading-7">{visible.text.startsWith(visible.confirmed ?? '') ? <>{visible.confirmed}<span className="text-mist">{visible.text.slice((visible.confirmed ?? '').length) || (!visible.text ? '说话后，文字会显示在这里。' : '')}</span></> : visible.text}</p>
          <p className="mt-2 text-xs text-mist">停止后可修改识别文字，再填入草稿。</p>
        </div>}
        {visible.stage === 'WAITING' && <button type="button" className={buttonStyle} onClick={() => void check()}>检查本次转写结果</button>}
        {visible.stage === 'REVIEW' && <>
          <label className="block text-sm">识别文字（可修改）<textarea ref={previewInput} value={visible.text} rows={3} maxLength={12000} className="mt-2 w-full rounded border border-line bg-panel p-3 text-paper" onChange={event => { const session = run.current; if (session?.id === visible.sessionId && current(session)) setState(value => value?.sessionId === session.id ? { ...value, text: event.target.value } : value); }} /></label>
          <p className={`text-xs leading-6 ${tooLong ? 'text-brass' : 'text-mist'}`}>填入后 {Array.from(merged.trim()).length} / 1000 字符。{tooLong ? '请缩短识别文字或原草稿后再填入。' : '已有文字会保留，识别文字追加到末尾。'}</p>
          <button type="button" disabled={!candidate || tooLong} className={buttonStyle} onClick={() => {
            const session = run.current;
            if (!session || session.id !== visible.sessionId || !current(session) || !candidate) return;
            const value = latest.current.value.trim() ? `${latest.current.value}\n${candidate}` : candidate;
            if (Array.from(value.trim()).length > 1000) return;
            latest.current.onChange(value); close();
          }}>填入草稿</button>
        </>}
      </div>}
  </section>;
}
