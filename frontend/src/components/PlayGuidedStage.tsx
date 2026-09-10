import { useState } from 'react';
import type { GuidedAction, PackagePlay } from '@/types/packagePlay';
import { canGuide } from '@/lib/playGuidance';

export default function PlayGuidedStage({ view, locked, onGuided }: { view: PackagePlay; locked: boolean; onGuided?: (action: GuidedAction) => void }) {
  const scope = `${view.play_id}:${view.selected_character_id}:${view.current_phase.id}:${view.revision}`;
  const [selection, setSelection] = useState<{ scope: string; ids: string[] }>({ scope, ids: [] });
  const selected = selection.scope === scope ? selection.ids : [];
  const actions = view.mechanics?.available_actions || [];
  const cost = selected.reduce((sum, id) => sum + (actions.find(item => item.id === id)?.cost || 0), 0);
  const remaining = view.mechanics?.remaining_points ?? 0;
  const batch = view.guided_play?.can_investigate_round !== undefined;
  const send = (action: GuidedAction) => { if (!locked && onGuided && canGuide(view, action)) onGuided(action); };
  const request: GuidedAction = { action: 'INVESTIGATE_ROUND', payload: { action_ids: selected } };
  const button = 'min-h-11 rounded-lg border border-brass/40 px-4 py-2 text-sm text-brass hover:bg-raised disabled:opacity-40';
  return <section aria-label="选择调查地点" className="min-w-0 space-y-4 rounded-xl border border-line bg-panel p-5">
    <h2 className="text-lg font-semibold">{batch ? '选择本轮调查地点' : '选择下一处调查地点'}</h2>
    <p className="text-sm leading-7 text-mist">{batch ? '可选择多个地点，按选择顺序调查。确认后，其他角色按本轮分工自动选择剩余合法地点，使用剩余调查点。调查结果依规则公开，完成后仍可阅读线索。' : '由你决定先去哪里。调查后会展示取得的线索，以及其他角色需要讲述的信息。'}</p>
    <p className="text-brass">调查点：{remaining} / {view.mechanics?.initial_points ?? 0}{batch && <span className="ml-3">已选 {selected.length} 处 · 消耗 {cost} 点</span>}</p>
    {view.guided_play?.has_legacy_ballot && <p className="text-sm leading-7 text-mist">原调查投票尚未结束，可按新流程直接选址继续。</p>}
    {view.guided_play?.can_present_required && <button type="button" className={button} disabled={locked || !onGuided || !canGuide(view, { action: 'PRESENT_REQUIRED' })} onClick={() => send({ action: 'PRESENT_REQUIRED' })}>听取角色说明</button>}
    <div className="grid gap-3 sm:grid-cols-2">{actions.map(item => <div key={item.id} className={`rounded-lg border p-4 ${selected.includes(item.id) ? 'border-brass bg-brass/5' : 'border-line'}`}>
      <h3 className="break-words font-medium">{item.label}</h3><p className="mt-2 text-sm text-mist">消耗 {item.cost} 点</p>
      {batch ? <label className="mt-2 flex min-h-11 cursor-pointer items-center gap-3 text-sm text-brass"><input type="checkbox" className="h-5 w-5 accent-brass" aria-label={`选择${item.label}`} checked={selected.includes(item.id)}
        disabled={locked || !onGuided || !view.guided_play?.can_investigate_round || (!selected.includes(item.id) && cost + item.cost > remaining)}
        onChange={() => { if (locked || !onGuided || !view.guided_play?.can_investigate_round) return; const ids = selected.includes(item.id) ? selected.filter(id => id !== item.id) : [...selected, item.id]; if (canGuide(view, { action: 'INVESTIGATE_ROUND', payload: { action_ids: ids } })) setSelection({ scope, ids }); }} />{selected.includes(item.id) ? `第 ${selected.indexOf(item.id) + 1} 处` : '选择此处'}</label>
        : <button type="button" className={`mt-3 ${button}`} disabled={locked || !onGuided || !canGuide(view, { action: 'INVESTIGATE', payload: { action_id: item.id } })} onClick={() => send({ action: 'INVESTIGATE', payload: { action_id: item.id } })}>调查这里</button>}
    </div>)}</div>
    {batch && <div className="space-y-2"><p className="text-sm leading-6 text-mist">{selected.length ? `你选择的地点先调查，剩余 ${remaining - cost} 点由其他角色按规则选点使用。` : '未选择地点时，将本轮剩余选点交给其他角色按规则完成。'} 若剩余点数没有可用地点，会保留余点。</p><button type="button" className={button} disabled={locked || !onGuided || !canGuide(view, request)} onClick={() => send(request)}>{selected.length ? '确认选点，其他角色接续选址' : '交给其他角色选点'}</button></div>}
    {!actions.length && <p className="text-sm text-mist">当前没有可继续调查的地点，可以查看已获线索或完成本轮。</p>}
    <p className="text-sm text-mist">阅读和交流完成后，使用顶栏右上角按钮进入下一阶段。</p>
  </section>;
}
