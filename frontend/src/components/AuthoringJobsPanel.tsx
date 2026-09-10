import Link from 'next/link';
import AuthoringRulePlanInput from './AuthoringRulePlanInput';
import type { AuthoringJob, AuthoringJobDraft, AuthoringJobState, AuthoringOutputDiagnostic, AuthoringSourceReference, AuthoringStep } from '@/types/authoringJob';
import type { FrozenSource, SourceBundle, SourceBundleSummary } from '@/types/sourceBundle';

const stateLabels: Record<AuthoringJobState, string> = {
  QUEUED: '等待执行', RUNNING: '正在执行', NEEDS_RECONCILIATION: '等待核对请求结果', BLOCKED: '需要处理问题', COMPLETED: '候选与审核建议已生成', CANCELLED: '已取消',
};
const stepLabels: Record<AuthoringStep, string> = { COMPILE: '编译材料', CANDIDATE: '校验并接收候选', AUDIT: '生成审核建议', DONE: '结束' };
const attemptLabels = { RESERVED: '已预留费用', IN_FLIGHT: '请求已发送', SUCCEEDED: '已完成', FAILED: '失败', UNKNOWN: '结果未知，需核对' };
const errorLabels: Record<string, string> = {
  AUTHORING_CANCELLED: '任务已取消，已发送请求仍需核对结果与费用。',
  AUTHORING_USAGE_RECONCILIATION_REQUIRED: '已有请求结果或费用未知，请先完成核对；不会自动重新调用模型。',
  AUTHORING_JOB_BUDGET_EXCEEDED: '任务预计费用超过 ¥0.10 上限，后续请求未发送。',
  AUTHORING_CONFIGURATION_CHANGED: '执行配置与任务创建时的记录不一致，任务已停止。',
  AUTHORING_PAID_EXECUTION_DISABLED: '付费执行开关未开启，任务未调用模型。',
  AUTHORING_SOURCE_CONTEXT_CHANGED: '所选材料或来源上下文发生变化，请重新核验。',
  AUTHORING_SOURCE_VERIFICATION_FAILED: '候选来源核验未通过，请处理来源问题。',
  AUTHORING_CALL_NOT_RETRYABLE: '已有请求不能重复发送，请查看执行记录并核对结果。',
  AUTHORING_INTEGRITY_FAILED: '任务记录未通过完整性检查，请核对保存的来源与产物。',
  AUTHORING_WORKER_FAILED: '工作进程未能完成任务，请查看执行状态后处理。',
  COMPILER_REPORTED_BLOCKERS: '编译发现阻断问题，请按下方说明核对材料与规则。',
  SOURCE_NOT_IN_BUNDLE: '所选材料不在当前来源快照中，请重新核对选择。',
  ORIGINAL_RELATION_MISSING: '修订或 OCR 材料缺少原件关联。请补齐来源记录后创建新任务。',
  ORIGINAL_RELATION_INVALID: '关联原件不存在或类型不符。请核对来源记录。',
  SOURCE_TYPE_UNSUPPORTED: '所选材料类型暂不支持编译。',
  SOURCE_TEXT_TOO_LARGE: '单份材料超出 16 KiB 文本上限，请先准备更小的来源材料。',
  MATERIAL_TEXT_TOO_LARGE: '所选文本总量超出 48 KiB 上限，请缩小材料范围。',
  SOURCE_TEXT_EMPTY: '所选文本为空，请核对来源材料。',
  SOURCE_INTEGRITY_FAILED: '来源文件缺失、变化或无法读取，请重新核验。',
  SOURCE_LOCATION_NOT_AUTHORIZED: '模型引用的位置超出本次所选材料范围。',
  SUPPLEMENT_PROVENANCE_UNAVAILABLE: '编辑补充缺少可用的来源说明。',
  AUTHORING_CONFIG_INVALID: '模型或费用配置不符合任务要求，请检查配置。',
  AUTHORING_INPUT_TOO_LARGE: '任务输入超出模型调用上限，未继续执行。',
  AUTHORING_RESERVATION_TOO_LARGE: '本步骤预估费用超出上限，未发送请求。',
  AUTHORING_PREPARATION_CHANGED: '准备内容发生变化，任务已停止，请核对版本。',
  AUTHORING_TIMEOUT_USAGE_UNKNOWN: '请求超时且用量未知，需要核对供应商结果，不会自动重发。',
  AUTHORING_CALL_FAILED_USAGE_UNKNOWN: '请求未返回明确结果，需要核对供应商结果，不会自动重发。',
  AUTHORING_USAGE_UNKNOWN: '未取得完整用量，费用与请求结果需要核对。',
  OUTPUT_JSON_INVALID: '模型未返回可用的结构化结果。',
  COMPILER_OUTPUT_INVALID: '编译结果未通过结构校验。',
  COMPILER_DRAFT_INVALID: '模型内容草稿未通过结构校验，请核对内容字段与阻断说明。',
  COMPILER_TEXT_DRAFT_INVALID: '模型摘录包含额外字段、缺失或重复内容，已拒绝接收。',
  COMPILER_RULE_PLAN_MISMATCH: '结果改变了冻结规则草案，已拒绝接收。',
  COMPILER_FROZEN_TEXT_MISMATCH: '结果改变了冻结正文，已拒绝接收。',
  COMPILER_PACKAGE_INVALID: '候选包未通过确定性校验，请核对材料与规则支持范围。',
  COMPILER_INPUT_BINDING_MISMATCH: '编译结果与本任务选定的来源或版本不一致。',
  COMPILER_REFERENCE_UNAUTHORIZED: '编译结果引用了未授权的来源位置。',
  COMPILER_TEXT_NOT_EXTRACTIVE: '编译结果包含无法对应选定原文的内容。',
  AUTHORING_FINISH_INVALID: 'AI 回复未正常结束，结果未被接收，也不会自动重发。',
  AUDIT_INCOMPLETE: 'AI 尚未完成全部审核，已保留草案，不能作为完整审核使用。',
  AUDIT_BOUNDED_OUTPUT_INVALID: '审核报告超过长度限制或缺少完整性标记，已拒绝接收。',
  AUDIT_OUTPUT_INVALID: '模型审核建议未通过结构或来源校验。',
  AUDIT_INDEXED_OUTPUT_INVALID: '审核意见缺少有效的来源选择，或引用编号不属于对应内容，已拒绝接收。',
};

export function authoringSourceUnavailable(source: FrozenSource): string {
  if (source.kind === 'reference') return '后台参考，不用于编译';
  if (!['original', 'ocr', 'revised', 'supplement'].includes(source.kind)) return '暂不支持此类材料';
  if (source.media_type !== 'text/plain' && source.media_type !== 'text/markdown') return '仅可选择纯文本或 Markdown';
  if ((source.kind === 'ocr' || source.kind === 'revised') && !source.original_paths.length) return '缺少原件关联';
  if (source.size_bytes > 16 * 1024) return '超过单份 16 KiB 上限';
  return '';
}

export function canQueueAuthoringJob(bundle: SourceBundle | undefined, draft: AuthoringJobDraft): boolean {
  if (draft.audit_mode !== undefined && (!['TARGET_SOURCE_INDEXES', 'BOUNDED_TARGET_SOURCE_INDEXES', 'DIRECT_BOUNDED_SOURCE_INDEXES', 'STRICT_BOUNDED_SOURCE_INDEXES', 'PORTABLE_STRICT_SOURCE_INDEXES', 'TYPED_STRICT_SOURCE_INDEXES', 'RUNTIME_CONTEXT_SOURCE_INDEXES', 'CITATION_CATALOG'].includes(draft.audit_mode) || draft.compiler_mode !== 'CONFIRM_FROZEN_TEXT')) return false;
  if (draft.compiler_mode !== undefined && (draft.compiler_mode !== 'CONFIRM_FROZEN_TEXT' || !draft.rule_plan)) return false;
  if (draft.rule_plan !== undefined && (draft.package_contract !== 'script-package/1.2' || !draft.rule_plan
    || draft.rule_plan.schema_version !== 'script-package/1.2' || draft.rule_plan.title !== draft.title.trim()
    || draft.rule_plan.content_version !== draft.content_version || draft.rule_plan.player_count !== draft.player_count)) return false;
  if (!bundle || bundle.status !== 'FROZEN' || !draft.title.trim() || draft.title.trim().length > 200
    || (draft.package_contract !== undefined && draft.package_contract !== 'script-package/1.2')
    || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$/.test(draft.content_version)
    || !Number.isInteger(draft.player_count) || draft.player_count < 2 || draft.player_count > 8
    || draft.source_ids.length < 1 || draft.source_ids.length > 20 || new Set(draft.source_ids).size !== draft.source_ids.length) return false;
  const selected = draft.source_ids.map(id => bundle.sources.find(source => source.id === id));
  return selected.every(source => source && !authoringSourceUnavailable(source))
    && selected.reduce((total, source) => total + (source?.size_bytes || 0), 0) <= 48 * 1024;
}

export function canCancelAuthoringJob(job: AuthoringJob): boolean { return job.state === 'QUEUED' || job.state === 'RUNNING'; }
export function canRecoverAuthoringJob(job: AuthoringJob): boolean { return job.state === 'RUNNING' || job.state === 'NEEDS_RECONCILIATION'; }

function safeError(code: string | null): string { return code ? errorLabels[code] || '任务未能继续，请核对来源、执行记录与配置。未知请求不会自动重发。' : ''; }
function money(value: unknown): string {
  return (typeof value === 'string' || typeof value === 'number') && /^\d+(\.\d+)?$/.test(String(value)) ? `¥${value}` : '暂无完整记录';
}
function reservedCost(prepared: Record<string, unknown>): unknown {
  const reservation = prepared.reservation;
  return reservation && typeof reservation === 'object' && !Array.isArray(reservation) ? (reservation as Record<string, unknown>).cost_cny : undefined;
}
const diagnosticLabels: Record<AuthoringOutputDiagnostic['code'], string> = {
  TEXT_NOT_IN_MATERIALS: '正文未原样出现',
  TEXT_OUTSIDE_REFERENCES: '正文不在引用范围',
  INPUT_BINDING_MISMATCH: '候选元数据与冻结输入不一致',
  PACKAGE_SCHEMA_INVALID: '候选包字段缺失、类型错误或包含不支持的字段值',
  PACKAGE_DUPLICATE_ID: '同类实体的标识重复',
  PACKAGE_SOURCE_NOT_FOUND: '引用的来源不在候选清单中',
  PACKAGE_SOURCE_PAGE_OUT_OF_RANGE: '引用页码超出来源页数',
  PACKAGE_SOURCE_LOCATION_MISSING: '来源引用缺少页码或锚点',
  PACKAGE_DUPLICATE_SOURCE_PATH: '同一文件路径被重复声明为来源',
  PACKAGE_ORIGINAL_SOURCE_MISSING: '规范化来源缺少有效的原件关联',
  PACKAGE_INVALID_SOURCE_LINK: '原件不应再关联上游原件',
  PACKAGE_CHARACTER_COUNT_MISMATCH: '角色数量与冻结人数不一致',
  PACKAGE_PHASE_NOT_FOUND: '引用的阶段不存在',
  PACKAGE_PHASE_CYCLE: '阶段存在循环，当前契约仅支持无环的线性阶段',
  PACKAGE_UNREACHABLE_PHASE: '存在无法从初始阶段到达的阶段',
  PACKAGE_INVALID_SETTLEMENT_PHASE: '结算没有绑定最后阶段',
  PACKAGE_DUPLICATE_REFERENCE: '结算或解锁条件存在重复引用',
  PACKAGE_TRUTH_NOT_FOUND: '结算引用的后台真相不存在',
  PACKAGE_INVALID_PUBLIC_SCOPE: '公共材料绑定了私有角色或私有披露规则',
  PACKAGE_INVALID_PRIVATE_SCOPE: '私有材料缺少有效角色归属或使用了公开披露规则',
  PACKAGE_EVIDENCE_NOT_FOUND: '解锁条件引用的线索不存在',
  PACKAGE_UNSATISFIABLE_RELEASE: '禁止公开的线索被用作公开解锁条件',
  PACKAGE_UNREACHABLE_EVIDENCE: '线索依赖存在循环或缺失引用，无法全部解锁',
  PACKAGE_INITIAL_KNOWLEDGE_MISSING: '角色缺少无前置线索条件的初始私有材料',
  AUDIT_CANDIDATE_INVALID: '模型审核所用候选未通过确定性校验',
  AUDIT_COVERAGE_INVALID: '模型审核未完整、唯一覆盖规定的五个维度',
  AUDIT_DUPLICATE_FINDING_ID: '模型审核问题的标识重复',
  AUDIT_TARGET_INVALID: '模型审核问题的目标类型与标识不符合约定',
  AUDIT_TARGET_NOT_FOUND: '模型审核问题引用的候选实体不存在',
  AUDIT_REFERENCE_MISMATCH: '模型审核问题的来源定位与目标实体声明不一致',
  AUDIT_CITATION_SCHEMA_INVALID: '模型返回的引用审核格式不完整或不符合要求',
  AUDIT_CITATION_REFERENCE_INVALID: '模型选择了目录外引用，或在同一意见中混用了不同目标',
  AUDIT_SOURCE_INDEX_UNAVAILABLE: '模型选择的来源编号不在该审核目标的引用范围内',
  AUDIT_BOUNDED_SCHEMA_INVALID: '简短审核的字段缺失、超长或格式不合格',
  AUDIT_SCHEMA_INVALID: '模型审核报告字段缺失、类型错误或包含不支持的字段值',
};
const diagnosticIndexes = {
  sources: '(?:0|[1-9][0-9]?|[1-4][0-9]{2})', characters: '[0-7]', phases: '(?:0|[1-9][0-9]?)',
  knowledge: '(?:0|[1-9][0-9]{0,2}|[1-4][0-9]{3})', evidence: '(?:0|[1-9][0-9]{0,2}|[1-4][0-9]{3})', truth: '(?:0|[1-9][0-9]{0,2})',
  findings: '(?:0|[1-9][0-9]?|1[0-9]{2})',
};
type DiagnosticCollection = keyof typeof diagnosticIndexes;
function indexedDiagnosticPath(path: string, collections: DiagnosticCollection[], suffix = ''): boolean {
  return collections.some(collection => new RegExp(`^/${collection}/${diagnosticIndexes[collection]}${suffix}$`).test(path));
}
function packageDiagnosticPath(code: AuthoringOutputDiagnostic['code'], path: string): boolean {
  switch (code) {
    case 'PACKAGE_SCHEMA_INVALID': return path === '/';
    case 'PACKAGE_DUPLICATE_ID': return indexedDiagnosticPath(path, ['sources', 'characters', 'phases', 'knowledge', 'evidence', 'truth'], '/id');
    case 'PACKAGE_SOURCE_NOT_FOUND':
    case 'PACKAGE_SOURCE_PAGE_OUT_OF_RANGE':
    case 'PACKAGE_SOURCE_LOCATION_MISSING':
      return /^\/(introduction|settlement\/instructions)\/sources\/(0|[1-9][0-9]?)$/.test(path)
        || indexedDiagnosticPath(path, ['characters', 'phases', 'knowledge', 'evidence', 'truth'], '/sources/(0|[1-9][0-9]?)');
    case 'PACKAGE_DUPLICATE_SOURCE_PATH':
    case 'PACKAGE_ORIGINAL_SOURCE_MISSING':
    case 'PACKAGE_INVALID_SOURCE_LINK': return indexedDiagnosticPath(path, ['sources']);
    case 'PACKAGE_CHARACTER_COUNT_MISMATCH': return path === '/player_count';
    case 'PACKAGE_PHASE_NOT_FOUND':
      return path === '/initial_phase_id' || indexedDiagnosticPath(path, ['phases'], '/next_phase_id')
        || indexedDiagnosticPath(path, ['knowledge', 'evidence'], '/release/phase_id');
    case 'PACKAGE_PHASE_CYCLE':
    case 'PACKAGE_UNREACHABLE_PHASE': return path === '/phases';
    case 'PACKAGE_INVALID_SETTLEMENT_PHASE': return path === '/settlement/phase_id';
    case 'PACKAGE_DUPLICATE_REFERENCE': return path === '/settlement/truth_ids' || indexedDiagnosticPath(path, ['knowledge', 'evidence'], '/release');
    case 'PACKAGE_TRUTH_NOT_FOUND': return path === '/settlement/truth_ids';
    case 'PACKAGE_INVALID_PUBLIC_SCOPE':
    case 'PACKAGE_INVALID_PRIVATE_SCOPE': return indexedDiagnosticPath(path, ['knowledge', 'evidence']);
    case 'PACKAGE_EVIDENCE_NOT_FOUND':
    case 'PACKAGE_UNSATISFIABLE_RELEASE': return indexedDiagnosticPath(path, ['knowledge', 'evidence'], '/release');
    case 'PACKAGE_UNREACHABLE_EVIDENCE': return path === '/evidence';
    case 'PACKAGE_INITIAL_KNOWLEDGE_MISSING': return indexedDiagnosticPath(path, ['characters']);
    default: return false;
  }
}
function auditDiagnosticPath(code: AuthoringOutputDiagnostic['code'], path: string): boolean {
  switch (code) {
    case 'AUDIT_BOUNDED_SCHEMA_INVALID':
      return ['/', '/schema_version', '/status', '/summary', '/coverage', '/findings'].includes(path)
        || /^\/findings\/[0-9](\/(id|category|severity|message|target|source_indexes))?$/.test(path)
        || /^\/findings\/[0-9]\/target\/(collection|id)$/.test(path)
        || /^\/findings\/[0-9]\/source_indexes\/(0|[1-9][0-9]?)$/.test(path);
    case 'AUDIT_CANDIDATE_INVALID': return path === '/';
    case 'AUDIT_COVERAGE_INVALID': return path === '/coverage';
    case 'AUDIT_DUPLICATE_FINDING_ID': return indexedDiagnosticPath(path, ['findings'], '/id');
    case 'AUDIT_TARGET_INVALID':
    case 'AUDIT_TARGET_NOT_FOUND': return indexedDiagnosticPath(path, ['findings'], '/target');
    case 'AUDIT_REFERENCE_MISMATCH': return indexedDiagnosticPath(path, ['findings'], '/sources/(0|[1-9][0-9]?)');
    case 'AUDIT_CITATION_SCHEMA_INVALID': return path === '/';
    case 'AUDIT_CITATION_REFERENCE_INVALID': return /^\/findings\/[0-9]\/citation_indexes(?:\/(?:0|[1-9][0-9]?))?$/.test(path);
    case 'AUDIT_SOURCE_INDEX_UNAVAILABLE': return indexedDiagnosticPath(path, ['findings'], '/source_indexes/(0|[1-9][0-9]?)');
    case 'AUDIT_SCHEMA_INVALID':
      return ['/', '/schema_version', '/summary', '/findings'].includes(path)
        || indexedDiagnosticPath(path, ['findings'], '(/(id|category|severity|target|message|sources))?')
        || indexedDiagnosticPath(path, ['findings'], '/target/(collection|id)')
        || indexedDiagnosticPath(path, ['findings'], '/sources/(0|[1-9][0-9]?)(/(source_id|page|anchor))?');
    default: return false;
  }
}
function safeOutputDiagnostics(receipt: Record<string, unknown> | null): AuthoringOutputDiagnostic[] {
  const items = receipt?.output_diagnostics;
  if (!Array.isArray(items)) return [];
  return items.slice(0, 10).filter((item): item is AuthoringOutputDiagnostic => {
    if (!item || typeof item !== 'object' || Array.isArray(item) || Object.keys(item).length !== 2
      || !Object.hasOwn(item, 'code') || !Object.hasOwn(item, 'entity_path')
      || typeof item.entity_path !== 'string' || typeof item.code !== 'string' || !Object.hasOwn(diagnosticLabels, item.code)) return false;
    const path = item.entity_path;
    if (path.length > 64 || /[^a-z0-9_/]/.test(path)) return false;
    if (item.code === 'INPUT_BINDING_MISMATCH') return ['/title', '/schema_version', '/script_key', '/content_version', '/player_count', '/sources'].includes(path);
    if (item.code === 'TEXT_NOT_IN_MATERIALS' || item.code === 'TEXT_OUTSIDE_REFERENCES') {
      return path === '/introduction/text' || path === '/settlement/instructions/text'
        || indexedDiagnosticPath(path, ['characters'], '/name') || indexedDiagnosticPath(path, ['knowledge', 'evidence', 'truth'], '/text');
    }
    return packageDiagnosticPath(item.code as AuthoringOutputDiagnostic['code'], path)
      || auditDiagnosticPath(item.code as AuthoringOutputDiagnostic['code'], path);
  });
}
function OutputDiagnostics({ receipt }: { receipt: Record<string, unknown> | null }) {
  const diagnostics = safeOutputDiagnostics(receipt);
  const finish = receipt?.response_finish;
  const finishLabel = finish === 'length' ? 'AI 回复达到输出长度上限，本次结果未完整返回。'
    : finish === 'content_filter' ? '供应商过滤了本次回复，未取得完整结果。'
    : finish === 'OTHER' ? '供应商返回未识别的结束原因，请结合任务状态核对。' : null;
  if (!diagnostics.length && !finishLabel) return null;
  return <div aria-label="模型输出核验诊断" className="mt-3 min-w-0 text-sm"><h4 className="font-semibold">模型输出核验</h4>{finishLabel && <p className="mt-2">{finishLabel}</p>}
    <ul className="mt-2 max-h-64 max-w-full space-y-2 overflow-auto">{diagnostics.map((item, index) => <li key={index} className="break-all rounded border border-amber-500/40 p-3">
      <p>{diagnosticLabels[item.code]}</p>
      <code className="mt-1 block text-xs text-mist">{item.entity_path}</code>
    </li>)}</ul>
  </div>;
}
function SourceReferences({ sources }: { sources: AuthoringSourceReference[] }) {
  return <ul className="mt-2 space-y-1 text-xs text-mist">{sources.map((source, index) => <li key={index} className="break-all">来源：{source.source_id}{source.page != null ? ` · 第 ${source.page} 页` : ''}{source.anchor ? ` · ${source.anchor}` : ''}</li>)}</ul>;
}

export type AuthoringJobsPanelProps = {
  bundles: SourceBundleSummary[]; bundle?: SourceBundle; bundleHash: string; sourceQuery: string; draft: AuthoringJobDraft;
  jobs: AuthoringJob[]; selectedJobId: string; job?: AuthoringJob;
  loading: boolean; sourceLoading: boolean; detailLoading: boolean; busy: boolean; error: string; notice: string;
  onBundle: (hash: string) => void; onSourceQuery: (query: string) => void; onDraft: (draft: AuthoringJobDraft) => void;
  onQueue: () => void; onSelectJob: (id: string) => void; onReload: () => void;
  onCancel: (job: AuthoringJob) => void; onRecover: (job: AuthoringJob) => void;
};

export default function AuthoringJobsPanel(props: AuthoringJobsPanelProps) {
  const bundle = props.bundle?.bundle_hash === props.bundleHash ? props.bundle : undefined;
  const job = String(props.job?.id) === props.selectedJobId ? props.job : undefined;
  const selectedSize = bundle?.sources.filter(source => props.draft.source_ids.includes(source.id)).reduce((total, source) => total + source.size_bytes, 0) || 0;
  const visibleSources = bundle?.sources.filter(source => source.relative_path.toLowerCase().includes(props.sourceQuery.toLowerCase())) || [];
  const queueDisabled = props.busy || props.loading || props.sourceLoading || !canQueueAuthoringJob(bundle, props.draft);
  return <main className="mx-auto min-w-0 max-w-6xl px-4 pb-16 pt-20 text-paper">
    <p className="text-xs tracking-widest text-brass">管理员 · 剧本准备</p>
    <h1 className="mt-2 text-3xl font-bold">材料编译任务</h1>
    <p className="mt-3 text-sm leading-6 text-mist">选择本次使用的文字材料，排队生成候选包与模型审核建议。所有产物仅管理员可见，仍需人工审核，尚未批准发布。</p>
    {props.error && <p role="alert" className="mt-5 rounded border border-red-400/50 bg-red-950/40 p-4 text-sm text-red-200">{props.error}</p>}
    {props.notice && <p role="status" className="mt-4 rounded border border-emerald-500/40 p-4 text-sm">{props.notice}</p>}
    <div className="mt-6 grid min-w-0 gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <section aria-label="创建编译任务" className="min-w-0 rounded border border-line bg-panel p-5">
        <h2 className="text-lg font-semibold">选择材料并排队</h2>
        <p className="mt-2 text-sm leading-6 text-mist">新建只排队。后台工作进程执行时，编译、审核各最多 ¥0.05，任务最多 ¥0.10。每个步骤仅发送一次，失败不会自动重试。</p>
        <form onSubmit={event => { event.preventDefault(); if (!queueDisabled) props.onQueue(); }}>
          <label className="mt-4 block text-sm">本次规则范围
            <select aria-label="本次规则范围" value={props.draft.compiler_mode ? 'confirm-frozen-text' : props.draft.rule_plan !== undefined ? 'frozen-rule-plan' : props.draft.package_contract || 'script-package/1.1'} disabled={props.busy || props.loading} onChange={event => {
              if (props.busy || props.loading) return;
              const next = { ...props.draft };
              delete next.package_contract;
              delete next.rule_plan;
              delete next.compiler_mode;
              delete next.audit_mode;
              if (['script-package/1.2', 'frozen-rule-plan', 'confirm-frozen-text'].includes(event.target.value)) next.package_contract = 'script-package/1.2';
              if (['frozen-rule-plan', 'confirm-frozen-text'].includes(event.target.value)) next.rule_plan = null;
              if (event.target.value === 'confirm-frozen-text') {
                next.compiler_mode = 'CONFIRM_FROZEN_TEXT';
                next.audit_mode = 'CITATION_CATALOG';
              }
              props.onDraft(next);
            }} className="mt-2 w-full rounded border border-line bg-ink p-3">
              <option value="script-package/1.1">阶段阅读与资料问答</option>
              <option value="script-package/1.2">含行动点与条件搜证</option>
              <option value="frozen-rule-plan">固定规则草案，只摘录原文</option>
              <option value="confirm-frozen-text">固定规则和正文，只检查内容</option>
            </select>
          </label>
          {props.draft.rule_plan !== undefined && <AuthoringRulePlanInput
            key={JSON.stringify([props.bundleHash, props.draft.source_ids, props.draft.title, props.draft.content_version, props.draft.player_count, props.draft.compiler_mode, props.draft.audit_mode])}
            disabled={props.busy || props.loading} plan={props.draft.rule_plan}
            preserveText={props.draft.compiler_mode === 'CONFIRM_FROZEN_TEXT'}
            onPlan={rule_plan => props.onDraft({ ...props.draft, rule_plan })} />}
          {props.draft.package_contract === 'script-package/1.2' && <p className="mt-2 text-xs leading-6 text-mist">所选来源须写明每阶段的共享额度、调查消耗和前置条件。缺少规则时任务会停下来报告问题，不会自动补编。</p>}
          <label className="mt-4 block text-sm">来源快照
            <select aria-label="来源快照" value={props.bundleHash} disabled={props.busy || props.loading} onChange={event => props.onBundle(event.target.value)} className="mt-2 w-full rounded border border-line bg-ink p-3">
              <option value="">请选择来源快照</option>
              {props.bundles.map(item => <option key={item.bundle_hash} value={item.bundle_hash} disabled={item.status !== 'FROZEN'}>{item.edition || item.bundle_hash.slice(0, 12)} · {item.status === 'FROZEN' ? `${item.file_count} 份文件` : '记录损坏'}</option>)}
            </select>
          </label>
          {props.sourceLoading && <p role="status" className="mt-3 text-sm text-mist">正在读取来源元数据…</p>}
          {bundle && <div className="mt-4 min-w-0">
            <label htmlFor="authoring-source-query" className="text-sm">查找材料</label>
            <input id="authoring-source-query" value={props.sourceQuery} onChange={event => props.onSourceQuery(event.target.value)} className="mt-2 w-full rounded border border-line bg-ink p-2 text-sm" placeholder="按文件名筛选" />
            <p className="mt-2 text-xs leading-6 text-mist">已选 {props.draft.source_ids.length} / 20 份，{(selectedSize / 1024).toFixed(1)} / 48 KiB。单份最多 16 KiB。这里只读取文件信息，正文不会自动填入表单。</p>
            <ul aria-label="可选来源材料" className="mt-3 max-h-72 min-w-0 space-y-2 overflow-auto">
              {visibleSources.map(source => {
                const checked = props.draft.source_ids.includes(source.id);
                const reason = authoringSourceUnavailable(source);
                const limitReached = !checked && (props.draft.source_ids.length >= 20 || selectedSize + source.size_bytes > 48 * 1024);
                const disabled = props.busy || Boolean(reason) || limitReached;
                return <li key={source.id} className="rounded border border-line p-3"><label className={`flex min-w-0 items-start gap-3 ${disabled ? 'opacity-60' : ''}`}>
                  <input type="checkbox" checked={checked} disabled={disabled} aria-label={`选择 ${source.relative_path}`} className="mt-1 shrink-0" onChange={() => {
                    if (disabled) return;
                    props.onDraft({ ...props.draft, source_ids: checked ? props.draft.source_ids.filter(id => id !== source.id) : [...props.draft.source_ids, source.id] });
                  }} />
                  <span className="min-w-0"><span className="block break-all text-sm">{source.relative_path}</span><span className="mt-1 block text-xs text-mist">{(source.size_bytes / 1024).toFixed(1)} KiB{reason ? ` · ${reason}` : limitReached ? ' · 已达选择上限' : ''}</span></span>
                </label></li>;
              })}
            </ul>
            {!visibleSources.length && <p className="mt-3 text-sm text-mist">没有匹配的材料。</p>}
            <Link href="/admin/source-bundles" className="mt-3 inline-block text-xs text-brass underline">在来源核验页查看材料</Link>
          </div>}
          <label className="mt-4 block text-sm">候选标题<input aria-label="候选标题" required maxLength={200} value={props.draft.title} disabled={props.busy} onChange={event => props.onDraft({ ...props.draft, title: event.target.value })} className="mt-2 w-full rounded border border-line bg-ink p-3" /></label>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <label className="min-w-0 text-sm">内容版本<input aria-label="内容版本" required maxLength={96} value={props.draft.content_version} disabled={props.busy} onChange={event => props.onDraft({ ...props.draft, content_version: event.target.value })} placeholder="例如 0.1.0" className="mt-2 w-full rounded border border-line bg-ink p-3" /><span className="mt-1 block text-xs text-mist">使用英文字母、数字、点、短横线或下划线。</span></label>
            <label className="min-w-0 text-sm">角色人数<select aria-label="角色人数" value={props.draft.player_count} disabled={props.busy} onChange={event => props.onDraft({ ...props.draft, player_count: Number(event.target.value) })} className="mt-2 w-full rounded border border-line bg-ink p-3">{[2, 3, 4, 5, 6, 7, 8].map(count => <option key={count} value={count}>{count} 人</option>)}</select></label>
          </div>
          <button type="submit" disabled={queueDisabled} className="mt-5 rounded bg-brass px-4 py-3 text-sm font-semibold text-ink disabled:opacity-50">{props.busy ? '正在操作…' : '加入编译队列'}</button>
        </form>
      </section>
      <section aria-label="编译任务列表" className="min-w-0 rounded border border-line bg-panel p-5">
        <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-lg font-semibold">任务记录</h2><button disabled={props.busy || props.loading} onClick={props.onReload} className="rounded border border-line px-3 py-2 text-sm disabled:opacity-50">刷新任务</button></div>
        <p className="mt-2 text-xs leading-6 text-mist">显示最近 100 项；等待或执行中的任务每 3 秒更新状态。</p>
        {props.loading && <p role="status" className="mt-4 text-sm text-mist">正在读取任务列表…</p>}
        {!props.loading && !props.jobs.length && <p className="mt-5 text-sm text-mist">还没有编译任务。选择来源文字后可创建第一项任务。</p>}
        <ul className="mt-4 max-h-[44rem] min-w-0 space-y-3 overflow-auto">{props.jobs.slice(0, 100).map(item => <li key={item.id}><button disabled={props.busy} onClick={() => props.onSelectJob(String(item.id))} className={`w-full min-w-0 rounded border p-3 text-left ${String(item.id) === props.selectedJobId ? 'border-brass' : 'border-line'}`}>
          <span className="block break-all text-sm font-semibold">{item.title} · {item.content_version}</span><span className="mt-2 block text-xs text-mist">#{item.id} · {stateLabels[item.state]} · {stepLabels[item.step]}</span>
        </button></li>)}</ul>
      </section>
    </div>
    {props.detailLoading && <p role="status" className="mt-5 text-sm text-mist">正在读取任务详情…</p>}
    {job && <section aria-label="编译任务详情" className="mt-6 min-w-0 rounded border border-line bg-panel p-5">
      <h2 className="break-all text-xl font-semibold">{job.title} · 任务 #{job.id}</h2>
      {job.model_snapshot.schema_version === 'authoring-model/1.3' && <p className="mt-3 text-sm leading-6 text-brass">本任务使用固定规则草案，AI 只摘录原文。草案仍需人工审核。</p>}
      {['authoring-model/1.4', 'authoring-model/1.5', 'authoring-model/1.6', 'authoring-model/1.7', 'authoring-model/1.8', 'authoring-model/1.9', 'authoring-model/1.10', 'authoring-model/1.11', 'authoring-model/1.12'].includes(String(job.model_snapshot.schema_version)) && <p className="mt-3 text-sm leading-6 text-brass">本任务固定规则和正文，AI 只检查内容。草案仍需人工审核。</p>}
      {job.model_snapshot.schema_version === 'authoring-model/1.12' && <p className="mt-2 text-sm text-mist">审核只选择已有引用，程序核对来源；真实模型效果尚待验证，仍需人工审核。</p>}
      {job.model_snapshot.schema_version === 'authoring-model/1.11' && <p className="mt-2 text-sm text-mist">审核已提供当前程序规则供核对，仍可能误判；不会自动批准或发布。</p>}
      <p className="mt-3 text-sm">{stateLabels[job.state]} · {stepLabels[job.step]} · 记录版本 {job.revision}</p>
      <p className="mt-2 break-all text-xs text-mist">模型：{job.model_snapshot.provider} / {job.model_snapshot.model} · 费率版本：{job.model_snapshot.pricing_version}</p>
      <p className="mt-2 text-sm">已计入费用：{money(job.charged_cost_cny)} · 单步骤最多 ¥0.05，任务最多 ¥0.10</p>
      <p className="mt-2 text-xs leading-6 text-mist">费用为本地记录；请求用量未知时可能按预留计入，最终以供应商核对结果为准。任务完成仅表示生成候选与模型审核建议，尚未批准发布。</p>
      {job.error_code && <p className="mt-4 rounded border border-amber-500/40 p-3 text-sm text-amber-100">{safeError(job.error_code)}</p>}
      {job.state === 'NEEDS_RECONCILIATION' && <p className="mt-3 text-sm leading-6 text-amber-100">已有请求结果或费用需要核对。恢复不会重发结果未知的请求；只有尚未发送的步骤可以继续。</p>}
      <div className="mt-4 flex flex-wrap gap-3">
        {canCancelAuthoringJob(job) && <button disabled={props.busy || props.detailLoading} onClick={() => { if (!props.busy && !props.detailLoading) props.onCancel(job); }} className="rounded border border-red-400/50 px-3 py-2 text-sm disabled:opacity-50">取消未完成任务</button>}
        {canRecoverAuthoringJob(job) && <button disabled={props.busy || props.detailLoading} onClick={() => { if (!props.busy && !props.detailLoading) props.onRecover(job); }} className="rounded border border-brass px-3 py-2 text-sm text-brass disabled:opacity-50">仅恢复未发送步骤</button>}
        {job.candidate_version_id != null && <Link href={`/admin/script-reviews?version_id=${job.candidate_version_id}`} className="rounded border border-line px-3 py-2 text-sm text-brass">查看候选 #{job.candidate_version_id} 的人工审核记录</Link>}
      </div>
      <details className="mt-4 min-w-0 text-xs text-mist"><summary className="cursor-pointer">查看来源与任务时间</summary><p className="mt-2 break-all">来源快照：{job.bundle_hash}</p><ul className="mt-2 max-h-48 space-y-1 overflow-auto">{job.source_ids.map(id => <li key={id} className="break-all">来源：{id}</li>)}</ul>{job.source_report_hash && <p className="mt-2 break-all">核验报告：{job.source_report_hash}</p>}<p className="mt-2 break-all">创建：{job.created_at} · 更新：{job.updated_at}</p></details>
      <div className="mt-6 space-y-5">{job.attempts.map(attempt => <article key={attempt.id} className="min-w-0 rounded border border-line bg-ink/50 p-4">
        <h3 className="font-semibold">{stepLabels[attempt.step]} · {attemptLabels[attempt.status]}</h3>
        <p className="mt-2 text-xs text-mist">步骤估算：{money(reservedCost(attempt.prepared) ?? attempt.receipt?.estimated_cost_cny)} · 计入费用：{money(attempt.receipt?.charged_cost_cny)}</p>
        {attempt.error_code && <p className="mt-3 text-sm text-amber-100">{safeError(attempt.error_code)}</p>}
        <OutputDiagnostics receipt={attempt.receipt} />
        {attempt.step === 'COMPILE' && attempt.output && 'blockers' in attempt.output && <>
          <p className="mt-3 text-sm">{attempt.output.status === 'CANDIDATE' ? '编译返回候选包，是否接收以任务记录为准。' : '编译发现需要处理的问题。'}</p>
          <ul className="mt-3 max-h-96 space-y-3 overflow-auto">{attempt.output.blockers.map((blocker, index) => <li key={index} className="rounded border border-line p-3"><p className="whitespace-pre-wrap break-words text-sm leading-6">{blocker.message}</p><SourceReferences sources={blocker.sources} /></li>)}</ul>
        </>}
        {attempt.step === 'AUDIT' && attempt.output && 'findings' in attempt.output && <>
          <h4 className="mt-4 font-semibold text-brass">模型审核建议</h4>
          <p className="mt-2 text-xs text-mist">模型生成的发现尚未经人工确认，不代表人工批准。</p>
          <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-6">{attempt.output.summary}</p>
          <ul className="mt-3 max-h-96 space-y-3 overflow-auto">{attempt.output.findings.map(finding => <li key={finding.id} className="rounded border border-line p-3"><p className="break-all text-xs text-mist">{finding.severity} · {finding.category} · {finding.id}</p><p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6">{finding.message}</p><p className="mt-2 break-all text-xs text-mist">位置：{finding.target.collection}{finding.target.id ? ` / ${finding.target.id}` : ''}</p><SourceReferences sources={finding.sources} /></li>)}</ul>
          {!attempt.output.findings.length && <p className="mt-3 text-sm text-mist">本次模型建议未列出问题，仍需人工审核。</p>}
        </>}
      </article>)}</div>
    </section>}
  </main>;
}
