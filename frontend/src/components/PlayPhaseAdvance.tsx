import { useRef, useState, type ReactNode } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { History, ArrowRight, X } from 'lucide-react';
import type { PackagePlay } from '@/types/packagePlay';
import { roundMaterial } from '@/lib/playRoundWorkspace';
import PlayRoundSummary from './PlayRoundSummary';
import PlayText from './PlayText';

export default function PlayPhaseAdvance({ view, disabled, onContinue, hiddenMemorySequences = [], renderVisual }: {
  view: PackagePlay; disabled: boolean; onContinue: () => void;
  hiddenMemorySequences?: number[];
  renderVisual: (visual: NonNullable<PackagePlay['visuals']>[number]) => ReactNode;
}) {
  const [dialog, setDialog] = useState<{ revision: number; recap: boolean } | null>(null);
  const submitted = useRef(false);
  // A refreshed projection invalidates the old decision, even within one phase.
  const open = dialog !== null && dialog.revision === view.revision;
  const finish = () => {
    if (!open || disabled || submitted.current) return;
    submitted.current = true; setDialog(null); onContinue();
  };
  const phase = view.round_workspace?.phases.find(p => p.phase_id === view.current_phase.id);
  return <Dialog.Root open={open} onOpenChange={value => { if (!value) setDialog(null); }}>
    <Dialog.Trigger asChild><button type="button" aria-label="进入下一阶段" disabled={disabled} className="min-h-11 rounded-lg bg-brass px-4 py-2 text-sm font-semibold text-ink transition-colors hover:bg-brass/90 disabled:opacity-40"
      onClick={() => { if (!disabled) { submitted.current = false; setDialog({ revision: view.revision, recap: false }); } }}>进入下一阶段</button></Dialog.Trigger>
    <Dialog.Portal><Dialog.Overlay className="fixed inset-0 z-[75] bg-black/60" />
      <Dialog.Content className={`fixed left-3 right-3 top-1/2 z-[75] flex max-h-[calc(100dvh-24px)] -translate-y-1/2 flex-col rounded-xl border border-brass/40 bg-panel p-5 text-paper shadow-xl sm:left-1/2 sm:right-auto sm:w-[min(620px,calc(100vw-24px))] sm:-translate-x-1/2`}>
        <div className="flex shrink-0 items-center justify-between gap-3"><Dialog.Title className="text-lg font-semibold">{dialog?.recap ? `${phase?.title || '本阶段'} · 阶段复盘` : '进入下一阶段前，需要复盘吗？'}</Dialog.Title><Dialog.Close asChild><button type="button" aria-label="关闭阶段复盘" className="min-h-11 shrink-0 rounded px-2 text-brass hover:bg-raised"><X aria-hidden="true" className="h-5 w-5" /></button></Dialog.Close></div>
        <Dialog.Description className="mt-2 shrink-0 text-sm leading-6 text-mist">{dialog?.recap ? '查看已经获得的线索与说法，准备好后继续。关闭窗口会留在当前阶段。' : '可以先整理本阶段的线索与说法，也可以直接继续。已获得的资料会保留在左侧记录中。'}</Dialog.Description>
        {dialog?.recap && <div className="mt-4 min-h-0 overflow-y-auto overscroll-contain">
          {phase ? <PlayRoundSummary key={`${view.play_id}:${phase.phase_id}`} view={view} phaseId={phase.phase_id} hiddenMemorySequences={hiddenMemorySequences} renderSource={item => {
            if (item.material) {
              const ref = phase.materials.find(ref => `${ref.collection}:${ref.id}` === item.material);
              const material = ref && roundMaterial(view, ref);
              return material && <><PlayText text={material.text} />{view.visuals?.filter(visual => visual.collection === ref.collection && visual.material_id === ref.id).map(visual => <div key={visual.id}>{renderVisual(visual)}</div>)}</>;
            }
            const record = [...(view.discussion?.entries || []), ...(view.role_responses?.entries || []), ...(view.investigation_proposals?.entries || []), ...(view.full_game?.private_discussion || [])].find(record => record.id === item.record);
            return record && <PlayText text={record.text} />;
          }} /> : <p className="text-sm leading-7">{view.single_player?.stage?.goal || '本阶段资料已保留，可关闭此窗口回看阅读材料与公共讨论。'}</p>}
        </div>}
        <div className="mt-5 flex shrink-0 flex-wrap gap-3 border-t border-line pt-4">
          {!dialog?.recap && <button type="button" className="inline-flex min-h-11 flex-1 items-center justify-center gap-2 rounded-lg border border-brass/60 px-4 py-2 text-sm text-brass hover:bg-raised" onClick={() => { if (open) setDialog({ revision: view.revision, recap: true }); }}><History aria-hidden="true" className="h-4 w-4" />需要复盘</button>}
          <button type="button" disabled={disabled} onClick={finish} className="inline-flex min-h-11 flex-1 items-center justify-center gap-2 rounded-lg bg-brass px-4 py-2 text-sm font-semibold text-ink disabled:opacity-40">继续推理<ArrowRight aria-hidden="true" className="h-4 w-4" /></button>
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
