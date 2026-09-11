import authService from '@/services/authService';
import { config } from '@/stores/configStore';
import type { CreatePackagePlayRequest, PackagePlay, PackagePlayActionRequest, PackagePlayAskRequest, PackagePlaySpeakRequest, PackagePlayProposalRequest, PackagePlayRespondRequest } from '@/types/packagePlay';
import type { FullTableRequest, FullDecisionRequest, FullPrivateReplyRequest, FullPhoneRequest, FullPhonePauseRequest, GuidedRequest, TopicRequest } from '@/types/packagePlay';

export class PackagePlayError extends Error {
  constructor(public readonly status: number, message: string, public readonly revisionConflict = false,
    public readonly rejectedBeforeDispatch = false) { super(message); }
}

export type PackagePlayAttempt = { signature: string; key: string };

export function watchPackagePlayAuth(onChange: () => void) {
  const token = authService.getToken();
  let observedToken = token;
  let valid = true;
  const check = () => {
    const current = authService.getToken();
    // An image's original binding never revives, even if a token changes back.
    // The workspace also needs every later change after an explicit refresh.
    if (observedToken !== current) { observedToken = current; valid = false; onChange(); }
    return valid;
  };
  const events = ['storage', 'auth-token-changed', 'focus'];
  if (typeof window !== 'undefined') events.forEach(name => window.addEventListener(name, check));
  return { isCurrent: check, dispose: () => {
    if (typeof window !== 'undefined') events.forEach(name => window.removeEventListener(name, check));
  } };
}

export function preparePackagePlayAttempt(payload: object, previous?: PackagePlayAttempt): PackagePlayAttempt {
  const signature = JSON.stringify(payload);
  return previous?.signature === signature ? previous : { signature, key: crypto.randomUUID() };
}

async function data<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = authService.getToken();
  const response = await fetch(`${config.api.baseUrl}/api/fusion${path}`, {
    ...options, cache: 'no-store', headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
    },
  });
  if (token !== authService.getToken()) throw new PackagePlayError(401, '登录身份已变化，请重新打开试玩。');
  if (!response.ok) {
    let revisionConflict = false;
    let code: unknown;
    if ([400, 409, 413, 422].includes(response.status)) {
      try { code = (await response.json())?.detail; }
      catch { /* An unreadable or unknown error never authorizes changing a retry. */ }
    }
    if (token !== authService.getToken()) throw new PackagePlayError(401, '登录身份已变化，请重新打开试玩。');
    revisionConflict = response.status === 409 && code === 'PACKAGE_PLAY_REVISION_CONFLICT';
    if (typeof code === 'string' && ['GUIDED_ROUND_SELECTION_INVALID', 'GUIDED_INVESTIGATION_NOT_AVAILABLE'].includes(code)) {
      throw new PackagePlayError(response.status, '所选地点或本轮调查状态已变化，请刷新后重新选择；本次调查未执行。', false, true);
    }
    if (typeof code === 'string' && ['SINGLE_TOPIC_NOT_AVAILABLE', 'SINGLE_FALLBACK_NOT_AVAILABLE', 'SINGLE_TOPIC_CALL_ACTIVE', 'SINGLE_TOPIC_REQUIRED',
      'PACKAGE_PLAY_AI_BUSY', 'PACKAGE_PLAY_AI_UNAVAILABLE', 'PACKAGE_PLAY_BUDGET_EXCEEDED',
      'PACKAGE_DIALOGUE_INPUT_INVALID', 'PACKAGE_DIALOGUE_INPUT_TOO_LARGE', 'FULL_PLAY_PRIVATE_REPLY_UNAVAILABLE', 'FULL_PLAY_CALL_NOT_PARTICIPANT'].includes(code)) {
      throw new PackagePlayError(response.status, '当前议题、通话或互动状态已变化。请刷新核对，再选择可用操作；本次操作未执行。', false, true);
    }
    if (response.status === 422 && code === 'PACKAGE_PLAY_QUESTION_CLARIFICATION_REQUIRED') {
      throw new PackagePlayError(422, '请先明确你指的是谁：核对自己角色的身份，还是请对方自我介绍？涉及其他人物时请写出名字，再发送问题。', false, true);
    }
    if (response.status === 422 && code === 'PACKAGE_PLAY_OUT_OF_SCOPE') {
      throw new PackagePlayError(422, '请围绕本剧本的人物、经历和线索交流。游戏外任务及索取隐藏资料不予回答；本次未请求 AI，不消耗互动轮次。', false, true);
    }
    const message = response.status === 401 ? '请先登录后再打开文字试玩。'
      : response.status === 403 ? '当前账号无权查看或操作这份文字试玩。'
      : response.status === 404 ? '找不到属于你的开场或文字试玩，请从开场页面重新进入。'
      : response.status === 409 ? revisionConflict
        ? '游戏进度已更新，请点击“刷新进度”查看最新结果。刷新只核对记录，不会重复执行操作或请求 AI。'
        : '当前操作暂时无法继续，请点击“刷新进度”核对游戏状态。刷新只核对记录，不会重复执行操作或请求 AI。'
      : response.status === 413 ? '输入内容过长，请缩短后再提交。'
      : response.status === 400 || response.status === 422 ? '本次操作不符合当前试玩要求，请检查字数、阶段和材料。'
      : '文字试玩读取或保存失败，请稍后重试。';
    throw new PackagePlayError(response.status, message, revisionConflict);
  }
  const result = await response.json();
  if (token !== authService.getToken()) throw new PackagePlayError(401, '登录身份已变化，请重新打开试玩。');
  if (result?.success !== true || !Object.hasOwn(result, 'data')) throw new PackagePlayError(502, '未收到完整试玩结果，请刷新后核对。');
  return result.data as T;
}

export interface PlayLibraryItem {
  play_id: string; opening_session_id: string; title: string; character_name: string;
  phase_label: string; settled: boolean; revision: number; updated_at: string;
}
export interface PlayLibrary { items: PlayLibraryItem[]; has_more: boolean }

export function checkedPlayLibrary(value: unknown): PlayLibrary {
  const result = value as PlayLibrary;
  if (!result || !Array.isArray(result.items) || result.items.length > 50 || typeof result.has_more !== 'boolean' ||
    result.items.some(item => !item || !/^play-[a-zA-Z0-9-]{1,80}$/.test(item.play_id) ||
      typeof item.opening_session_id !== 'string' || typeof item.title !== 'string' || !item.title.trim() || item.title.length > 300 ||
      typeof item.character_name !== 'string' || !item.character_name.trim() || item.character_name.length > 100 ||
      !['阅读材料', '共同调查', '终局答卷', '已结束'].includes(item.phase_label) ||
      typeof item.settled !== 'boolean' || (item.phase_label === '已结束') !== item.settled ||
      !Number.isSafeInteger(item.revision) || item.revision < 0 || typeof item.updated_at !== 'string' || !Number.isFinite(Date.parse(item.updated_at))) ||
    new Set(result.items.map(item => item.play_id)).size !== result.items.length || (result.has_more && !result.items.length)) {
    throw new PackagePlayError(502, '游戏记录暂时无法读取，请重试。');
  }
  return result;
}

const packagePlayService = {
  finaleMotivations: (playId: string, signal?: AbortSignal) => data<PackagePlay>(`/package-plays/${encodeURIComponent(playId)}/finale-motivations`, {
    method: 'POST', signal,
  }),
  library: async (offset = 0, limit = 12, signal?: AbortSignal): Promise<PlayLibrary> => {
    if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > 50) throw new Error('无效的记录页码。');
    return checkedPlayLibrary(await data<unknown>(`/package-play-library?offset=${offset}&limit=${limit}`, { signal }));
  },
  topic: (playId: string, request: TopicRequest, signal?: AbortSignal) => data<PackagePlay>(`/package-plays/${encodeURIComponent(playId)}/topic`, {
    method: 'POST', body: JSON.stringify(request), signal,
  }),
  guided: (playId: string, request: GuidedRequest, signal?: AbortSignal) => data<PackagePlay>(`/package-plays/${encodeURIComponent(playId)}/guided`, {
    method: 'POST', body: JSON.stringify(request), signal,
  }),
  image: async (playId: string, visualId: string, signal?: AbortSignal): Promise<Blob> => {
    const token = authService.getToken();
    const response = await fetch(`${config.api.baseUrl}/api/fusion/package-plays/${encodeURIComponent(playId)}/images/${encodeURIComponent(visualId)}`, {
      signal, cache: 'no-store', headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (token !== authService.getToken()) throw new PackagePlayError(401, '登录身份已变化，请重新打开试玩。');
    if (!response.ok) throw new PackagePlayError(response.status, '原图暂时无法读取，请刷新试玩后重试。');
    if (!['image/png', 'image/jpeg'].includes(response.headers.get('Content-Type') || '')) {
      throw new PackagePlayError(502, '未收到有效原图。');
    }
    const blob = await response.blob();
    if (token !== authService.getToken()) throw new PackagePlayError(401, '登录身份已变化，请重新打开试玩。');
    if (!blob.size || !['image/png', 'image/jpeg'].includes(blob.type)) throw new PackagePlayError(502, '未收到有效原图。');
    return blob;
  },
  replyPrivate: (playId: string, request: FullPrivateReplyRequest, signal?: AbortSignal) => data<PackagePlay>(`/package-plays/${encodeURIComponent(playId)}/private-responses`, {
    method: 'POST', body: JSON.stringify(request), signal,
  }),
  phoneStep: (playId: string, request: FullPhoneRequest, signal?: AbortSignal) => data<PackagePlay>(`/package-plays/${encodeURIComponent(playId)}/phone-step`, {
    method: 'POST', body: JSON.stringify(request), signal,
  }),
  pausePhone: (playId: string, request: FullPhonePauseRequest, signal?: AbortSignal) => data<PackagePlay>(`/package-plays/${encodeURIComponent(playId)}/phone-pause`, {
    method: 'POST', body: JSON.stringify(request), signal,
  }),
  table: (playId: string, request: FullTableRequest, signal?: AbortSignal) => data<PackagePlay>(`/package-plays/${encodeURIComponent(playId)}/table`, {
    method: 'POST', body: JSON.stringify(request), signal,
  }),
  decide: (playId: string, request: FullDecisionRequest, signal?: AbortSignal) => data<PackagePlay>(`/package-plays/${encodeURIComponent(playId)}/decisions`, {
    method: 'POST', body: JSON.stringify(request), signal,
  }),
  lookup: (openingSessionId: string, signal?: AbortSignal) => data<PackagePlay | null>(`/package-plays?opening_session_id=${encodeURIComponent(openingSessionId)}`, { signal }),
  create: (request: CreatePackagePlayRequest, signal?: AbortSignal) => data<PackagePlay>('/package-plays', { method: 'POST', body: JSON.stringify(request), signal }),
  read: (playId: string, signal?: AbortSignal) => data<PackagePlay>(`/package-plays/${encodeURIComponent(playId)}`, { signal }),
  act: (playId: string, request: PackagePlayActionRequest, signal?: AbortSignal) => data<PackagePlay>(`/package-plays/${encodeURIComponent(playId)}/actions`, {
    method: 'POST', body: JSON.stringify(request), signal,
  }),
  ask: (playId: string, request: PackagePlayAskRequest, signal?: AbortSignal) => data<PackagePlay>(`/package-plays/${encodeURIComponent(playId)}/ask`, {
    method: 'POST', body: JSON.stringify(request), signal,
  }),
  speak: (playId: string, request: PackagePlaySpeakRequest, signal?: AbortSignal) => data<PackagePlay>(`/package-plays/${encodeURIComponent(playId)}/discussion`, {
    method: 'POST', body: JSON.stringify(request), signal,
  }),
  propose: (playId: string, request: PackagePlayProposalRequest, signal?: AbortSignal) => data<PackagePlay>(`/package-plays/${encodeURIComponent(playId)}/proposals`, {
    method: 'POST', body: JSON.stringify(request), signal,
  }),
  respond: (playId: string, request: PackagePlayRespondRequest, signal?: AbortSignal) => data<PackagePlay>(`/package-plays/${encodeURIComponent(playId)}/responses`, {
    method: 'POST', body: JSON.stringify(request), signal,
  }),
};

export default packagePlayService;
