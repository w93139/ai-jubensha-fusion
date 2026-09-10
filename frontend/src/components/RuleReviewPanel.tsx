import { useEffect, useRef, useState } from 'react';
import scriptReviewService from '@/services/scriptReviewService';
import type { ReviewCandidate, RuleReview } from '@/types/scriptReview';

const fields: Record<string, string> = {
  truth_ids: '结尾引用的真相编号', phase_id: '开放阶段下限／所属阶段', phase_ids: '可执行阶段', cost: '消耗行动点', points: '本阶段共享行动点',
  advance_policy: '推进和结束要求', origin: '规则来源性质', allowed_character_ids: '获准执行的角色',
  required_action_ids: '必须已完成的动作', required_public_evidence_ids: '必须已公开的证据',
  visibility: '可见范围', character_id: '所属角色', disclosure: '原文分享权限', kind: '事实分类',
};
const values: Record<string, string> = {
  REQUIRE_EXHAUSTED: '行动点用尽后才能推进／结束', ALLOW_REMAINING: '允许有剩余行动点',
  SOURCE_EXPLICIT: '声明来自原材料', EDITORIAL: '编辑补充', PUBLIC: '公开', CHARACTER_PRIVATE: '角色私有',
  MAY_SHARE: '允许分享原文', MUST_SHARE: '要求分享原文', KEEP_PRIVATE: '不得分享原文',
  FACT: '事实', CLAIM: '角色说法', INFERENCE: '推测',
};
const collections: Record<string, string> = {
  'mechanics.phase_budgets': '阶段预算', 'mechanics.actions': '调查动作', knowledge: '知识', evidence: '证据', settlement: '结尾设置',
};

function display(value: unknown): string {
  if (Array.isArray(value)) return value.length ? value.map(item => String(item)).join('、') : '无';
  if (value === null) return '无';
  return typeof value === 'string' ? values[value] || value : String(value);
}

function validRuntimeFacts(facts: RuleReview['runtime_facts']): boolean {
  return Boolean(facts && Object.keys(facts).length === 6 && facts.package_contract === 'script-package/1.2'
    && facts.human_players === 1 && facts.investigation_actor === 'SELECTED_HUMAN_ONLY'
    && facts.action_success_limit === 'ONCE_PER_SESSION' && facts.phase_budget === 'SHARED_NO_CARRY'
    && facts.material_recipient === 'DECLARED_OWNER');
}

export function matchesRuleReview(report: RuleReview | undefined, packageHash: string, bundleHash: string): report is RuleReview {
  return Boolean(report && ['rule-review/1.0', 'rule-review/1.1'].includes(report.schema_version)
    && (report.schema_version === 'rule-review/1.1' ? validRuntimeFacts(report.runtime_facts) : report.runtime_facts === undefined) && report.package_hash === packageHash
    && report.bundle_hash === bundleHash && report.semantic_status === 'UNREVIEWED' && report.publication_ready === false
    && Array.isArray(report.rows) && Number.isInteger(report.offset) && report.offset >= 0
    && Number.isInteger(report.limit) && report.limit >= 1 && report.limit <= 50
    && Number.isInteger(report.row_count) && report.row_count >= 0 && report.row_count <= 15101
    && report.rows.length <= report.limit
    && (report.next_offset === null || (report.next_offset === report.offset + report.limit && report.next_offset < report.row_count))
    && report.rows.every(row => row && row.target && (row.target.collection === 'settlement'
      ? report.schema_version === 'rule-review/1.1' && row.target.id === null : typeof row.target.id === 'string')
      && Object.hasOwn(collections, row.target.collection) && typeof row.label === 'string'
      && row.rules && typeof row.rules === 'object' && !Array.isArray(row.rules)
      && Array.isArray(row.sources) && row.sources.length <= 3
      && Number.isInteger(row.source_count) && row.source_count >= row.sources.length
      && typeof row.sources_truncated === 'boolean' && typeof row.notices_truncated === 'boolean'
      && Array.isArray(row.notices) && row.notices.length <= 100
      && Number.isInteger(row.notice_count) && row.notice_count >= row.notices.length
      && row.sources.every(source => source && typeof source.source_id === 'string' && typeof source.kind === 'string'
        && (source.anchor === null || typeof source.anchor === 'string')
        && (source.page === null || Number.isInteger(source.page)) && typeof source.truncated === 'boolean'
        && ['AVAILABLE', 'NON_TEXT', 'TEXT_LIMIT', 'TEXT_UNAVAILABLE', 'LOCATION_UNAVAILABLE'].includes(source.status)
        && (source.status === 'AVAILABLE' ? typeof source.text === 'string' && source.text.length <= 2000 : source.text === null))
      && row.notices.every(notice => notice && notice.code === 'PUBLIC_PREREQUISITE_ALREADY_REQUIRED_BY_ACTION'
        && typeof notice.action_id === 'string' && typeof notice.evidence_id === 'string')));
}

export function RuleReviewContent({ report }: { report: RuleReview }) {
  return <div className="mt-4 space-y-4">
    {report.schema_version === 'rule-review/1.1' && validRuntimeFacts(report.runtime_facts) && <aside aria-label="当前程序执行的规则" className="rounded border border-line p-4 text-sm leading-6">
      <h3 className="font-semibold">先核对程序实际怎样运行</h3>
      <ul className="mt-2 list-disc space-y-1 pl-5">
        <li>每局只有一名真人；只有你选择的角色能执行调查，AI 不代搜证。</li>
        <li>每个调查动作整局最多成功一次，包括免费动作，无须在草案里重复添加限制字段。</li>
        <li>调查点按阶段共享，不结转到下一阶段。</li>
        <li>私有材料按其声明的角色归属发放，调查者不会自动得到别人的私有材料。</li>
      </ul>
      <p className="mt-2 text-mist">这些是当前程序的固定规则，不是 AI 审核结论，也不能证明本剧本内容或所有行动顺序正确。</p>
    </aside>}
    <p className="text-sm text-mist">共 {report.row_count} 项，本页 {report.rows.length} 项。以下内容尚未完成语义审核。</p>
    {report.rows.map(row => <article key={`${row.target.collection}:${row.target.id}`} className="min-w-0 rounded border border-line p-4">
      <h3 className="break-words font-semibold">{collections[row.target.collection] || row.target.collection} · {row.label}</h3>
      <p className="mt-1 break-all text-xs text-mist">报告位置：{row.target.collection} / {row.target.id}</p>
      {!!row.notice_count && <div role="note" className="mt-3 rounded border border-amber-500/60 p-3 text-sm leading-6">
        <p>可能重复添加条件：材料自身的条件与前置动作的条件重合。请核对原文是否分别要求；不要自动删除。</p>
        <ul className="mt-2 space-y-1">{row.notices.map((notice, index) => <li key={index} className="break-all">
          材料要求公开证据 {notice.evidence_id}，而动作 {notice.action_id} 已要求该证据公开。
        </li>)}</ul>
        {row.notices_truncated && <p>提示较多，仅显示前 {row.notices.length} 项，共 {row.notice_count} 项。</p>}
      </div>}
      <div className="mt-4 grid min-w-0 gap-4 md:grid-cols-2">
        <div className="min-w-0"><h4 className="text-sm font-semibold">候选中的规则</h4>
          <dl className="mt-2 space-y-2 text-sm">{Object.entries(row.rules).flatMap<[string, unknown]>(([key, value]) =>
            key === 'release' && value && typeof value === 'object' ? Object.entries(value) : [[key, value]],
          ).map(([key, value]) => <div key={key} className="min-w-0"><dt className="text-mist">{fields[key] || key}</dt>
            <dd className="mt-1 whitespace-pre-wrap break-all">{display(value)}</dd></div>)}</dl>
        </div>
        <div className="min-w-0"><h4 className="text-sm font-semibold">该项声明引用的原文</h4>
          {row.sources.map((source, index) => <div key={index} className="mt-3 min-w-0">
            <p className="break-all text-xs text-mist">{source.source_id} · {source.anchor || `第 ${source.page} 页`} · {source.kind}</p>
            {source.status === 'AVAILABLE' && source.text !== null
              ? <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded bg-ink p-3 text-xs leading-6">{source.text}</pre>
              : <p className="mt-2 text-sm text-amber-200">{source.status === 'NON_TEXT' ? '图片或非文本资料，请在来源材料页核对原件。'
                : source.status === 'TEXT_LIMIT' ? '来源文字超出本页读取限额，请在来源材料页核对。' : '无法唯一定位或读取这段文字，请在来源材料页核对。'}</p>}
            {source.truncated && <p className="mt-2 text-xs text-amber-200">原文较长，此处仅显示开头；需要核对完整来源。</p>}
          </div>)}
          {row.sources_truncated && <p className="mt-2 text-xs text-amber-200">共 {row.source_count} 处引用，此处仅显示前 {row.sources.length} 处；其余请核对来源材料。</p>}
        </div>
      </div>
    </article>)}
  </div>;
}

type RuleReviewProps = {
  candidate: ReviewCandidate; bundleHash: string; locked: boolean;
};

function RuleReviewSession({ candidate, bundleHash, locked }: RuleReviewProps) {
  const [report, setReport] = useState<RuleReview>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef<AbortController | undefined>(undefined);
  useEffect(() => () => { pending.current?.abort(); }, []);
  const current = matchesRuleReview(report, candidate.package_hash, bundleHash) ? report : undefined;
  const read = async (offset: number) => {
    if (locked || pending.current || !bundleHash) return;
    const controller = new AbortController(); pending.current = controller;
    setLoading(true); setError(''); setReport(undefined);
    try {
      const result = await scriptReviewService.rules(candidate.id, candidate.package_hash, bundleHash, offset, controller.signal);
      if (controller.signal.aborted) return;
      if (!matchesRuleReview(result, candidate.package_hash, bundleHash) || result.offset !== offset) throw new Error('规则结果与当前候选或来源不一致，请重新读取。');
      setReport(result);
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '规则对照读取失败。');
    } finally {
      if (!controller.signal.aborted) { setLoading(false); pending.current = undefined; }
    }
  };
  if (candidate.contract_version !== 'script-package/1.2') return null;
  return <section aria-label="规则与来源逐项核对" className="mt-6 min-w-0 rounded border border-line bg-panel p-5">
    <h2 className="font-semibold">规则与来源逐项核对</h2>
    <p className="mt-2 text-sm leading-6 text-mist">分别检查动作的前置条件和材料自身的开放条件。结构提示不能代替原文核对；读取不修改候选，也不提交审核或批准发布。</p>
    {!bundleHash && <p className="mt-3 text-sm text-mist">请先在人工审核报告中选择本次核对的来源快照。</p>}
    <button type="button" disabled={locked || loading || !bundleHash} onClick={() => read(0)} className="mt-3 rounded border border-brass px-3 py-2 text-sm text-brass disabled:opacity-50">{loading ? '正在核对来源…' : '读取规则对照'}</button>
    {error && <p role="alert" className="mt-3 text-sm text-red-200">{error}</p>}
    {current && <><RuleReviewContent report={current} />
      <div className="mt-4 flex flex-wrap gap-3">
        <button type="button" disabled={locked || loading || current.offset === 0} onClick={() => read(Math.max(0, current.offset - current.limit))} className="rounded border border-line px-3 py-2 text-sm disabled:opacity-50">上一页规则</button>
        <button type="button" disabled={locked || loading || current.next_offset === null} onClick={() => { if (current.next_offset !== null) void read(current.next_offset); }} className="rounded border border-line px-3 py-2 text-sm disabled:opacity-50">下一页规则</button>
      </div>
    </>}
  </section>;
}

export default function RuleReviewPanel(props: RuleReviewProps) {
  // A new candidate/source gets fresh state and aborts the previous request.
  return <RuleReviewSession key={`${props.candidate.id}:${props.candidate.package_hash}:${props.bundleHash}`} {...props} />;
}
