import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/router';
import AuthGuard from '@/components/AuthGuard';
import AppLayout from '@/components/AppLayout';
import ScriptReviewPanel, { auditSchemaForPackage, canSubmitDisposition, findingKey } from '@/components/ScriptReviewPanel';
import sourceBundleService from '@/services/sourceBundleService';
import scriptReviewService, { prepareReviewAttempt } from '@/services/scriptReviewService';
import type { ReviewAttempt } from '@/services/scriptReviewService';
import { useAuthStore } from '@/stores/authStore';
import type { CandidateReview, FindingDraft, ReviewCandidate, ReviewFinding, ScriptAudit, ScriptAuditReport } from '@/types/scriptReview';
import type { SourceBundleSummary } from '@/types/sourceBundle';

function ReviewWorkspace({ requestedId }: { requestedId: string }) {
  const [candidates, setCandidates] = useState<ReviewCandidate[]>([]);
  const [bundles, setBundles] = useState<SourceBundleSummary[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    Promise.allSettled([scriptReviewService.candidates(controller.signal), sourceBundleService.list(controller.signal)])
      .then(([candidateResult, bundleResult]) => {
        if (controller.signal.aborted) return;
        const errors: string[] = [];
        if (candidateResult.status === 'fulfilled') {
          setCandidates(candidateResult.value);
          setSelectedId(current => candidateResult.value.some(item => String(item.id) === current) ? current
            : candidateResult.value.some(item => String(item.id) === requestedId) ? requestedId : String(candidateResult.value[0]?.id ?? ''));
        } else errors.push(candidateResult.reason instanceof Error ? candidateResult.reason.message : '读取候选列表失败。');
        if (bundleResult.status === 'fulfilled') setBundles(bundleResult.value);
        else errors.push(bundleResult.reason instanceof Error ? bundleResult.reason.message : '读取来源快照列表失败。');
        setError(errors.join(' '));
        setLoading(false);
      });
    return () => controller.abort();
  }, [reload, requestedId]);
  return <CandidateWorkspace key={selectedId} candidates={candidates} bundles={bundles} selectedId={selectedId} listLoading={loading} listError={error} reload={reload}
    onSelect={setSelectedId} onReload={() => { setLoading(true); setError(''); setReload(value => value + 1); }} />;
}

function CandidateWorkspace({ candidates, bundles, selectedId, listLoading, listError, reload, onSelect, onReload }: {
  candidates: ReviewCandidate[]; bundles: SourceBundleSummary[]; selectedId: string; listLoading: boolean; listError: string; reload: number;
  onSelect: (id: string) => void; onReload: () => void;
}) {
  const candidate = candidates.find(item => String(item.id) === selectedId);
  const [review, setReview] = useState<CandidateReview>();
  const [bundleHash, setBundleHash] = useState('');
  const [reportText, setReportText] = useState('');
  const [drafts, setDrafts] = useState<Record<string, FindingDraft>>({});
  const [loading, setLoading] = useState(Boolean(selectedId));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const managed = useRef<{ read?: AbortController; write?: AbortController; attempts: Record<string, ReviewAttempt> }>({ attempts: {} });

  useEffect(() => {
    const requests = managed.current;
    const controller = new AbortController();
    requests.read = controller;
    if (selectedId) {
      scriptReviewService.review(Number(selectedId), controller.signal)
        .then(result => { if (!controller.signal.aborted) setReview(result); })
        .catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '读取审核记录失败。'); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }
    return () => controller.abort();
  }, [selectedId, reload]);

  useEffect(() => {
    const requests = managed.current;
    return () => { requests.read?.abort(); requests.write?.abort(); };
  }, []);

  const save = async (attemptId: string, payload: object, request: (key: string, signal: AbortSignal) => Promise<ScriptAudit>, onSuccess: () => void) => {
    if (!candidate || managed.current.write) return;
    const requests = managed.current;
    const attempt = prepareReviewAttempt(payload, requests.attempts[attemptId]);
    requests.attempts[attemptId] = attempt;
    requests.read?.abort();
    const controller = new AbortController();
    requests.write = controller;
    setBusy(true); setError(''); setNotice('');
    try {
      const audit = await request(attempt.key, controller.signal);
      if (controller.signal.aborted) return;
      delete requests.attempts[attemptId];
      onSuccess();
      setReview(current => current ? { ...current, audits: [audit, ...current.audits.filter(item => item.id !== audit.id)] } : current);
      setNotice('人工记录已保存，尚未批准发布。');
      try {
        const refreshed = await scriptReviewService.review(candidate.id, controller.signal);
        if (!controller.signal.aborted) setReview(refreshed);
      } catch {
        if (!controller.signal.aborted) setError('记录已保存，但刷新失败。请点击“刷新记录”查看最新结果。');
      }
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '保存失败，请重试。');
    } finally {
      if (!controller.signal.aborted) { setBusy(false); requests.write = undefined; }
    }
  };

  const submitReport = async () => {
    if (!candidate || !review || review.candidate.package_hash !== candidate.package_hash || listLoading || loading || managed.current.write) return;
    if (!bundles.some(item => item.bundle_hash === bundleHash && item.status === 'FROZEN' && item.script_key === candidate.script_key)) {
      setError('请先选择与候选剧本对应的来源快照。'); return;
    }
    let report: ScriptAuditReport;
    const reportSchema = auditSchemaForPackage(candidate.contract_version);
    try {
      const parsed: unknown = JSON.parse(reportText);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error();
      const value = parsed as Partial<ScriptAuditReport>;
      if (value.schema_version !== reportSchema || typeof value.summary !== 'string' || !value.summary.trim() || !Array.isArray(value.coverage) || !Array.isArray(value.findings)) throw new Error();
      report = value as ScriptAuditReport;
    } catch { setError(`请输入有效的 ${reportSchema} JSON，包含 summary、coverage 和 findings。`); return; }
    const payload = { expected_package_hash: candidate.package_hash, bundle_hash: bundleHash, report };
    await save('report', payload, (key, signal) => scriptReviewService.submitAudit(candidate.id, { ...payload, idempotency_key: key }, signal), () => setReportText(''));
  };

  const submitDisposition = async (audit: ScriptAudit, finding: ReviewFinding, draft: FindingDraft) => {
    const observed = review?.audits.find(item => item.id === audit.id && item.audit_hash === audit.audit_hash && item.revision === audit.revision);
    if (!candidate || !observed || observed.package_hash !== candidate.package_hash || !observed.report.findings.some(item => item.id === finding.id) || !canSubmitDisposition(finding, draft) || listLoading || loading) return;
    const key = findingKey(observed, finding);
    const payload = {
      expected_package_hash: candidate.package_hash, expected_audit_hash: observed.audit_hash, expected_revision: observed.revision,
      finding_id: finding.id, status: draft.status, note: draft.note.trim(),
    };
    await save(key, payload, (idempotencyKey, signal) => scriptReviewService.submitDisposition(observed.id, { ...payload, idempotency_key: idempotencyKey }, signal), () => {
      setDrafts(current => { const next = { ...current }; delete next[key]; return next; });
    });
  };

  return <ScriptReviewPanel candidates={candidates} selectedId={selectedId} bundles={bundles} bundleHash={bundleHash} review={review} reportText={reportText} drafts={drafts}
    loading={loading || listLoading} busy={busy} error={error || listError} notice={notice}
    onSelect={onSelect} onReload={() => { setLoading(Boolean(selectedId)); setError(''); setNotice(''); onReload(); }}
    onBundle={hash => { delete managed.current.attempts.report; setBundleHash(hash); setNotice(''); }}
    onReport={text => { delete managed.current.attempts.report; setReportText(text); setNotice(''); }} onSubmitReport={submitReport}
    onDraft={(key, draft) => { delete managed.current.attempts[key]; setDrafts(current => ({ ...current, [key]: draft })); setNotice(''); }} onSubmitDisposition={submitDisposition} />;
}

export default function ScriptReviewsPage() {
  const user = useAuthStore(state => state.user);
  const router = useRouter();
  const requestedId = typeof router.query.version_id === 'string' ? router.query.version_id : '';
  return <AuthGuard><AppLayout>{user?.is_admin ? <ReviewWorkspace key={requestedId} requestedId={requestedId} /> : <p role="alert" className="mx-auto max-w-3xl px-4 pt-24 text-paper">只有管理员可以查看剧本审核记录。</p>}</AppLayout></AuthGuard>;
}
