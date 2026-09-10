import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import packageFlowService, { PackageFlowError, preparePackageFlowAttempt } from '@/services/packageFlowService';
import type { PackageFlowAttempt } from '@/services/packageFlowService';
import type { FlowMaterial, FlowMaterialCollection, PackageFlow, PackageFlowAction } from '@/types/packageFlow';

export function matchesFlowRoute(view: PackageFlow, flowId: string, openingSessionId: string): boolean {
  return Boolean((flowId || openingSessionId) && /^flow-[0-9a-f]{32}$/.test(view.flow_id)
    && (!flowId || view.flow_id === flowId) && (!openingSessionId || view.opening_session_id === openingSessionId)
    && view.status === 'RULES_PREVIEW' && view.runtime_ready === false && Number.isSafeInteger(view.revision) && view.revision >= 0);
}

export function sameFlowBinding(left: PackageFlow, right: PackageFlow): boolean {
  return left.flow_id === right.flow_id && left.opening_session_id === right.opening_session_id && left.release_id === right.release_id
    && left.version_id === right.version_id && left.package_hash === right.package_hash && left.selected_character_id === right.selected_character_id;
}

export function canPerformFlowAction(view: PackageFlow, action: PackageFlowAction): boolean {
  if (action.action === 'ADVANCE_PHASE') return view.can_advance && !view.phase_complete;
  if (action.action !== 'SHARE_MATERIAL' || !action.target || (action.target.collection !== 'knowledge' && action.target.collection !== 'evidence')) return false;
  const publicItems = action.target.collection === 'knowledge' ? view.public_knowledge : view.public_evidence;
  const privateItems = action.target.collection === 'knowledge' ? view.private_knowledge : view.private_evidence;
  const observed = privateItems.find(item => item.id === action.target.id);
  return Boolean(observed?.can_share && (observed.disclosure === 'MAY_SHARE' || observed.disclosure === 'MUST_SHARE')
    && !publicItems.some(item => item.id === action.target.id));
}

const disclosureLabels = { PUBLIC: '公开信息', MAY_SHARE: '允许自行分享', MUST_SHARE: '按规则需分享', KEEP_PRIVATE: '需保密' };
const kindLabels = { FACT: '已知事实', CLAIM: '角色说法', INFERENCE: '推测' };

export type PackageFlowPanelProps = {
  flowId: string;
  openingSessionId: string;
  view?: PackageFlow | null;
  loading: boolean;
  busy: boolean;
  requiresRefresh: boolean;
  error: string;
  notice: string;
  onReload: () => void;
  onCreate: () => void;
  onAction: (action: PackageFlowAction) => void;
};

export default function PackageFlowPanel(props: PackageFlowPanelProps) {
  const view = props.view && matchesFlowRoute(props.view, props.flowId, props.openingSessionId) ? props.view : undefined;
  const locked = props.loading || props.busy || props.requiresRefresh;
  const canCreate = !locked && props.view === null && !props.flowId && Boolean(props.openingSessionId);
  const actor = view?.characters.find(item => item.id === view.selected_character_id);
  const materials = (title: string, collection: FlowMaterialCollection, items: FlowMaterial[], isPublic: boolean) => <section aria-label={title} className="min-w-0 rounded border border-line bg-panel p-5">
    <h2 className="text-lg font-semibold">{title}</h2>
    <p className="mt-2 text-xs leading-6 text-mist">{isPublic ? '按当前规则已公开的内容。' : '仅本角色可见；允许公开的材料可手动分享。'}</p>
    {items.length ? <ul className="mt-4 space-y-4">{items.map(item => {
      const action: PackageFlowAction = { action: 'SHARE_MATERIAL', target: { collection, id: item.id } };
      const canShare = !isPublic && view && canPerformFlowAction(view, action);
      const sharedBy = item.shared_by_character_id ? view?.characters.find(character => character.id === item.shared_by_character_id)?.name : undefined;
      return <li key={item.id} className="min-w-0 rounded border border-line p-4">
        <p className="whitespace-pre-wrap break-all text-sm leading-7">{item.text}</p>
        <p className="mt-3 break-all text-xs leading-6 text-mist">{item.kind ? `${kindLabels[item.kind]} · ` : ''}{isPublic ? '已公开' : disclosureLabels[item.disclosure]}{sharedBy ? ` · 由${sharedBy}公开` : ''}</p>
        {!isPublic && item.disclosure === 'MUST_SHARE' && <p className="mt-2 text-xs leading-6 text-brass">请按剧本要求分享。需要你点击公开，页面不会自动替你分享。</p>}
        {canShare && <button type="button" aria-label={`公开${collection === 'knowledge' ? '资料' : '线索'} ${item.id}`} disabled={locked}
          onClick={() => { if (!locked && view && canPerformFlowAction(view, action)) props.onAction(action); }}
          className="mt-3 rounded border border-brass px-3 py-2 text-sm text-brass disabled:opacity-50">{collection === 'knowledge' ? '公开这份资料' : '公开这条线索'}</button>}
      </li>;
    })}</ul> : <p className="mt-4 text-sm text-mist">当前没有这类已解锁材料。</p>}
  </section>;
  return <main className="mx-auto min-w-0 max-w-4xl px-4 pb-24 pt-20 text-paper md:pt-12">
    <p className="text-xs text-brass">固定版本 · 手动检查阶段</p>
    <h1 className="mt-2 font-dossier text-3xl">阶段演练</h1>
    <p className="mt-4 text-sm leading-7 text-mist">你可以按顺序检查阶段材料，手动公开本角色允许分享的内容。AI 角色互动、主持结算和完整整局尚未接入；这不是完整试玩。</p>
    {props.error && <p role="alert" className="mt-5 whitespace-pre-wrap break-all rounded border border-red-400/50 p-4 text-sm text-red-200">{props.error}</p>}
    {props.notice && <p role="status" className="mt-4 break-all rounded border border-emerald-500/40 p-3 text-sm">{props.notice}</p>}
    <div className="mt-5 flex flex-wrap items-center gap-4">
      <button type="button" disabled={props.loading || props.busy || (!props.flowId && !props.openingSessionId)} onClick={() => { if (!props.loading && !props.busy) props.onReload(); }}
        className="rounded border border-line px-3 py-2 text-sm disabled:opacity-50">刷新演练</button>
      <Link href={(view?.opening_session_id || props.openingSessionId) ? `/play/package-preview?session=${encodeURIComponent(view?.opening_session_id || props.openingSessionId)}` : '/play/package-preview'} className="text-sm text-brass underline">返回开场预览</Link>
    </div>
    {props.loading && <p role="status" className="mt-5 text-sm text-mist">正在读取已保存的阶段演练…</p>}
    {!props.loading && !props.flowId && !props.openingSessionId && <p className="mt-6 text-sm text-mist">请先打开属于你的开场，再点击“检查后续阶段”。</p>}
    {!props.loading && props.view === null && !props.flowId && props.openingSessionId && <section aria-label="开始阶段演练" className="mt-6 rounded border border-line bg-panel p-5">
      <h2 className="text-lg font-semibold">这份开场还没有阶段演练</h2>
      <p className="mt-3 text-sm leading-7 text-mist">开始时会重新检查当前发布版本是否有效，并固定沿用开场中的角色与版本。原开场记录保持只读，不会自动升级。</p>
      <button type="button" disabled={!canCreate} onClick={() => { if (canCreate) props.onCreate(); }} className="mt-4 rounded bg-brass px-4 py-3 text-sm font-semibold text-ink disabled:opacity-50">{props.busy ? '正在开始…' : '开始阶段演练'}</button>
    </section>}
    {view && <article aria-label="我的固定版本阶段演练" className="mt-6 min-w-0 space-y-5">
      <section className="min-w-0 rounded border border-line bg-panel p-5">
        <h2 className="break-all text-xl font-semibold">{view.script.title}</h2>
        <p className="mt-2 break-all text-sm text-brass">{view.script.content_version} · {actor?.name || '固定角色'} · {view.script.player_count} 个角色</p>
        <p className="mt-2 text-xs text-mist">已保存操作：{view.revision} 次</p>
        <details className="mt-4"><summary className="cursor-pointer text-sm text-brass">查看故事介绍</summary><p className="mt-3 whitespace-pre-wrap break-all text-sm leading-7">{view.introduction.text}</p></details>
      </section>
      <section aria-label="当前阶段" className="min-w-0 rounded border border-line bg-panel p-5">
        <h2 className="break-all text-lg font-semibold">当前阶段：{view.current_phase.title}</h2>
        {view.phase_complete ? <p className="mt-3 text-sm leading-7 text-mist">阶段材料已走到末段。AI 角色互动、主持结算与完整整局仍未接入，演练不会自动给出结局。</p>
          : <>
            <p className="mt-3 text-sm leading-7 text-mist">阅读当前材料后，可手动进入下一阶段。页面不会自动推进，推进后可查看新解锁的材料。</p>
            {!view.can_advance && <p className="mt-2 text-sm text-mist">当前不能继续推进，请刷新确认最新状态。</p>}
            <button type="button" disabled={locked || !canPerformFlowAction(view, { action: 'ADVANCE_PHASE' })}
              onClick={() => { if (!locked && canPerformFlowAction(view, { action: 'ADVANCE_PHASE' })) props.onAction({ action: 'ADVANCE_PHASE' }); }}
              className="mt-4 rounded bg-brass px-4 py-3 text-sm font-semibold text-ink disabled:opacity-50">进入下一阶段</button>
          </>}
      </section>
      {materials('公开资料', 'knowledge', view.public_knowledge, true)}
      {materials('公开线索', 'evidence', view.public_evidence, true)}
      {materials('我的未公开资料', 'knowledge', view.private_knowledge.filter(item => !view.public_knowledge.some(shared => shared.id === item.id)), false)}
      {materials('我的未公开线索', 'evidence', view.private_evidence.filter(item => !view.public_evidence.some(shared => shared.id === item.id)), false)}
    </article>}
  </main>;
}

type FlowWorkspaceProps = { flowId: string; openingSessionId: string; onCreated: (flowId: string) => Promise<unknown> };

export function PackageFlowWorkspace({ flowId, openingSessionId, onCreated }: FlowWorkspaceProps) {
  const [view, setView] = useState<PackageFlow | null>();
  const [loading, setLoading] = useState(Boolean(flowId || openingSessionId));
  const [busy, setBusy] = useState(false);
  const [requiresRefresh, setRequiresRefresh] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [reload, setReload] = useState(0);
  const managed = useRef<{ active?: AbortController; observed?: PackageFlow; attempts: Partial<Record<'create' | 'action', PackageFlowAttempt>> }>({ attempts: {} });

  useEffect(() => {
    if (!flowId && !openingSessionId) return;
    const requests = managed.current;
    const controller = new AbortController(); requests.active?.abort(); requests.active = controller;
    const read = flowId ? packageFlowService.read(flowId, controller.signal) : packageFlowService.lookup(openingSessionId, controller.signal);
    read.then(result => {
      if (controller.signal.aborted) return;
      if (result !== null && (!matchesFlowRoute(result, flowId, openingSessionId)
        || (requests.observed && (!sameFlowBinding(requests.observed, result) || result.revision < requests.observed.revision)))) {
        throw new PackageFlowError(409, '读取的演练与已保存角色、版本或操作次数不一致，已保留原有材料。请刷新演练核对。');
      }
      if (result === null && (flowId || requests.observed)) throw new PackageFlowError(409, '未找到已保存的演练，已保留原有材料。请刷新演练核对。');
      if (result) requests.observed = result;
      setView(result); setRequiresRefresh(false);
    }).catch(cause => {
      if (!controller.signal.aborted) {
        if (cause instanceof PackageFlowError && [401, 403, 404].includes(cause.status)) requests.observed = undefined;
        setView(requests.observed);
        setRequiresRefresh(true); setError(cause instanceof PackageFlowError ? cause.message : '读取演练结果失败，请刷新后核对。');
      }
    }).finally(() => { if (!controller.signal.aborted) { requests.active = undefined; setLoading(false); } });
    return () => controller.abort();
  }, [flowId, openingSessionId, reload]);

  useEffect(() => {
    const requests = managed.current;
    return () => requests.active?.abort();
  }, []);

  const failure = (cause: unknown) => {
    setError(cause instanceof PackageFlowError ? cause.message : '保存失败，请重试；相同操作会复用本次请求标识。');
    if (cause instanceof PackageFlowError && cause.status === 409) setRequiresRefresh(true);
    if (cause instanceof PackageFlowError && [401, 403, 404].includes(cause.status)) { managed.current.observed = undefined; setRequiresRefresh(true); setView(undefined); }
  };

  const create = async () => {
    const requests = managed.current;
    if (requests.active || loading || requiresRefresh || view !== null || flowId || !openingSessionId) return;
    const payload = { opening_session_id: openingSessionId };
    const attempt = preparePackageFlowAttempt(payload, requests.attempts.create); requests.attempts.create = attempt;
    const controller = new AbortController(); requests.active = controller;
    setBusy(true); setError(''); setNotice('');
    try {
      const result = await packageFlowService.create({ ...payload, idempotency_key: attempt.key }, controller.signal);
      if (controller.signal.aborted) return;
      if (!matchesFlowRoute(result, '', openingSessionId)) throw new PackageFlowError(409, '返回的演练与当前开场不一致，请刷新演练核对。');
      delete requests.attempts.create; requests.observed = result; setView(result); setNotice('阶段演练已开始，原开场记录保持不变。');
      try { await onCreated(result.flow_id); }
      catch { if (!controller.signal.aborted) setError('演练已保存，页面地址更新失败。请刷新演练恢复已有记录。'); }
    } catch (cause) { if (!controller.signal.aborted) failure(cause); }
    finally { if (!controller.signal.aborted) { requests.active = undefined; setBusy(false); } }
  };

  const act = async (action: PackageFlowAction) => {
    const requests = managed.current;
    if (requests.active || loading || requiresRefresh || !view || !matchesFlowRoute(view, flowId, openingSessionId) || !canPerformFlowAction(view, action)) return;
    const payload = { ...action, expected_revision: view.revision };
    const attempt = preparePackageFlowAttempt({ flow_id: view.flow_id, ...payload }, requests.attempts.action); requests.attempts.action = attempt;
    const controller = new AbortController(); requests.active = controller;
    setBusy(true); setError(''); setNotice('');
    try {
      const result = await packageFlowService.act(view.flow_id, { ...payload, idempotency_key: attempt.key }, controller.signal);
      if (controller.signal.aborted) return;
      if (!matchesFlowRoute(result, flowId, openingSessionId) || !sameFlowBinding(view, result)) throw new PackageFlowError(409, '返回的演练版本不一致，请刷新演练核对。');
      delete requests.attempts.action;
      if (result.revision >= (requests.observed?.revision ?? view.revision)) { requests.observed = result; setView(result); }
      setNotice(action.action === 'ADVANCE_PHASE' ? '阶段推进已保存。' : '材料公开已保存。');
      try {
        const refreshed = await packageFlowService.read(view.flow_id, controller.signal);
        if (!controller.signal.aborted) {
          if (!matchesFlowRoute(refreshed, flowId, openingSessionId) || !sameFlowBinding(view, refreshed)
            || refreshed.revision < Math.max(requests.observed?.revision ?? view.revision, result.revision)) throw new Error();
          requests.observed = refreshed; setView(refreshed);
        }
      } catch (cause) {
        if (!controller.signal.aborted) {
          if (cause instanceof PackageFlowError && [401, 403, 404].includes(cause.status)) failure(cause);
          else { setRequiresRefresh(true); setError('操作已保存，但刷新失败。请点击“刷新演练”核对最新结果。'); }
        }
      }
    } catch (cause) { if (!controller.signal.aborted) failure(cause); }
    finally { if (!controller.signal.aborted) { requests.active = undefined; setBusy(false); } }
  };

  return <PackageFlowPanel flowId={flowId} openingSessionId={openingSessionId} view={view} loading={loading} busy={busy} requiresRefresh={requiresRefresh} error={error} notice={notice}
    onReload={() => { if (!managed.current.active) { setLoading(Boolean(flowId || openingSessionId)); setError(''); setNotice(''); setReload(value => value + 1); } }} onCreate={create} onAction={act} />;
}
