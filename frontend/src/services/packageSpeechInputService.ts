import authService from '@/services/authService';
import { config } from '@/stores/configStore';

export type SpeechInputChannel = 'PUBLIC' | 'QUESTION' | 'PRIVATE';
export type SpeechInputCapability = {
  available: boolean; reason: string | null; max_seconds: number; min_seconds: number; max_characters: number;
  provider_name?: '千问' | '豆包' | null;
  realtime?: boolean;
};
export type SpeechInputResult = {
  request_id: string; state: 'OK' | 'PARTIAL' | 'EMPTY' | 'PENDING' | 'FAILED' | 'UNKNOWN' | 'EXPIRED';
  text: string | null; error_code: string | null;
};
export class SpeechInputError extends Error {
  constructor(public readonly status: number, message: string) { super(message); }
}

function messageFor(status: number): string {
  if (status === 401 || status === 403 || status === 404) return '当前语音输入已不可访问，请重新打开属于你的游戏。';
  if (status === 409) return '游戏或通话状态已变化，请在当前发言处重新录音。';
  if (status === 413 || status === 422) return '录音格式或时长不符合要求，请重新录制不超过 60 秒的语音。';
  if (status === 429) return '当前语音输入较多，请稍后再试，也可以继续输入文字。';
  if (status === 503) return '语音输入暂未就绪，请先使用文字输入。';
  return '未能确认转写结果。可以检查本次结果；不会自动重新上传录音。';
}

export async function request(path: string, options: RequestInit): Promise<unknown> {
  const token = authService.getToken();
  if (!token) throw new SpeechInputError(401, messageFor(401));
  const response = await fetch(`${config.api.baseUrl}/api/fusion${path}`, {
    ...options, cache: 'no-store', headers: { Authorization: `Bearer ${token}`,
      ...(options.body ? { 'Content-Type': 'audio/wav' } : {}) },
  });
  if (authService.getToken() !== token) throw new SpeechInputError(401, messageFor(401));
  // Provider errors and request bodies never become player-visible feedback.
  if (!response.ok) throw new SpeechInputError(response.status, messageFor(response.status));
  const body = await response.json();
  if (authService.getToken() !== token) throw new SpeechInputError(401, messageFor(401));
  if (!body || body.success !== true || !body.data) throw new SpeechInputError(502, messageFor(502));
  return body.data;
}

export function result(value: unknown, id: string): SpeechInputResult {
  const data = value as SpeechInputResult | null;
  if (!data || data.request_id !== id || !['OK', 'PARTIAL', 'EMPTY', 'PENDING', 'FAILED', 'UNKNOWN', 'EXPIRED'].includes(data.state)
    || (['OK', 'PARTIAL'].includes(data.state) ? typeof data.text !== 'string' || !data.text.trim() || Array.from(data.text).length > 6000 : data.text !== null)
    || (data.error_code !== null && typeof data.error_code !== 'string')) {
    throw new SpeechInputError(502, messageFor(502));
  }
  return { request_id: data.request_id, state: data.state, text: data.text, error_code: data.error_code };
}

const route = (playId: string) => `/package-plays/${encodeURIComponent(playId)}/speech-input`;
const packageSpeechInputService = {
  async capability(playId: string, signal: AbortSignal): Promise<SpeechInputCapability> {
    const data = await request(route(playId), { signal }) as SpeechInputCapability;
    if (typeof data.available !== 'boolean' || data.max_seconds !== 60 || data.min_seconds !== 0.2 || data.max_characters !== 1000
      || (data.reason !== null && typeof data.reason !== 'string')
      || (data.provider_name != null && !['千问', '豆包'].includes(data.provider_name))
      || (data.realtime != null && typeof data.realtime !== 'boolean')) throw new SpeechInputError(502, messageFor(502));
    return { available: data.available, reason: data.reason, max_seconds: 60, min_seconds: 0.2, max_characters: 1000,
      provider_name: data.provider_name ?? null, realtime: data.realtime === true };
  },
  async transcribe(playId: string, id: string, audio: Blob, context: { revision: number; channel: SpeechInputChannel; callId?: string }, signal: AbortSignal): Promise<SpeechInputResult> {
    const query = new URLSearchParams({ expected_revision: String(context.revision), channel: context.channel });
    if (context.channel === 'PRIVATE' && context.callId) query.set('call_id', context.callId);
    return result(await request(`${route(playId)}/${encodeURIComponent(id)}?${query}`, { method: 'POST', body: audio, signal }), id);
  },
  async receipt(playId: string, id: string, signal: AbortSignal): Promise<SpeechInputResult> {
    return result(await request(`${route(playId)}/${encodeURIComponent(id)}`, { signal }), id);
  },
};
export default packageSpeechInputService;
