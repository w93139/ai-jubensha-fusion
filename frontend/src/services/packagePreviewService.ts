import authService from '@/services/authService';
import { config } from '@/stores/configStore';

const changedIdentityMessage = '登录身份已变化，请刷新页面或重新打开开场。';

/** An opened preview never revives after its authenticated binding changes. */
export function watchPackagePreviewAuth(onChange: () => void) {
  const token = authService.getToken();
  let current = true;
  const check = () => {
    if (current && token !== authService.getToken()) { current = false; onChange(); }
    return current;
  };
  const events = ['storage', 'auth-token-changed', 'focus'];
  if (typeof window !== 'undefined') events.forEach(name => window.addEventListener(name, check));
  return { isCurrent: check, dispose: () => {
    if (typeof window !== 'undefined') events.forEach(name => window.removeEventListener(name, check));
  } };
}

export interface PreviewRelease {
  id: number; version_id: number; title: string; content_version: string; player_count: number;
  characters: { id: string; name: string }[];
}
interface PreviewItem { id: string; text: string; kind?: string; disclosure: string }
export interface PackagePreview {
  session_id: string; release_id: number; version_id: number; selected_character_id: string;
  script: { title: string; content_version: string };
  characters: { id: string; name: string }[];
  introduction: { text: string }; initial_phase: { id: string; title: string };
  public_knowledge: PreviewItem[]; private_knowledge: PreviewItem[];
  public_evidence: PreviewItem[]; private_evidence: PreviewItem[];
  status: 'READING_PREVIEW'; runtime_ready: false; available_actions: [];
  supports_rules_preview?: boolean;
  visuals?: { id: string; collection: 'knowledge' | 'evidence'; material_id: string; label: string }[];
  reading_supplements?: { id: string; text: string }[];
}

async function data<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = authService.getToken();
  const response = await fetch(`${config.api.baseUrl}/api/fusion${path}`, {
    ...options, cache: 'no-store', headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
    },
  });
  if (token !== authService.getToken()) throw new Error(changedIdentityMessage);
  if (!response.ok) throw new Error(response.status === 401 ? '请先登录。'
    : response.status === 403 ? '当前账号无权打开这份内容。'
    : response.status === 404 ? '找不到这份已发布内容或你的开场记录。'
    : response.status === 409 ? '审核或版本已经变化，请刷新后重新选择。'
    : '暂时无法打开开场，请稍后重试。');
  const result = await response.json();
  if (token !== authService.getToken()) throw new Error(changedIdentityMessage);
  return result.data as T;
}

const packagePreviewService = {
  image: async (sessionId: string, visualId: string, signal?: AbortSignal): Promise<Blob> => {
    const token = authService.getToken();
    const response = await fetch(`${config.api.baseUrl}/api/fusion/package-sessions/${encodeURIComponent(sessionId)}/images/${encodeURIComponent(visualId)}`, {
      signal, cache: 'no-store', headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (token !== authService.getToken()) throw new Error(changedIdentityMessage);
    if (!response.ok) throw new Error('原图暂时无法读取，请刷新开场后重试。');
    if (!['image/png', 'image/jpeg'].includes(response.headers.get('Content-Type') || '')) throw new Error('未收到有效原图。');
    const blob = await response.blob();
    if (token !== authService.getToken()) throw new Error(changedIdentityMessage);
    if (!blob.size || !['image/png', 'image/jpeg'].includes(blob.type)) throw new Error('未收到有效原图。');
    return blob;
  },
  releases: (signal?: AbortSignal) => data<PreviewRelease[]>('/package-releases', { signal }),
  create: (releaseId: number, characterId: string, key: string, signal?: AbortSignal) => data<PackagePreview>('/package-sessions', {
    method: 'POST', body: JSON.stringify({ release_id: releaseId, character_id: characterId, idempotency_key: key }), signal,
  }),
  read: (sessionId: string, signal?: AbortSignal) => data<PackagePreview>(`/package-sessions/${encodeURIComponent(sessionId)}`, { signal }),
};
export default packagePreviewService;
