import { config } from '@/stores/configStore';
import authService from '@/services/authService';
import type { AuthoringJob, CreateAuthoringJobRequest } from '@/types/authoringJob';

export class AuthoringJobError extends Error {
  constructor(public readonly status: number, message: string) { super(message); }
}

export type AuthoringQueueAttempt = { signature: string; key: string };
export function prepareAuthoringQueueAttempt(payload: object, previous?: AuthoringQueueAttempt): AuthoringQueueAttempt {
  const signature = JSON.stringify(payload);
  return previous?.signature === signature ? previous : { signature, key: crypto.randomUUID() };
}

export function isActiveAuthoringJob(job: AuthoringJob): boolean { return job.state === 'QUEUED' || job.state === 'RUNNING'; }

async function data<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = authService.getToken();
  const response = await fetch(`${config.api.baseUrl}/api/admin/fusion/authoring-jobs${path}`, {
    ...options, cache: 'no-store', headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
    },
  });
  if (!response.ok) {
    const message = response.status === 401 ? '请登录后查看编译任务。'
      : response.status === 403 ? '只有管理员可以管理编译任务。'
      : response.status === 404 ? '找不到这项任务，请刷新列表。'
      : response.status === 409 ? '任务版本或执行状态已变化，请刷新后核对。恢复仅适用于尚未发送的步骤，未知请求不会重发。'
      : response.status === 400 || response.status === 422 ? '任务参数不符合要求，请核对来源选择、版本号与人数。'
      : response.status === 503 ? '编译服务暂不可用，请检查工作进程与模型配置后重试。'
      : '任务读取或操作失败，请稍后重试。';
    throw new AuthoringJobError(response.status, message);
  }
  return (await response.json()).data as T;
}

const authoringJobService = {
  list: (signal?: AbortSignal) => data<AuthoringJob[]>('', { signal }),
  detail: (id: number, signal?: AbortSignal) => data<AuthoringJob>(`/${encodeURIComponent(id)}`, { signal }),
  create: (request: CreateAuthoringJobRequest, signal?: AbortSignal) => data<AuthoringJob>(request.rule_plan !== undefined ? '/with-rule-plan' : '', { method: 'POST', body: JSON.stringify(request), signal }),
  cancel: (id: number, revision: number, signal?: AbortSignal) => data<AuthoringJob>(`/${encodeURIComponent(id)}/cancel`, { method: 'POST', body: JSON.stringify({ expected_revision: revision }), signal }),
  recover: (id: number, revision: number, signal?: AbortSignal) => data<AuthoringJob>(`/${encodeURIComponent(id)}/recover`, { method: 'POST', body: JSON.stringify({ expected_revision: revision }), signal }),
};

export function watchAuthoringJobs(options: {
  selectedId?: number;
  onJobs: (jobs: AuthoringJob[]) => void;
  onJob: (job: AuthoringJob) => void;
  onError: (error: string) => void;
  onReady: () => void;
}): () => void {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let active = false;
  const refresh = async () => {
    if (controller.signal.aborted) return;
    const results = await Promise.allSettled([
      authoringJobService.list(controller.signal),
      options.selectedId === undefined ? Promise.resolve(undefined) : authoringJobService.detail(options.selectedId, controller.signal),
    ]);
    if (controller.signal.aborted) return;
    const [list, detail] = results;
    if (list.status === 'fulfilled') {
      const jobs = list.value.slice(0, 100);
      active = jobs.some(isActiveAuthoringJob);
      options.onJobs(jobs);
    }
    if (detail.status === 'fulfilled' && detail.value && detail.value.id === options.selectedId) {
      options.onJob(detail.value);
      active = active || isActiveAuthoringJob(detail.value);
    }
    const failed = results.find(result => result.status === 'rejected');
    options.onError(failed?.status === 'rejected'
      ? failed.reason instanceof AuthoringJobError ? failed.reason.message : '无法读取任务状态，请检查连接后刷新。'
      : '');
    options.onReady();
    if (active) timer = setTimeout(refresh, 3000);
  };
  void refresh();
  return () => { controller.abort(); if (timer !== undefined) clearTimeout(timer); };
}

export default authoringJobService;
