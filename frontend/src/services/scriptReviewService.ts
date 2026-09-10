import { config } from '@/stores/configStore';
import authService from '@/services/authService';
import type { CandidateReview, ReviewCandidate, RuleReview, ScriptAudit, SubmitAuditRequest, SubmitDispositionRequest } from '@/types/scriptReview';

export class ScriptReviewError extends Error {
  constructor(public readonly status: number, message: string) { super(message); }
}

export type ReviewAttempt = { signature: string; key: string };

export function prepareReviewAttempt(payload: object, previous?: ReviewAttempt): ReviewAttempt {
  const signature = JSON.stringify(payload);
  return previous?.signature === signature ? previous : { signature, key: crypto.randomUUID() };
}

async function data<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = authService.getToken();
  const response = await fetch(`${config.api.baseUrl}/api/admin/fusion${path}`, {
    ...options,
    cache: 'no-store',
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
    },
  });
  if (!response.ok) {
    const message = response.status === 401 ? '请先登录后再查看审核记录。'
      : response.status === 403 ? '只有管理员可以查看与提交审核记录。'
      : response.status === 404 ? '找不到这份候选或审核记录，请刷新列表。'
      : response.status === 409 ? '记录已变化，或来源核验未通过。请刷新记录并核对候选、来源及处理版本后重试。'
      : response.status === 422 || response.status === 400 ? '报告或处理记录不符合要求，请核对格式、来源引用和处理说明。'
      : '审核记录读取或提交失败，请稍后重试。';
    throw new ScriptReviewError(response.status, message);
  }
  return (await response.json()).data as T;
}

const scriptReviewService = {
  candidates: (signal?: AbortSignal) => data<ReviewCandidate[]>('/review-candidates', { signal }),
  review: (versionId: number, signal?: AbortSignal) => data<CandidateReview>(`/script-packages/${encodeURIComponent(versionId)}/review`, { signal }),
  rules: (versionId: number, packageHash: string, bundleHash: string, offset = 0, signal?: AbortSignal) => data<RuleReview>(
    `/script-packages/${encodeURIComponent(versionId)}/rule-review?${new URLSearchParams({ expected_package_hash: packageHash, bundle_hash: bundleHash, offset: String(offset), limit: '20' })}`,
    { signal },
  ),
  submitAudit: (versionId: number, request: SubmitAuditRequest, signal?: AbortSignal) => data<ScriptAudit>(`/script-packages/${encodeURIComponent(versionId)}/audits`, {
    method: 'POST', body: JSON.stringify(request), signal,
  }),
  submitDisposition: (auditId: number, request: SubmitDispositionRequest, signal?: AbortSignal) => data<ScriptAudit>(`/script-audits/${encodeURIComponent(auditId)}/dispositions`, {
    method: 'POST', body: JSON.stringify(request), signal,
  }),
};

export default scriptReviewService;
