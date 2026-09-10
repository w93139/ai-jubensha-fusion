import { playerMaterialText } from '@/lib/playPresentation';
import { useRef, useState, type ReactNode } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { ArrowLeft, BookOpen, History, ListFilter, Pin, X } from 'lucide-react';
import type { PackagePlay, RoundWorkspace } from '@/types/packagePlay';
import { roundMaterial } from '@/lib/playRoundWorkspace';
import PlayText from './PlayText';
import PlayRoundSummary from './PlayRoundSummary';

type Phase = RoundWorkspace['phases'][number];
type Material = Phase['materials'][number];
export default function PlayRoundWorkspace({ view, hiddenMemorySequences = [], renderVisual, onCollect, locked }: {
  view: PackagePlay; hiddenMemorySequences?: number[]; locked: boolean;
  renderVisual: (visual: NonNullable<PackagePlay['visuals']>[number]) => ReactNode;
  onCollect: (collection: 'knowledge' | 'evidence', id: string) => void;
}) {
  const trigger = useRef<HTMLButtonElement | null>(null);
  const [open, setOpen] = useState(false);
  const [rail, setRail] = useState<{ phase: string | null; pinned: boolean }>({ phase: null, pinned: false });
  const railTrigger = useRef<HTMLButtonElement | null>(null);
  const restoringFocus = useRef(false);
  const peekId = `play-round-peek-${view.play_id}`;
  const closeRail = () => {
    setRail({ phase: null, pinned: false });
    restoringFocus.current = true;
    railTrigger.current?.focus?.({ preventScroll: true });
    restoringFocus.current = false;
  };
  const peek = (phase: string, target: HTMLButtonElement) => {
    if (rail.pinned || restoringFocus.current) return;
    railTrigger.current = target; setRail({ phase, pinned: false });
  };
  const [selection, setSelection] = useState<{ phase: string; material?: string; record?: string; recap?: boolean } | null>(null);
  const phases = view.round_workspace?.phases || [];
  const currentIndex = phases.findIndex(entry => entry.phase_id === view.current_phase.id);
  const previousPhase = currentIndex > 0 ? phases[currentIndex - 1] : undefined;
  const phase = phases.find(item => item.phase_id === selection?.phase);
  const name = (id: string) => view.characters.find(actor => actor.id === id)?.name || '角色';
  const materials = (entry: Phase) => entry.materials.filter(ref => ref.collection !== 'memory' || !hiddenMemorySequences.includes(ref.sequence));
  const label = (ref: Material) => {
    const item = roundMaterial(view, ref);
    return item && 'title' in item ? item.title : `${ref.collection === 'evidence' ? '线索' : '资料'} · ${Array.from(playerMaterialText(item?.text || '').replace(/[#*\n]/g, ' ').trim()).slice(0, 28).join('')}`;
  };
  const records = (entry: Phase) => [...(view.discussion?.entries || []), ...(view.role_responses?.entries || []), ...(view.investigation_proposals?.entries || []), ...(view.full_game?.private_discussion || [])]
    .filter(item => !hiddenMemorySequences.includes(item.sequence) && (entry.statement_ids.includes(item.id) || entry.private_message_ids.includes(item.id))).sort((a, b) => a.sequence - b.sequence);
  const select = (value: NonNullable<typeof selection>, target: HTMLButtonElement) => {
    if (!open) trigger.current = target.closest?.('[data-round-peek]') ? railTrigger.current : target;
    setRail({ phase: null, pinned: false }); setSelection(value); setOpen(true);
  };
  const button = 'min-h-11 rounded-lg border border-line px-3 py-2 text-left text-sm text-brass hover:bg-raised';
  const renderIndex = (entries = phases) => <div className="space-y-3">{entries.map(entry => <details key={entry.phase_id} open={entries.length === 1 || entry.phase_id === view.current_phase.id} className="rounded-lg border border-line p-3">
    <summary className="min-h-11 cursor-pointer py-2 text-sm font-medium text-paper">{entry.title}</summary>
    <div className="my-3"><PlayRoundSummary key={`${view.play_id}:${entry.phase_id}`} view={view} phaseId={entry.phase_id} hiddenMemorySequences={hiddenMemorySequences} onSelect={(item, target) => select({ phase: entry.phase_id, material: item.material, record: item.record }, target)} /></div>
    <details><summary className="min-h-11 cursor-pointer py-2 text-xs text-brass">全部资料与记录</summary>
    <p className="mb-2 text-xs text-mist">{materials(entry).length} 份资料 · {records(entry).length} 条记录</p>
    {entry.investigations.map((investigation, i) => <details key={`${investigation.sequence}:${i}`} className="mb-2 rounded border border-line px-2"><summary className="min-h-11 cursor-pointer py-2 text-xs leading-5 text-paper">{investigation.mode === 'LEGACY' ? '旧调查记录' : `${investigation.character_id === view.selected_character_id ? '我' : name(investigation.character_id)}选择`} · {investigation.label}{investigation.mode === 'AUTO' ? '（自动安排）' : ''}</summary>
      <p className="text-xs text-mist">消耗 {investigation.cost} 点</p>{investigation.materials.map(ref => { const material = materials(entry).find(item => item.collection === ref.collection && item.id === ref.id); return material ? <button key={`${ref.collection}:${ref.id}`} type="button" className="block min-h-11 w-full break-words py-2 text-left text-xs text-brass" onClick={event => select({ phase: entry.phase_id, material: `${ref.collection}:${ref.id}` }, event.currentTarget)}>{label(material)}</button> : null; })}
      {investigation.materials.length === 0 && <p className="py-2 text-xs text-mist">此记录未关联新增可读线索。</p>}
    </details>)}
    <div className="space-y-1">{materials(entry).filter(ref => !entry.investigations.some(investigation => investigation.materials.some(item => item.collection === ref.collection && item.id === ref.id))).map(ref => <button key={`${ref.collection}:${ref.id}`} type="button" className="block min-h-11 w-full break-words rounded px-2 py-2 text-left text-sm text-brass hover:bg-raised" onClick={event => select({ phase: entry.phase_id, material: `${ref.collection}:${ref.id}` }, event.currentTarget)}>{label(ref)}</button>)}</div>
    {records(entry).length > 0 && <details className="mt-2"><summary className="min-h-11 cursor-pointer py-2 text-sm text-brass">已保存发言</summary>{records(entry).map(record => <button key={record.id} type="button" className="block min-h-11 w-full break-words rounded px-2 py-2 text-left text-xs text-mist hover:bg-raised" onClick={event => select({ phase: entry.phase_id, record: record.id }, event.currentTarget)}>{name(record.speaker)}{'audience' in record ? ' · 私聊' : ' · 公开'}：{Array.from(record.text).slice(0, 24).join('')}</button>)}</details>}
    {materials(entry).length + records(entry).length === 0 && <p className="py-2 text-xs text-mist">本步骤还没有新增资料或记录。</p>}
    </details>
    <button type="button" className={`${button} mt-3 inline-flex items-center gap-2`} onClick={event => select({ phase: entry.phase_id, recap: true }, event.currentTarget)}><History aria-hidden="true" className="h-4 w-4" />阶段复盘（可选）</button>
  </details>)}</div>;
  const renderMaterial = (ref: Material) => {
    const item = roundMaterial(view, ref);
    if (!item) return null;
    return <section key={`${ref.collection}:${ref.id}`} className="rounded-lg border border-line p-4"><h3 className="mb-3 text-sm font-semibold text-brass">{label(ref)}</h3><PlayText text={item.text} />
      {view.visuals?.filter(visual => visual.collection === ref.collection && visual.material_id === ref.id).map(visual => <div key={visual.id}>{renderVisual(visual)}</div>)}
      {ref.collection !== 'memory' && <button type="button" className={`${button} mt-3`} disabled={locked} onClick={() => { if (!locked) { setOpen(false); setSelection(null); trigger.current = null; onCollect(ref.collection as 'knowledge' | 'evidence', ref.id); } }}>收藏线索</button>}
    </section>;
  };
  return <>
    <aside aria-label="每轮线索与记录" data-round-rail data-pinned={Boolean(rail.pinned && rail.phase) || undefined} className="play-round-rail fixed bottom-0 left-0 top-0 z-40 hidden border-r border-line bg-panel"
      onPointerLeave={event => { if (event.pointerType === 'mouse' && !rail.pinned) setRail({ phase: null, pinned: false }); }}
      onBlur={event => { if (!rail.pinned && !event.currentTarget.contains(event.relatedTarget)) setRail({ phase: null, pinned: false }); }}
      onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeRail(); } }}>
      <div className="flex h-full flex-col items-center gap-2 overflow-y-auto px-1 py-3">
        <BookOpen aria-hidden="true" className="mb-1 h-5 w-5 shrink-0 text-brass" /><span className="sr-only">已到达的步骤</span>
        {phases.map((entry, i) => <button key={entry.phase_id} type="button" className="play-round-tab flex min-h-14 w-11 shrink-0 flex-col items-center justify-center rounded-lg border border-transparent text-mist hover:bg-raised hover:text-paper focus-visible:outline-2 focus-visible:outline-brass"
          title={entry.title} aria-label={`打开${entry.title}记录`} aria-controls={peekId} aria-expanded={rail.phase === entry.phase_id} aria-pressed={rail.pinned && rail.phase === entry.phase_id}
          onPointerEnter={event => { if (event.pointerType === 'mouse') peek(entry.phase_id, event.currentTarget); }}
          onFocus={event => peek(entry.phase_id, event.currentTarget)}
          onClick={event => { railTrigger.current = event.currentTarget; if (rail.pinned && rail.phase === entry.phase_id) closeRail(); else setRail({ phase: entry.phase_id, pinned: true }); }}>
          <span className="text-base font-semibold">{i + 1}</span><span className="text-[11px]">{entry.kind === 'READING' ? '阅读' : entry.kind === 'INVESTIGATION' ? '调查' : '结局'}</span>
        </button>)}
      </div>
      {rail.phase && <section id={peekId} data-round-peek aria-label="展开的轮次记录" className="play-round-peek absolute bottom-0 top-0 flex min-w-0 flex-col border-r border-line bg-panel p-3 text-paper shadow-xl">
        <div className="mb-3 flex shrink-0 items-center justify-between gap-2"><h2 className="text-sm font-semibold">{phases.find(entry => entry.phase_id === rail.phase)?.title}</h2>
          <div className="flex items-center gap-1"><button type="button" aria-pressed={rail.pinned} aria-label={rail.pinned ? '取消固定轮次记录' : '固定轮次记录'} className="min-h-11 rounded px-2 text-brass hover:bg-raised" onClick={() => setRail(current => ({ ...current, pinned: !current.pinned }))}><Pin aria-hidden="true" className="h-4 w-4" /></button><button type="button" aria-label="收起轮次记录" className="min-h-11 rounded px-2 text-brass hover:bg-raised" onClick={closeRail}><X aria-hidden="true" className="h-4 w-4" /></button></div>
        </div>
        <p className="mb-3 text-xs text-mist">{rail.pinned ? '已固定，点击关闭或按 Esc 收起。' : '悬停查看，点击轮次或图钉可固定。'}</p>
        <div className="min-h-0 overflow-y-auto overscroll-contain">{renderIndex(phases.filter(entry => entry.phase_id === rail.phase))}</div>
      </section>}
    </aside>
    <Dialog.Root open={open} onOpenChange={value => { setOpen(value); if (!value) setSelection(null); }}>
      {previousPhase && <button type="button" className="play-nav-action play-previous-round gap-1" aria-label={`回看上一轮：${previousPhase.title}`} title={`回看${previousPhase.title}，保留当前进度`} onClick={event => select({ phase: previousPhase.phase_id, recap: true }, event.currentTarget)}><ArrowLeft aria-hidden="true" className="h-4 w-4" /><span>上一轮</span></button>}
      <Dialog.Trigger asChild><button type="button" data-round-mobile-trigger className="play-nav-action gap-2" onClick={event => { trigger.current = event.currentTarget; setRail({ phase: null, pinned: false }); setSelection(null); }}><ListFilter aria-hidden="true" className="h-4 w-4" />每轮记录</button></Dialog.Trigger>
      <Dialog.Portal><Dialog.Overlay className="fixed inset-0 z-[70] bg-black/60" />
        <Dialog.Content onCloseAutoFocus={event => { event.preventDefault(); if (trigger.current?.isConnected) trigger.current.focus({ preventScroll: true }); }} className="fixed bottom-3 left-3 right-3 top-3 z-[70] flex min-w-0 flex-col rounded-xl border border-brass/40 bg-panel p-4 text-paper shadow-xl sm:left-auto sm:w-[min(640px,calc(100vw-24px))]">
          <div className="flex items-center justify-between gap-3"><Dialog.Title className="text-lg font-semibold">{phase ? `${phase.title} · ${selection?.recap ? '阶段复盘' : '已存资料'}` : '每轮线索与记录'}</Dialog.Title><Dialog.Close asChild><button type="button" className={button} aria-label="关闭每轮记录"><X aria-hidden="true" className="h-5 w-5" /></button></Dialog.Close></div>
          {phase && phase.phase_id !== view.current_phase.id && <Dialog.Close asChild><button type="button" className={`${button} mt-3 self-start`}>回到当前进度</button></Dialog.Close>}
          <Dialog.Description className="mt-2 text-sm leading-6 text-mist">{selection?.recap ? '仅回看本阶段已经获得的资料、已保存的说法和调查记录。角色说法需要你自行核对；复盘不生成答案，也不推进游戏。手记可从顶部独立查阅。' : '按首次获得的步骤整理，关闭后回到原阅读位置。'}</Dialog.Description>
          <div className="mt-4 min-h-0 space-y-4 overflow-y-auto overscroll-contain">
            {!phase ? renderIndex() : <>
              <button type="button" className={button} onClick={() => setSelection(null)}>返回每轮目录</button>
              {selection?.recap && <PlayRoundSummary key={`${view.play_id}:${phase.phase_id}:recap`} view={view} phaseId={phase.phase_id} hiddenMemorySequences={hiddenMemorySequences} onSelect={(item, target) => select({ phase: phase.phase_id, material: item.material, record: item.record }, target)} />}
              {materials(phase).filter(ref => selection?.recap || `${ref.collection}:${ref.id}` === selection?.material).map(renderMaterial)}
              {records(phase).filter(record => selection?.recap || record.id === selection?.record).map(record => <section key={record.id} className="rounded-lg border border-line p-4"><h3 className="mb-3 text-sm text-brass">{name(record.speaker)} · {'audience' in record ? '双方私聊说法' : '公开说法'}</h3><PlayText text={record.text} /></section>)}
              {selection?.recap && <section className="space-y-2 rounded-lg border border-line p-4"><h3 className="font-semibold">调查记录</h3>{phase.investigations.length ? phase.investigations.map((entry, i) => <p key={`${entry.sequence}:${entry.action_id}:${i}`} className="text-sm leading-6">{entry.label} · {entry.cost} 点 · {entry.mode === 'LEGACY' ? '旧调查记录' : `${entry.character_id === view.selected_character_id ? '我' : name(entry.character_id)}选择${entry.mode === 'AUTO' ? '（自动安排）' : ''}`}</p>) : <p className="text-sm text-mist">本步骤没有调查记录。</p>}</section>}
            </>}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  </>;
}
