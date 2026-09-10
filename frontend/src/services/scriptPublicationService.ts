import { config } from '@/stores/configStore';
import authService from '@/services/authService';
import type { ApprovePublicationRequest, CandidateDocument, PublicationApproval, PublicationGate, PublicationRelease, PublishPackageRequest } from '@/types/scriptPublication';

export class ScriptPublicationError extends Error {
  constructor(public readonly status: number, message: string) { super(message); }
}

export type PublicationAttempt = { signature: string; key: string };

export function preparePublicationAttempt(payload: object, previous?: PublicationAttempt): PublicationAttempt {
  const signature = JSON.stringify(payload);
  return previous?.signature === signature ? previous : { signature, key: crypto.randomUUID() };
}

async function data<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = authService.getToken();
  const response = await fetch(`${config.api.baseUrl}/api/admin/fusion${path}`, {
    ...options, cache: 'no-store',
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
    },
  });
  if (!response.ok) {
    const message = response.status === 401 ? '请先登录后再查看或确认发布版本。'
      : response.status === 403 ? '只有管理员可以确认内容与登记发布版本。'
      : response.status === 404 ? '找不到这份候选或发布记录，请刷新。'
      : response.status === 409 ? '审核依据已变化，或当前核验未通过。请刷新发布检查、重新核对内容并确认；旧确认不能直接发布。'
      : response.status === 400 || response.status === 422 ? '确认内容不符合要求，请检查模型意见的处理结果与必填说明。'
      : '发布记录读取或提交失败，请稍后重试。';
    throw new ScriptPublicationError(response.status, message);
  }
  const result = await response.json();
  if (result?.success !== true || !result.data) throw new ScriptPublicationError(502, '未收到完整发布结果，请刷新后核对。');
  return result.data as T;
}

const path = (versionId: number) => `/script-packages/${encodeURIComponent(versionId)}`;
const scriptPublicationService = {
  gate: (versionId: number, signal?: AbortSignal) => data<PublicationGate>(`${path(versionId)}/publication`, { signal }),
  candidate: (versionId: number, signal?: AbortSignal) => data<CandidateDocument>(path(versionId), { signal }),
  approve: (versionId: number, request: ApprovePublicationRequest, signal?: AbortSignal) => data<Omit<PublicationApproval, 'valid'>>(`${path(versionId)}/approvals`, {
    method: 'POST', body: JSON.stringify(request), signal,
  }),
  publish: (versionId: number, request: PublishPackageRequest, signal?: AbortSignal) => data<Omit<PublicationRelease, 'current_approval_valid'>>(`${path(versionId)}/publish`, {
    method: 'POST', body: JSON.stringify(request), signal,
  }),
};

export default scriptPublicationService;
