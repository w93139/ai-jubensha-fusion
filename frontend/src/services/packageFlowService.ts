import authService from '@/services/authService';
import { config } from '@/stores/configStore';
import type { CreatePackageFlowRequest, PackageFlow, PackageFlowActionRequest } from '@/types/packageFlow';

export class PackageFlowError extends Error {
  constructor(public readonly status: number, message: string) { super(message); }
}

export type PackageFlowAttempt = { signature: string; key: string };

export function preparePackageFlowAttempt(payload: object, previous?: PackageFlowAttempt): PackageFlowAttempt {
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
  if (!response.ok) {
    const message = response.status === 401 ? '请先登录后再打开阶段演练。'
      : response.status === 403 ? '当前账号无权查看或操作这份阶段演练。'
      : response.status === 404 ? '找不到属于你的开场或阶段演练，请从开场页面重新进入。'
      : response.status === 409 ? '版本、阶段或材料状态已变化。请点击“刷新演练”核对最新结果；不会自动重放推进或公开操作。'
      : response.status === 400 || response.status === 422 ? '本次操作不符合当前演练要求，请刷新后核对阶段和材料。'
      : '阶段演练读取或保存失败，请稍后重试。';
    throw new PackageFlowError(response.status, message);
  }
  const result = await response.json();
  if (result?.success !== true || !Object.hasOwn(result, 'data')) throw new PackageFlowError(502, '未收到完整演练结果，请刷新后核对。');
  return result.data as T;
}

const packageFlowService = {
  lookup: (openingSessionId: string, signal?: AbortSignal) => data<PackageFlow | null>(`/package-flows?opening_session_id=${encodeURIComponent(openingSessionId)}`, { signal }),
  create: (request: CreatePackageFlowRequest, signal?: AbortSignal) => data<PackageFlow>('/package-flows', { method: 'POST', body: JSON.stringify(request), signal }),
  read: (flowId: string, signal?: AbortSignal) => data<PackageFlow>(`/package-flows/${encodeURIComponent(flowId)}`, { signal }),
  act: (flowId: string, request: PackageFlowActionRequest, signal?: AbortSignal) => data<PackageFlow>(`/package-flows/${encodeURIComponent(flowId)}/actions`, {
    method: 'POST', body: JSON.stringify(request), signal,
  }),
};

export default packageFlowService;
