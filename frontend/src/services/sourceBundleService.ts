import { config } from '@/stores/configStore';
import authService from '@/services/authService';
import type { SourceBundle, SourceBundleSummary, SourceVerification } from '@/types/sourceBundle';

const prefix = '/api/admin/fusion';

async function response(path: string, options: RequestInit = {}): Promise<Response> {
  const token = authService.getToken();
  const result = await fetch(`${config.api.baseUrl}${prefix}${path}`, {
    ...options, cache: 'no-store', headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  });
  if (!result.ok) {
    const message = result.status === 401 ? '请先登录后再查看材料。'
      : result.status === 403 ? '只有管理员可以查看剧本来源。'
      : result.status === 404 ? '找不到这份来源记录。'
      : '材料读取或核验失败，请检查文件是否完整后重试。';
    throw new Error(message);
  }
  return result;
}

async function data<T>(path: string, options: RequestInit = {}): Promise<T> {
  return (await (await response(path, options)).json()).data as T;
}

const sourceBundleService = {
  list: (signal?: AbortSignal) => data<SourceBundleSummary[]>('/source-bundles', { signal }),
  bundle: (hash: string, signal?: AbortSignal) => data<SourceBundle>(`/source-bundles/${encodeURIComponent(hash)}`, { signal }),
  verify: (hash: string, signal?: AbortSignal) => data<SourceVerification>(`/source-bundles/${encodeURIComponent(hash)}/verify`, { method: 'POST', signal }),
  source: (hash: string, id: string, signal?: AbortSignal) => response(`/source-bundles/${encodeURIComponent(hash)}/sources/${encodeURIComponent(id)}`, { signal }),
};

export default sourceBundleService;
