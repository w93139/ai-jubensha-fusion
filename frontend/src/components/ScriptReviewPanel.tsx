import Link from 'next/link';
import dynamic from 'next/dynamic';
import type { CandidateReview, FindingDraft, FindingStatus, ReviewCandidate, ReviewCategory, ReviewFinding, ScriptAudit, ScriptAuditReport } from '@/types/scriptReview';
import type { SourceBundleSummary } from '@/types/sourceBundle';

const ScriptPublicationPanel = dynamic(() => import('./ScriptPublicationPanel'), { ssr: false });
const RuleReviewPanel = dynamic(() => import('./RuleReviewPanel'), { ssr: false });

const categoryLabels: Record<ReviewCategory, string> = {
  PROVENANCE: '来源', TIMELINE: '时间线', EVIDENCE: '证据链', KNOWLEDGE_BOUNDARY: '知识边界', PLAYABILITY: '可玩性',
};
const statusLabels: Record<FindingStatus, string> = { OPEN: '待处理', ACKNOWLEDGED: '已知悉', DISMISSED: '判定误报' };
const severityLabels = { BLOCKER: '阻断', WARNING: '警告', INFO: '提示' };

export const fictionalAuditExample: ScriptAuditReport = {
  schema_version: 'script-audit/1.0',
  summary: '完全虚构的格式示例；请根据实际核对结果填写，不可直接提交。',
  coverage: ['PROVENANCE'],
  findings: [{
    id: 'fictional-finding', category: 'PROVENANCE', severity: 'WARNING',
    target: { collection: 'introduction', id: null },
    message: '虚构示例：需要核对一处来源定位。此处不包含剧本正文。',
    sources: [{ source_id: 'replace-with-observed-source-id', anchor: 'L1' }],
  }],
};

export function auditSchemaForPackage(contract: string): ScriptAuditReport['schema_version'] {
  if (contract === 'script-package/1.3') return 'script-audit/1.2';
  return contract === 'script-package/1.2' ? 'script-audit/1.1' : 'script-audit/1.0';
}

export function auditExampleForPackage(contract: string): ScriptAuditReport {
  return { ...fictionalAuditExample, schema_version: auditSchemaForPackage(contract) };
}

export function findingKey(audit: ScriptAudit, finding: ReviewFinding): string { return `${audit.id}:${finding.id}`; }

export function latestDisposition(audit: ScriptAudit, finding: ReviewFinding) {
  return audit.dispositions.filter(item => item.finding_id === finding.id)
    .reduce<(typeof audit.dispositions)[number] | undefined>((latest, item) => !latest || item.revision > latest.revision ? item : latest, undefined);
}

export function canSubmitDisposition(finding: ReviewFinding, draft: FindingDraft | undefined): boolean {
  return Boolean(draft?.note.trim() && (draft.status === 'OPEN' || draft.status === 'DISMISSED' || (draft.status === 'ACKNOWLEDGED' && finding.severity !== 'BLOCKER')));
}

export type ScriptReviewPanelProps = {
  candidates: ReviewCandidate[];
  selectedId: string;
  bundles: SourceBundleSummary[];
  bundleHash: string;
  review?: CandidateReview;
  reportText: string;
  drafts: Record<string, FindingDraft>;
  loading: boolean;
  busy: boolean;
  error: string;
  notice: string;
  onSelect: (id: string) => void;
  onBundle: (hash: string) => void;
  onReport: (text: string) => void;
  onReload: () => void;
  onSubmitReport: () => void;
  onDraft: (key: string, draft: FindingDraft) => void;
  onSubmitDisposition: (audit: ScriptAudit, finding: ReviewFinding, draft: FindingDraft) => void;
};

export default function ScriptReviewPanel(props: ScriptReviewPanelProps) {
  const candidate = props.candidates.find(item => String(item.id) === props.selectedId);
  const review = props.review?.candidate.id === candidate?.id && props.review?.candidate.package_hash === candidate?.package_hash ? props.review : undefined;
  const selectedBundle = props.bundles.find(item => item.bundle_hash === props.bundleHash && item.status === 'FROZEN' && item.script_key === candidate?.script_key);
  const locked = props.busy || props.loading;
  const submitDisabled = locked || !review || !selectedBundle || !props.reportText.trim();
  return <main className="mx-auto max-w-6xl px-4 pb-16 pt-20 text-paper">
    <p className="text-xs tracking-widest text-brass">管理员 · 剧本准备</p>
    <h1 className="mt-2 text-3xl font-bold">剧本审核记录</h1>
    <p className="mt-3 text-sm leading-6 text-mist">记录人工核对发现的问题，并逐项保留处理说明。审核内容仅管理员可见；人工审核记录未运行自动 Audit，也未批准发布，最终确认与发布需在下方单独操作。</p>
    {props.error && <p role="alert" className="mt-5 rounded border border-red-400/50 bg-red-950/40 p-4 text-red-200">{props.error}</p>}
    {props.notice && <p role="status" className="mt-4 rounded border border-emerald-500/40 p-4 text-sm">{props.notice}</p>}
    <div className="mt-6 flex flex-wrap items-end gap-3">
      <label className="min-w-0 flex-1 text-sm">候选剧本版本
        <select aria-label="候选剧本版本" value={props.selectedId} disabled={props.busy} onChange={event => props.onSelect(event.target.value)} className="mt-2 w-full rounded border border-line bg-panel p-3">
          <option value="">请选择候选版本</option>
          {props.candidates.map(item => <option key={item.id} value={item.id}>{item.title} · {item.content_version}</option>)}
        </select>
      </label>
      <button disabled={locked} onClick={props.onReload} className="rounded border border-line px-4 py-3 text-sm disabled:opacity-50">刷新记录</button>
    </div>
    {props.loading && <p role="status" className="mt-4 text-sm text-mist">正在读取候选和审核记录…</p>}
    {!props.loading && !props.candidates.length && !props.error && <p className="mt-8 text-mist">还没有候选剧本包。完成候选接收后，这里会显示可审核的版本。</p>}
    {candidate && <>
      <section aria-label="新增人工审核报告" className="mt-6 min-w-0 rounded border border-line bg-panel p-5">
        <h2 className="font-semibold">新增人工审核报告</h2>
        <p className="mt-2 text-sm leading-6 text-mist">报告绑定当前候选包与来源快照，提交时重新核验来源。阻断问题的实际内容修复需要新的候选版本；“判定误报”必须说明依据。</p>
        {review && <details className="mt-4 min-w-0 text-sm text-mist"><summary className="cursor-pointer">候选实体与来源标识</summary>
          <p className="mt-3 leading-6">下列标识来自当前候选，仅包含位置与文件信息。填写报告时可据此核对目标、来源和锚点；<Link href="/admin/source-bundles" className="text-brass underline">查看来源材料</Link>。</p>
          <pre aria-label="候选实体与来源元数据" className="mt-3 max-h-80 max-w-full overflow-auto whitespace-pre-wrap break-all rounded bg-ink p-3 text-xs leading-6">{JSON.stringify({ references: review.references || [], source_files: review.source_files || [] }, null, 2)}</pre>
        </details>}
        <form onSubmit={event => { event.preventDefault(); if (!submitDisabled) props.onSubmitReport(); }}>
          <label className="mt-4 block text-sm">本次核对的来源快照
            <select aria-label="本次核对的来源快照" value={props.bundleHash} disabled={locked} onChange={event => props.onBundle(event.target.value)} className="mt-2 w-full rounded border border-line bg-ink p-3">
              <option value="">请选择同一剧本的来源快照</option>
              {props.bundles.filter(item => item.script_key === candidate.script_key).map(item => <option key={item.bundle_hash} value={item.bundle_hash} disabled={item.status !== 'FROZEN'}>{item.edition || item.bundle_hash.slice(0, 12)} · {item.status === 'FROZEN' ? `${item.file_count} 份文件` : '记录损坏'}</option>)}
            </select>
          </label>
          <details className="mt-4 text-sm text-mist"><summary className="cursor-pointer">查看完全虚构的 JSON 格式示例</summary><pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded bg-ink p-3 text-xs leading-6">{JSON.stringify(auditExampleForPackage(candidate.contract_version), null, 2)}</pre></details>
          {candidate.contract_version === 'script-package/1.2' && <p className="mt-3 text-xs leading-6 text-mist">本版还需核对共享行动点和调查前置条件；可从上方元数据选择对应的调查动作或阶段预算作为问题位置。</p>}
          <label htmlFor="audit-report" className="mt-4 block text-sm">结构化人工审核报告（JSON）</label>
          <textarea id="audit-report" rows={10} value={props.reportText} disabled={locked} onChange={event => props.onReport(event.target.value)} placeholder="填写实际核对结果；示例中的标识需要替换为当前候选与来源中存在的标识。" className="mt-2 w-full min-w-0 rounded border border-line bg-ink p-3 font-mono text-sm" />
          <button type="submit" disabled={submitDisabled} className="mt-3 rounded bg-brass px-4 py-3 text-sm font-semibold text-ink disabled:opacity-50">{props.busy ? '正在保存…' : '保存人工审核报告'}</button>
        </form>
      </section>
      {review && candidate.contract_version === 'script-package/1.2' && <RuleReviewPanel
        key={`${candidate.id}:${candidate.package_hash}:${selectedBundle?.bundle_hash || ''}`}
        candidate={candidate} bundleHash={selectedBundle?.bundle_hash || ''} locked={locked} />}
      <section aria-label="已有审核记录" className="mt-7 min-w-0">
        <h2 className="text-xl font-semibold">已有审核记录</h2>
        {review && !review.audits.length && <p className="mt-4 text-sm text-mist">这个候选版本还没有人工审核记录。</p>}
        {review?.audits.filter(audit => audit.version_id === candidate.id && audit.package_hash === candidate.package_hash).map(audit => <article key={audit.id} className="mt-4 min-w-0 rounded border border-line bg-panel p-5">
          <h3 className="font-semibold">审核记录 #{audit.id} · 处理版本 {audit.revision}</h3>
          <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6">{audit.report.summary}</p>
          <p className="mt-2 text-xs text-mist">覆盖：{audit.report.coverage.map(category => categoryLabels[category]).join('、')} · 待处理阻断 {audit.open_blockers} · 待处理警告 {audit.open_warnings}</p>
          <p className="mt-2 text-xs text-mist">人工记录，尚未批准发布。</p>
          <details className="mt-3 text-xs text-mist"><summary className="cursor-pointer">查看绑定的版本标识</summary><p className="mt-2 break-all">候选包：{audit.package_hash}</p><p className="mt-2 break-all">来源快照：{audit.bundle_hash}</p><p className="mt-2 break-all">来源核验：{audit.source_report_hash}</p><p className="mt-2 break-all">审核报告：{audit.audit_hash}</p></details>
          <ul className="mt-4 space-y-4">
            {audit.report.findings.map(finding => {
              const key = findingKey(audit, finding);
              const latest = latestDisposition(audit, finding);
              const draft = props.drafts[key] || { status: latest?.status || 'OPEN', note: '' };
              const disabled = locked || !canSubmitDisposition(finding, draft);
              return <li key={finding.id} className="min-w-0 rounded border border-line bg-ink/50 p-4">
                <h4 className="break-words text-sm font-semibold">{severityLabels[finding.severity]} · {categoryLabels[finding.category]} · {finding.id}</h4>
                <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6">{finding.message}</p>
                <p className="mt-2 break-words text-xs text-mist">位置：{finding.target.collection}{finding.target.id ? ` / ${finding.target.id}` : ''}</p>
                <ul aria-label={`问题 ${finding.id} 的来源`} className="mt-2 space-y-1 text-xs text-mist">{finding.sources.map((source, index) => <li key={index} className="break-all">来源：{source.source_id}{source.page != null ? ` · 第 ${source.page} 页` : ''}{source.anchor ? ` · ${source.anchor}` : ''}</li>)}</ul>
                <p className="mt-3 text-sm">最新状态：{statusLabels[latest?.status || 'OPEN']}</p>
                {latest && <p className="mt-1 whitespace-pre-wrap break-words text-sm text-mist">处理说明：{latest.note}</p>}
                {latest && <details className="mt-3 min-w-0 text-xs text-mist"><summary className="cursor-pointer">查看此问题的全部处理历史</summary>
                  <ol aria-label={`问题 ${finding.id} 的处理历史`} className="mt-3 max-h-64 max-w-full space-y-3 overflow-auto">
                    {audit.dispositions.filter(item => item.finding_id === finding.id).sort((left, right) => left.revision - right.revision).map(item => <li key={item.id} className="rounded border border-line p-3">
                      <p>处理版本 {item.revision} · 提交人 #{item.submitted_by} · {statusLabels[item.status]}</p>
                      <p className="mt-2 whitespace-pre-wrap break-words leading-6">{item.note}</p>
                    </li>)}
                  </ol>
                </details>}
                <form aria-label={`处理问题 ${finding.id}`} onSubmit={event => { event.preventDefault(); if (!disabled) props.onSubmitDisposition(audit, finding, draft); }} className="mt-3">
                  <label className="block text-xs">处理结果
                    <select aria-label={`问题 ${finding.id} 的处理结果`} value={draft.status} disabled={locked} onChange={event => props.onDraft(key, { ...draft, status: event.target.value as FindingStatus })} className="mt-2 w-full rounded border border-line bg-ink p-2 text-sm">
                      <option value="OPEN">待处理</option>
                      <option value="ACKNOWLEDGED" disabled={finding.severity === 'BLOCKER'}>已知悉（仅警告与提示）</option>
                      <option value="DISMISSED">判定误报（需依据）</option>
                    </select>
                  </label>
                  <label className="mt-3 block text-xs">处理说明（必填）<textarea aria-label={`问题 ${finding.id} 的处理说明`} required maxLength={4000} rows={3} value={draft.note} disabled={locked} onChange={event => props.onDraft(key, { ...draft, note: event.target.value })} className="mt-2 w-full rounded border border-line bg-ink p-2 text-sm" /></label>
                  <button type="submit" disabled={disabled} className="mt-3 rounded border border-brass px-3 py-2 text-sm text-brass disabled:opacity-50">保存此项处理</button>
                </form>
              </li>;
            })}
          </ul>
          {!audit.report.findings.length && <p className="mt-4 text-sm text-mist">本报告未记录问题；仍需后续审核与发布门禁。</p>}
        </article>)}
      </section>
      <ScriptPublicationPanel candidate={candidate} externalLocked={locked}
        reviewToken={JSON.stringify(review?.audits.map(item => [item.id, item.audit_hash, item.revision]) || [])} />
    </>}
  </main>;
}
