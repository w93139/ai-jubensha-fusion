import { config } from '@/stores/configStore';
import { FusionEvent, FusionState, PublicScript } from '@/types/fusion';
import authService from '@/services/authService';

type ApiResponse<T> = { success: boolean; message: string; data: T };

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = authService.getToken();
  const response = await fetch(`${config.api.baseUrl}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...options.headers },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || body.message || '请求失败');
  return (body as ApiResponse<T>).data;
}

const fusionGameService = {
  scripts: () => request<PublicScript[]>('/api/fusion/scripts'),
  createSession: (scriptId: number) => request<FusionState>('/api/fusion/sessions', { method: 'POST', body: JSON.stringify({ script_id: scriptId }) }),
  state: (sessionId: string) => request<FusionState>(`/api/fusion/sessions/${sessionId}`),
  events: (sessionId: string, after = 0) => request<FusionEvent[]>(`/api/fusion/sessions/${sessionId}/events?after=${after}`),
  selectCharacter: (sessionId: string, characterId: number) => request<FusionState>(`/api/fusion/sessions/${sessionId}/select-character`, {
    method: 'POST', body: JSON.stringify({ character_id: characterId, idempotency_key: crypto.randomUUID() }),
  }),
  action: (sessionId: string, type: string, payload: Record<string, unknown> = {}) => request<FusionState>(`/api/fusion/sessions/${sessionId}/actions`, {
    method: 'POST', body: JSON.stringify({ type, payload, idempotency_key: crypto.randomUUID() }),
  }),
  websocketUrl: (sessionId: string) => {
    const origin = config.api.baseUrl || (typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8010');
    const base = origin.replace(/^http/, 'ws');
    return `${base}/api/fusion/ws/${sessionId}?token=${encodeURIComponent(authService.getToken() || '')}`;
  },
};

export default fusionGameService;
