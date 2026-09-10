import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import scriptPublicationService, { preparePublicationAttempt, ScriptPublicationError } from '@/services/scriptPublicationService';
import type { PublicationAttempt } from '@/services/scriptPublicationService';
import type { ReviewCandidate, ReviewFinding } from '@/types/scriptReview';
import type { ApprovePublicationRequest, CandidateDocument, ModelFindingDecision, PublicationFindingDraft, PublicationGate, PublishPackageRequest } from '@/types/scriptPublication';

const severityLabels = { BLOCKER: '阻断', WARNING: '警告', INFO: '提示' };
const categoryLabels = { PROVENANCE: '来源', TIMELINE: '时间线', EVIDENCE: '证据链', KNOWLEDGE_BOUNDARY: '知识边界', PLAYABILITY: '可玩性' };
const checkLabels: Record<string, string> = {
  CANDIDATE_VALID: '候选结构与规则', REVIEW_INTEGRITY_VALID: '审核记录完整性', MANUAL_AUDIT_COMPLETE: '人工审核覆盖',
  MANUAL_BLOCKERS_CLOSED: '人工阻断问题', MANUAL_WARNINGS_REVIEWED: '人工警告处理', MODEL_AUDIT_COMPLETE: '模型审核报告',
  AUTHORING_TASKS_SETTLED: '编译任务状态', MODEL_FINDING_LIMIT: '模型意见数量', SOURCE_SCOPE_VALID: '来源快照一致性', SOURCE_VERIFIED: '来源文件核验',
};

export function modelFindingKey(jobId: number, findingId: string): string { return JSON.stringify([jobId, findingId]); }

export function matchingGate(candidate: ReviewCandidate, gate?: PublicationGate): PublicationGate | undefined {
  return gate?.candidate.id === candidate.id && gate.candidate.package_hash === candidate.package_hash ? gate : undefined;
}

export function matchingDocument(candidate: ReviewCandidate, document?: CandidateDocument): CandidateDocument | undefined {
  return document?.id === candidate.id && document.package_hash === candidate.package_hash
    && document.script_key === candidate.script_key && document.content_version === candidate.content_version ? document : undefined;
}

export function modelDecisions(gate: PublicationGate, drafts: Record<string, PublicationFindingDraft>): ModelFindingDecision[] | undefined {
  const decisions: ModelFindingDecision[] = [];
  let hasReport = false;
  for (const item of gate.model_reports) {
    if (!item.report && (item.status === 'FAILED' || item.status === 'MISSING')) continue;
    if (item.status !== 'SUCCEEDED' || !item.report) return undefined;
    hasReport = true;
    for (const finding of item.report.findings) {
      if (finding.severity === 'INFO') continue;
      const draft = drafts[modelFindingKey(item.job_id, finding.id)];
      if (!draft?.note.trim() || draft.note.trim().length > 4000
        || (draft.status !== 'DISMISSED' && !(draft.status === 'ACKNOWLEDGED' && finding.severity === 'WARNING'))) return undefined;
      decisions.push({ job_id: item.job_id, finding_id: finding.id, status: draft.status, note: draft.note.trim() });
    }
  }
  return hasReport && decisions.length <= 1000 ? decisions : undefined;
}

export function approvalPayload(candidate: ReviewCandidate, gate: PublicationGate | undefined, document: CandidateDocument | undefined,
  drafts: Record<string, PublicationFindingDraft>, note: string, confirmed: boolean): Omit<ApprovePublicationRequest, 'idempotency_key'> | undefined {
  const current = matchingGate(candidate, gate);
  if (!current?.can_approve || current.release || !current.bundle_hash || !current.checks.every(item => item.passed)
    || !matchingDocument(candidate, document) || !confirmed || !note.trim() || note.trim().length > 4000) return undefined;
  const decisions = modelDecisions(current, drafts);
  if (!decisions) return undefined;
  return { expected_package_hash: candidate.package_hash, bundle_hash: current.bundle_hash, expected_basis_hash: current.basis_hash,
    model_dispositions: decisions, note: note.trim() };
}

export function publishPayload(candidate: ReviewCandidate, gate?: PublicationGate): Omit<PublishPackageRequest, 'idempotency_key'> | undefined {
  const current = matchingGate(candidate, gate);
  const approval = current?.approval;
  if (!current?.can_publish || current.release || !current.checks.every(item => item.passed) || !approval?.valid
    || approval.version_id !== candidate.id || approval.package_hash !== candidate.package_hash
    || approval.bundle_hash !== current.bundle_hash || approval.basis_hash !== current.basis_hash) return undefined;
  return { approval_id: approval.id, expected_approval_hash: approval.approval_hash, expected_basis_hash: current.basis_hash };
}

const record = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
const plain = (value: unknown): string => typeof value === 'string' ? value : '';

function CandidateContents({ document }: { document: CandidateDocument }) {
  const content = document.package;
  return <div aria-label="候选正文" className="mt-4 max-h-[36rem] min-w-0 max-w-full space-y-5 overflow-auto rounded bg-ink p-4 text-sm">
    <p className="text-mist">包含所有角色材料与后台真相，仅供管理员逐项核对。正文在此只读；修改内容需提交新候选版本。</p>
    <section><h4 className="font-semibold">故事介绍</h4><p className="mt-2 whitespace-pre-wrap break-all leading-6">{plain(record(content.introduction).text)}</p></section>
    {([['characters', '角色'], ['phases', '阶段'], ['knowledge', '角色知识与公共材料'], ['evidence', '证据'], ['truth', '后台真相']] as const).map(([collection, label]) => <section key={collection}>
      <h4 className="font-semibold">{label}</h4>
      {(Array.isArray(content[collection]) ? content[collection] as unknown[] : []).map((value, index) => {
        const item = record(value);
        return <div key={index} className="mt-3 min-w-0 border-t border-line pt-3">
          <p className="break-all text-xs text-mist">{plain(item.id)}{plain(item.name) ? ` · ${plain(item.name)}` : ''}{plain(item.title) ? ` · ${plain(item.title)}` : ''}</p>
          {plain(item.text) && <p className="mt-2 whitespace-pre-wrap break-all leading-6">{plain(item.text)}</p>}
          <details className="mt-2"><summary className="cursor-pointer text-xs text-brass">查看此项规则与来源</summary><pre className="mt-2 whitespace-pre-wrap break-all text-xs leading-6">{JSON.stringify(Object.fromEntries(Object.entries(item).filter(([key]) => key !== 'text')), null, 2)}</pre></details>
        </div>;
      })}
    </section>)}
    <section><h4 className="font-semibold">结算说明</h4><p className="mt-2 whitespace-pre-wrap break-all leading-6">{plain(record(record(content.settlement).instructions).text)}</p></section>
    <details><summary className="cursor-pointer text-brass">查看完整候选与所有来源标识</summary><pre className="mt-3 whitespace-pre-wrap break-all text-xs leading-6">{JSON.stringify(content, null, 2)}</pre></details>
  </div>;
}

export type ScriptPublicationViewProps = {
  candidate: ReviewCandidate;
  gate?: PublicationGate;
  document?: CandidateDocument;
  drafts: Record<string, PublicationFindingDraft>;
  note: string;
  confirmed: boolean;
  loading: boolean;
  reading: boolean;
  busy: boolean;
  error: string;
  notice: string;
  onReload: () => void;
  onRead: () => void;
  onDraft: (key: string, value: PublicationFindingDraft) => void;
  onNote: (value: string) => void;
  onConfirmed: (value: boolean) => void;
  onApprove: (payload: Omit<ApprovePublicationRequest, 'idempotency_key'>) => void;
  onPublish: (payload: Omit<PublishPackageRequest, 'idempotency_key'>) => void;
};

export function ScriptPublicationView(props: ScriptPublicationViewProps) {
  const gate = matchingGate(props.candidate, props.gate);
  const document = matchingDocument(props.candidate, props.document);
  const locked = props.busy || props.loading || props.reading;
  const approval = approvalPayload(props.candidate, gate, document, props.drafts, props.note, props.confirmed);
  const publication = publishPayload(props.candidate, gate);
  const findingControl = (jobId: number, finding: ReviewFinding) => {
    if (finding.severity === 'INFO') return <p className="mt-3 text-xs text-mist">提示仅供人工参考，不代表规则或可玩性已获证明。</p>;
    const key = modelFindingKey(jobId, finding.id);
    const draft = props.drafts[key] || { status: '', note: '' };
    return <div className="mt-3">
      <label className="block text-sm">你的处理结果（必选）
        <select aria-label={`模型任务 ${jobId} 问题 ${finding.id} 的处理结果`} value={draft.status} disabled={locked || Boolean(gate?.release)}
          onChange={event => props.onDraft(key, { ...draft, status: event.target.value as PublicationFindingDraft['status'] })} className="mt-2 w-full min-w-0 rounded border border-line bg-ink p-2">
          <option value="">请选择人工判断</option>
          <option value="ACKNOWLEDGED" disabled={finding.severity === 'BLOCKER'}>已知悉并接受（仅警告）</option>
          <option value="DISMISSED">核对后判定为误报</option>
        </select>
      </label>
      {finding.severity === 'BLOCKER' && <p className="mt-2 text-xs leading-6 text-mist">若问题属实，请修正材料并生成新候选。本版只能在核对为误报、写明依据后继续。</p>}
      <label className="mt-3 block text-sm">处理依据（必填）<textarea aria-label={`模型任务 ${jobId} 问题 ${finding.id} 的处理依据`} value={draft.note} required maxLength={4000} rows={3}
        disabled={locked || Boolean(gate?.release)} onChange={event => props.onDraft(key, { ...draft, note: event.target.value })} className="mt-2 w-full min-w-0 rounded border border-line bg-ink p-2" /></label>
    </div>;
  };
  return <section aria-label="最终确认与发布" className="mt-8 min-w-0 max-w-full rounded border border-line bg-panel p-5">
    <h2 className="text-xl font-semibold">最终确认与发布</h2>
    <p className="mt-3 text-sm leading-6 text-mist">先核对候选正文、来源与审核意见，再由管理员确认本版内容。模型建议不会替你批准；保存确认后，还需单独登记发布版本。</p>
    <p className="mt-2 text-sm leading-6 text-brass">玩家可从固定版本的开场进入文字试玩，完整剧本玩法仍在开发。</p>
    <p className="mt-3 break-all text-sm">当前版本：{props.candidate.title} · {props.candidate.content_version}{gate ? ` · ${gate.candidate.player_count} 个角色` : ''}</p>
    {props.error && <p role="alert" className="mt-4 whitespace-pre-wrap break-all rounded border border-red-400/50 p-3 text-sm text-red-200">{props.error}</p>}
    {props.notice && <p role="status" className="mt-4 break-all rounded border border-emerald-500/40 p-3 text-sm">{props.notice}</p>}
    <div className="mt-4 flex flex-wrap gap-3">
      <button type="button" disabled={locked} onClick={() => { if (!locked) props.onReload(); }} className="rounded border border-line px-3 py-2 text-sm disabled:opacity-50">刷新发布检查</button>
      <button type="button" disabled={locked} onClick={() => { if (!locked) props.onRead(); }} className="rounded border border-brass px-3 py-2 text-sm text-brass disabled:opacity-50">{document ? '重新读取候选正文' : '查看候选正文'}</button>
      <Link href="/admin/source-bundles" className="self-center text-sm text-brass underline">核对来源材料</Link>
    </div>
    {(props.loading || props.reading) && <p role="status" className="mt-3 text-sm text-mist">{props.reading ? '正在读取本版候选正文…' : '正在核验发布依据…'}</p>}
    {document && <CandidateContents document={document} />}
    {gate && <>
      <section aria-label="发布前检查" className="mt-6">
        <h3 className="font-semibold">发布前检查</h3>
        <p className="mt-2 text-xs leading-6 text-mist">服务端核对全部 {gate.manual_reports.length} 份人工报告与 {gate.model_reports.length} 项模型任务。新增报告、问题处理或核验依据变化，会使旧确认失效。</p>
        <ul className="mt-3 space-y-2 text-sm">{gate.checks.map((check, index) => <li key={index} className="min-w-0 rounded border border-line p-3">
          <p>{check.passed ? '通过' : '待处理'} · {Object.hasOwn(checkLabels, check.code) ? checkLabels[check.code] : '发布条件'}</p>
          <p className="mt-1 whitespace-pre-wrap break-all text-xs leading-6 text-mist">{check.message}</p>
        </li>)}</ul>
        {!gate.can_approve && !gate.release && <p className="mt-3 text-sm leading-6 text-mist">请先完成上方未通过的检查。人工报告及其问题可在本页上方处理；模型任务可在<Link href="/admin/authoring-jobs" className="text-brass underline">编译工作台</Link>查看。</p>}
      </section>
      <section aria-label="确认前的模型审核建议" className="mt-6">
        <h3 className="font-semibold">逐项核对模型审核建议</h3>
        <p className="mt-2 text-sm leading-6 text-mist">模型可能误报。警告与阻断意见都需要你独立选择处理结果、写明依据；这些说明随最终确认保存，与上方人工报告分别保留。</p>
        {gate.model_reports.map(item => <article key={item.job_id} className="mt-4 min-w-0 rounded border border-line bg-ink/50 p-4">
          <h4 className="text-sm font-semibold">模型任务 #{item.job_id} · 模型审核建议</h4>
          {item.status !== 'SUCCEEDED' || !item.report ? <p className="mt-2 text-sm text-mist">尚无可用于确认的完整模型审核报告，请先在编译工作台查看任务状态。</p> : <>
            <p className="mt-2 whitespace-pre-wrap break-all text-sm leading-6">{item.report.summary}</p>
            <p className="mt-2 text-xs text-mist">覆盖：{item.report.coverage.map(category => categoryLabels[category]).join('、')}</p>
            <ul className="mt-3 space-y-4">{item.report.findings.map(finding => <li key={finding.id} className="min-w-0 border-t border-line pt-4">
              <p className="break-all text-sm font-semibold">{severityLabels[finding.severity]} · {categoryLabels[finding.category]} · {finding.id}</p>
              <p className="mt-2 whitespace-pre-wrap break-all text-sm leading-6">{finding.message}</p>
              <p className="mt-2 break-all text-xs text-mist">位置：{finding.target.collection}{finding.target.id ? ` / ${finding.target.id}` : ''}</p>
              <ul aria-label={`模型任务 ${item.job_id} 问题 ${finding.id} 的来源`} className="mt-2 text-xs text-mist">{finding.sources.map((source, index) => <li key={index} className="break-all">{source.source_id}{source.page != null ? ` · 第 ${source.page} 页` : ''}{source.anchor ? ` · ${source.anchor}` : ''}</li>)}</ul>
              {findingControl(item.job_id, finding)}
            </li>)}</ul>
            {!item.report.findings.length && <p className="mt-3 text-xs text-mist">模型未列出问题，仍需人工核对与确认。</p>}
          </>}
        </article>)}
      </section>
      {gate.approval && <div aria-label="已保存的人工确认" className="mt-6 min-w-0 rounded border border-line p-4 text-sm">
        <p className="font-semibold">{gate.approval.valid && gate.approval.basis_hash === gate.basis_hash ? '已保存人工确认' : '旧确认已失效，请重新核对'}</p>
        <p className="mt-2 break-all text-xs text-mist">提交人 #{gate.approval.submitted_by} · {gate.approval.created_at}</p>
        <p className="mt-2 whitespace-pre-wrap break-all leading-6">{gate.approval.note}</p>
        <details className="mt-3 text-xs"><summary className="cursor-pointer text-brass">查看确认时的模型意见处理</summary><ul className="mt-2 max-h-64 space-y-3 overflow-auto">{gate.approval.model_dispositions.map(item => <li key={modelFindingKey(item.job_id, item.finding_id)} className="break-all">
          <p>模型任务 #{item.job_id} · {item.finding_id} · {item.status === 'DISMISSED' ? '判定误报' : '已知悉并接受'}</p><p className="mt-1 whitespace-pre-wrap leading-6">{item.note}</p>
        </li>)}</ul></details>
        {!gate.release && <p className="mt-3 text-brass">确认已保存，尚未登记发布版本。</p>}
      </div>}
      {!gate.release && <form aria-label="人工确认本版内容" className="mt-6" onSubmit={event => { event.preventDefault(); if (!locked && approval) props.onApprove(approval); }}>
        <label className="block text-sm">最终核对说明（必填）<textarea aria-label="最终核对说明" value={props.note} required rows={3} maxLength={4000} disabled={locked}
          onChange={event => props.onNote(event.target.value)} className="mt-2 w-full min-w-0 rounded border border-line bg-ink p-3" /></label>
        <label className="mt-4 flex items-start gap-3 text-sm leading-6"><input type="checkbox" aria-label="已核对本版正文、来源和模型意见" checked={props.confirmed}
          disabled={locked || !document || !gate.can_approve} onChange={event => props.onConfirmed(event.target.checked)} className="mt-1 shrink-0" />
          <span>我已核对本版正文、来源和模型意见，并对本次人工确认负责。</span></label>
        {!document && <p className="mt-2 text-xs text-mist">请先点击“查看候选正文”，再完成核对与确认。</p>}
        <button type="submit" disabled={locked || !approval} className="mt-4 rounded bg-brass px-4 py-3 text-sm font-semibold text-ink disabled:opacity-50">{props.busy ? '正在保存…' : '确认本版内容'}</button>
      </form>}
      <div className="mt-6 border-t border-line pt-5">
        {gate.release ? <>
          <p className="text-sm font-semibold">已登记发布版本 · {props.candidate.content_version}</p>
          <p className="mt-2 break-all text-xs text-mist">登记人 #{gate.release.submitted_by} · {gate.release.created_at}</p>
          {!gate.release.current_approval_valid && <p className="mt-2 text-sm leading-6 text-mist">当前审核依据已变化，本版不能再新建开场。请创建新的内容版本重新确认；已创建的开场预览仍保留原版本。</p>}
          {gate.release.current_approval_valid && <Link href={`/play/package-preview?release_id=${encodeURIComponent(gate.release.id)}`} className="mt-3 inline-block text-sm text-brass underline">查看本版开场预览</Link>}
        </> : <>
          <p className="text-sm leading-6 text-mist">登记后保存当前候选的固定版本。后续审核变更不会把另一份内容替换进这份发布记录；完整剧本玩法仍在开发。</p>
          <button type="button" disabled={locked || !publication} onClick={() => { if (!locked && publication) props.onPublish(publication); }} className="mt-3 rounded border border-brass px-4 py-3 text-sm text-brass disabled:opacity-50">登记发布版本</button>
        </>}
      </div>
      <details className="mt-5 min-w-0 text-xs text-mist"><summary className="cursor-pointer">查看本次绑定标识</summary>
        <p className="mt-2 break-all">候选包：{props.candidate.package_hash}</p><p className="mt-2 break-all">来源快照：{gate.bundle_hash || '尚未确定'}</p><p className="mt-2 break-all">审核依据：{gate.basis_hash}</p>
        {gate.approval && <p className="mt-2 break-all">人工确认：{gate.approval.approval_hash}</p>}{gate.release && <p className="mt-2 break-all">发布版本：{gate.release.release_hash}</p>}
      </details>
    </>}
  </section>;
}

type WorkspaceProps = { candidate: ReviewCandidate; reviewToken: string; externalLocked: boolean };

function PublicationWorkspace({ candidate, reviewToken, externalLocked }: WorkspaceProps) {
  const [gate, setGate] = useState<PublicationGate>();
  const [document, setDocument] = useState<CandidateDocument>();
  const [drafts, setDrafts] = useState<Record<string, PublicationFindingDraft>>({});
  const [note, setNote] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [reading, setReading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [reload, setReload] = useState(0);
  const managed = useRef<{ gate?: AbortController; document?: AbortController; write?: AbortController; basis?: string; attempts: Partial<Record<'approve' | 'publish', PublicationAttempt>> }>({ attempts: {} });

  useEffect(() => {
    if (externalLocked || managed.current.write) return;
    const requests = managed.current;
    const controller = new AbortController();
    requests.gate?.abort(); requests.gate = controller;
    setLoading(true);
    scriptPublicationService.gate(candidate.id, controller.signal).then(result => {
      if (controller.signal.aborted) return;
      if (!matchingGate(candidate, result)) throw new Error('返回的发布检查与当前候选不一致，请刷新。');
      if (requests.basis && requests.basis !== result.basis_hash) {
        setDrafts({}); setConfirmed(false); setNote(''); requests.attempts = {};
        setNotice('审核依据已变化，请重新核对并确认。');
      }
      requests.basis = result.basis_hash; setGate(result);
    }).catch(cause => { if (!controller.signal.aborted) { setGate(undefined); setError(cause instanceof ScriptPublicationError ? cause.message : '读取发布检查失败，请刷新。'); } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [candidate, reviewToken, externalLocked, reload]);

  useEffect(() => {
    const requests = managed.current;
    return () => { requests.gate?.abort(); requests.document?.abort(); requests.write?.abort(); };
  }, []);

  const read = async () => {
    const requests = managed.current;
    if (externalLocked || requests.write || reading || loading) return;
    requests.document?.abort();
    const controller = new AbortController(); requests.document = controller;
    setReading(true); setError(''); setConfirmed(false); delete requests.attempts.approve;
    try {
      const result = await scriptPublicationService.candidate(candidate.id, controller.signal);
      if (!controller.signal.aborted) {
        if (!matchingDocument(candidate, result)) throw new Error();
        setDocument(result);
      }
    } catch (cause) {
      if (!controller.signal.aborted) { setDocument(undefined); setError(cause instanceof ScriptPublicationError ? cause.message : '读取候选正文失败，请刷新后重试。'); }
    } finally { if (!controller.signal.aborted) setReading(false); }
  };

  const write = async (kind: 'approve' | 'publish', payload: Omit<ApprovePublicationRequest, 'idempotency_key'> | Omit<PublishPackageRequest, 'idempotency_key'>) => {
    const requests = managed.current;
    if (externalLocked || loading || reading || requests.write) return;
    const observed = kind === 'approve' ? approvalPayload(candidate, gate, document, drafts, note, confirmed) : publishPayload(candidate, gate);
    if (!observed || JSON.stringify(observed) !== JSON.stringify(payload)) return;
    const attempt = preparePublicationAttempt(payload, requests.attempts[kind]); requests.attempts[kind] = attempt;
    const controller = new AbortController(); requests.write = controller; requests.gate?.abort();
    setBusy(true); setError(''); setNotice('');
    try {
      if (kind === 'approve') await scriptPublicationService.approve(candidate.id, { ...payload as Omit<ApprovePublicationRequest, 'idempotency_key'>, idempotency_key: attempt.key }, controller.signal);
      else await scriptPublicationService.publish(candidate.id, { ...payload as Omit<PublishPackageRequest, 'idempotency_key'>, idempotency_key: attempt.key }, controller.signal);
      if (controller.signal.aborted) return;
      delete requests.attempts[kind]; setConfirmed(false);
      setNotice(kind === 'approve' ? '人工确认已保存，尚未登记发布版本。' : '发布版本已登记，玩家可从开场进入文字试玩。');
      setGate(undefined);
      try {
        const refreshed = await scriptPublicationService.gate(candidate.id, controller.signal);
        if (!controller.signal.aborted) {
          if (!matchingGate(candidate, refreshed)) throw new Error();
          requests.basis = refreshed.basis_hash; setGate(refreshed);
        }
      } catch {
        if (!controller.signal.aborted) setError('操作已保存，但读取最新状态失败。请刷新发布检查，核对结果后再继续。');
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(cause instanceof ScriptPublicationError ? cause.message : '提交失败，请重试；相同内容会复用本次请求标识。');
        if (cause instanceof ScriptPublicationError && cause.status === 409) {
          setConfirmed(false); setDrafts({}); setGate(undefined); setReload(value => value + 1);
        }
      }
    } finally { if (!controller.signal.aborted) { requests.write = undefined; setBusy(false); } }
  };

  const edit = () => { delete managed.current.attempts.approve; setConfirmed(false); setNotice(''); };
  return <ScriptPublicationView candidate={candidate} gate={gate} document={document} drafts={drafts} note={note} confirmed={confirmed}
    loading={loading || externalLocked} reading={reading} busy={busy} error={error} notice={notice}
    onReload={() => { if (!managed.current.write) { setError(''); setNotice(''); setConfirmed(false); setReload(value => value + 1); } }} onRead={read}
    onDraft={(key, value) => { edit(); setDrafts(current => ({ ...current, [key]: value })); }} onNote={value => { edit(); setNote(value); }}
    onConfirmed={value => { delete managed.current.attempts.approve; setConfirmed(value); setNotice(''); }}
    onApprove={payload => void write('approve', payload)} onPublish={payload => void write('publish', payload)} />;
}

export default function ScriptPublicationPanel(props: WorkspaceProps) {
  return <PublicationWorkspace key={`${props.candidate.id}:${props.candidate.package_hash}`} {...props} />;
}
