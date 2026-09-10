import { useEffect, useRef, useState } from 'react';
import AuthGuard from '@/components/AuthGuard';
import AppLayout from '@/components/AppLayout';
import AuthoringJobsPanel, { canCancelAuthoringJob, canQueueAuthoringJob, canRecoverAuthoringJob } from '@/components/AuthoringJobsPanel';
import authoringJobService, { AuthoringJobError, prepareAuthoringQueueAttempt, watchAuthoringJobs } from '@/services/authoringJobService';
import type { AuthoringQueueAttempt } from '@/services/authoringJobService';
import sourceBundleService from '@/services/sourceBundleService';
import { useAuthStore } from '@/stores/authStore';
import type { AuthoringJob, AuthoringJobDraft } from '@/types/authoringJob';
import type { SourceBundle, SourceBundleSummary } from '@/types/sourceBundle';

const emptyDraft: AuthoringJobDraft = { title: '', content_version: '', player_count: 5, source_ids: [] };

function AuthoringWorkspace() {
  const [bundles, setBundles] = useState<SourceBundleSummary[]>([]);
  const [bundle, setBundle] = useState<SourceBundle>();
  const [bundleHash, setBundleHash] = useState('');
  const [sourceQuery, setSourceQuery] = useState('');
  const [draft, setDraft] = useState<AuthoringJobDraft>(emptyDraft);
  const [jobs, setJobs] = useState<AuthoringJob[]>([]);
  const [selectedJobId, setSelectedJobId] = useState('');
  const [job, setJob] = useState<AuthoringJob>();
  const [listLoading, setListLoading] = useState(true);
  const [bundlesLoading, setBundlesLoading] = useState(true);
  const [sourceLoading, setSourceLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [bundlesError, setBundlesError] = useState('');
  const [sourceError, setSourceError] = useState('');
  const [actionError, setActionError] = useState('');
  const [notice, setNotice] = useState('');
  const [reload, setReload] = useState(0);
  const managed = useRef<{ stopPoll?: () => void; write?: AbortController; queueAttempt?: AuthoringQueueAttempt }>({});

  useEffect(() => {
    const requests = managed.current;
    const stop = watchAuthoringJobs({
      selectedId: selectedJobId ? Number(selectedJobId) : undefined,
      onJobs: setJobs,
      onJob: setJob,
      onError: setLoadError,
      onReady: () => { setListLoading(false); setDetailLoading(false); },
    });
    requests.stopPoll = stop;
    return stop;
  }, [selectedJobId, reload]);

  useEffect(() => {
    const controller = new AbortController();
    sourceBundleService.list(controller.signal)
      .then(result => { if (!controller.signal.aborted) { setBundles(result); setBundlesError(''); } })
      .catch(error => { if (!controller.signal.aborted) setBundlesError(error instanceof Error ? error.message : '来源列表读取失败。'); })
      .finally(() => { if (!controller.signal.aborted) setBundlesLoading(false); });
    return () => controller.abort();
  }, [reload]);

  useEffect(() => {
    const controller = new AbortController();
    if (bundleHash) {
      sourceBundleService.bundle(bundleHash, controller.signal)
        .then(result => { if (!controller.signal.aborted) { setBundle(result); setSourceError(''); } })
        .catch(error => { if (!controller.signal.aborted) setSourceError(error instanceof Error ? error.message : '来源元数据读取失败。'); })
        .finally(() => { if (!controller.signal.aborted) setSourceLoading(false); });
    }
    return () => controller.abort();
  }, [bundleHash, reload]);

  useEffect(() => {
    const requests = managed.current;
    return () => { requests.stopPoll?.(); requests.write?.abort(); };
  }, []);

  const perform = async (request: (signal: AbortSignal) => Promise<AuthoringJob>, success: (result: AuthoringJob) => void) => {
    const requests = managed.current;
    if (requests.write) return;
    requests.stopPoll?.();
    const controller = new AbortController();
    requests.write = controller;
    setBusy(true); setActionError(''); setNotice('');
    try {
      const result = await request(controller.signal);
      if (controller.signal.aborted) return;
      setJobs(current => [result, ...current.filter(item => item.id !== result.id)].slice(0, 100));
      setJob(result); setSelectedJobId(String(result.id));
      success(result);
    } catch (error) {
      if (!controller.signal.aborted) setActionError(error instanceof AuthoringJobError ? error.message : '操作未返回明确结果，请刷新任务状态。保持相同内容重试排队会复用本次提交标识。');
    } finally {
      if (!controller.signal.aborted) {
        requests.write = undefined;
        setBusy(false); setReload(value => value + 1);
      }
    }
  };

  const queue = async () => {
    if (managed.current.write || listLoading || bundlesLoading || sourceLoading || bundle?.bundle_hash !== bundleHash || !canQueueAuthoringJob(bundle, draft)) return;
    const payload = { ...draft, title: draft.title.trim(), source_ids: [...draft.source_ids], bundle_hash: bundleHash };
    const attempt = prepareAuthoringQueueAttempt(payload, managed.current.queueAttempt);
    managed.current.queueAttempt = attempt;
    await perform(signal => authoringJobService.create({ ...payload, idempotency_key: attempt.key }, signal), result => {
      managed.current.queueAttempt = undefined;
      setDraft(emptyDraft);
      setNotice(`任务 #${result.id} 已加入队列。由独立工作进程执行，当前没有批准发布。`);
    });
  };

  const changeState = async (observed: AuthoringJob, action: 'cancel' | 'recover') => {
    if (!job || job.id !== observed.id || job.revision !== observed.revision || managed.current.write || detailLoading) return;
    if (action === 'cancel' ? !canCancelAuthoringJob(job) : !canRecoverAuthoringJob(job)) return;
    await perform(signal => authoringJobService[action](observed.id, observed.revision, signal), () => {
      setNotice(action === 'cancel' ? '取消结果已保存。已发送请求仍需核对其结果与费用。' : '恢复检查结果已保存。只会继续尚未发送的步骤，不会重发未知请求。');
    });
  };

  return <AuthoringJobsPanel bundles={bundles} bundle={bundle} bundleHash={bundleHash} sourceQuery={sourceQuery} draft={draft} jobs={jobs} selectedJobId={selectedJobId} job={job}
    loading={listLoading || bundlesLoading} sourceLoading={sourceLoading} detailLoading={detailLoading} busy={busy} error={actionError || loadError || sourceError || bundlesError} notice={notice}
    onBundle={hash => {
      managed.current.queueAttempt = undefined;
      setBundleHash(hash); setBundle(undefined); setSourceLoading(Boolean(hash)); setSourceQuery('');
      setDraft(current => ({ ...current, source_ids: [], ...(current.rule_plan !== undefined ? { rule_plan: null } : {}) })); setSourceError(''); setNotice('');
    }} onSourceQuery={setSourceQuery} onDraft={value => {
      managed.current.queueAttempt = undefined;
      const bindingChanged = draft.title !== value.title || draft.content_version !== value.content_version
        || draft.player_count !== value.player_count || JSON.stringify(draft.source_ids) !== JSON.stringify(value.source_ids);
      setDraft(bindingChanged && value.rule_plan !== undefined ? { ...value, rule_plan: null } : value);
      setNotice('');
    }} onQueue={queue}
    onSelectJob={id => { if (id === selectedJobId) setReload(value => value + 1); setSelectedJobId(id); setJob(undefined); setDetailLoading(Boolean(id)); setActionError(''); }}
    onReload={() => { setListLoading(true); setBundlesLoading(true); setDetailLoading(Boolean(selectedJobId)); setSourceLoading(Boolean(bundleHash)); setBundle(undefined); setActionError(''); setLoadError(''); setSourceError(''); setBundlesError(''); setReload(value => value + 1); }}
    onCancel={item => changeState(item, 'cancel')} onRecover={item => changeState(item, 'recover')} />;
}

export default function AuthoringJobsPage() {
  const user = useAuthStore(state => state.user);
  return <AuthGuard><AppLayout>{user?.is_admin ? <AuthoringWorkspace /> : <p role="alert" className="mx-auto max-w-3xl px-4 pt-24 text-paper">只有管理员可以查看和管理编译任务。</p>}</AppLayout></AuthGuard>;
}
