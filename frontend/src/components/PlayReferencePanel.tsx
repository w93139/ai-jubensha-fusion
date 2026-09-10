import { useId, useRef, useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import * as Tabs from '@radix-ui/react-tabs';

import { extractMemoryTriggerSections, extractRuleSections, extractTaskSections } from '../lib/playReference';
import type { PlayReferenceMaterial } from '../lib/playReference';
import PlayText from './PlayText';

export interface PlayReferencePanelProps {
  /** Bind the authenticated owner, opening/play and character; no play ID is required. */
  scope: string;
  characterName: string;
  /** Only the current actor's authorized private materials. */
  materials: readonly PlayReferenceMaterial[];
  /** Original public rule materials, excluding other actors' shared material. */
  rulesMaterials?: readonly PlayReferenceMaterial[];
  /** Current authorized memories, already filtered for speech reveal timing. */
  memories: readonly { id: string; title: string; text: string }[];
  locked?: boolean;
  /** Compact triggers placed directly in the game header grid. */
  toolbar?: boolean;
}

const buttonClass = 'rounded-md border border-line bg-panel px-3 py-2 text-sm text-paper hover:bg-raised focus-visible:outline-2 focus-visible:outline-brass disabled:opacity-40';

export default function PlayReferencePanel(props: PlayReferencePanelProps) {
  if (props.locked || !props.scope.trim()) return <div className={props.toolbar ? 'contents' : 'flex items-center gap-2'}>
    <button type="button" className={buttonClass} disabled>{props.toolbar ? '我的任务' : '任务'}</button>
    <button type="button" className={buttonClass} disabled>{props.toolbar ? '我的回忆' : '回忆（0）'}</button>
  </div>;
  // Keying discards the previous scope's open state, focus refs and content
  // before render. Source text is never copied into component state/storage.
  return <ScopedReferencePanel key={props.scope} {...props} />;
}

function ScopedReferencePanel({ characterName, materials, rulesMaterials = [], memories, toolbar }: PlayReferencePanelProps) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState('tasks');
  const trigger = useRef<HTMLButtonElement | null>(null);
  const closeButton = useRef<HTMLButtonElement | null>(null);
  const contentId = useId();
  const tasks = extractTaskSections(materials);
  const triggerHints = extractMemoryTriggerSections(materials);
  const rules = extractRuleSections(rulesMaterials);

  return <Dialog.Root open={open} onOpenChange={setOpen}>
    <div className={toolbar ? 'contents' : 'flex items-center gap-2'}>
      {([['tasks', toolbar ? '我的任务' : '任务'], ['memories', toolbar ? '我的回忆' : `回忆（${memories.length}）`]] as const).map(([value, label]) => <button
        key={value} type="button" className={buttonClass} aria-haspopup="dialog" aria-controls={contentId}
        aria-expanded={open} onClick={event => { trigger.current = event.currentTarget; setTab(value); setOpen(true); }}>
        {label}
      </button>)}
    </div>
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
      <Dialog.Content id={contentId}
        className="fixed bottom-3 left-3 right-3 z-50 flex max-h-[85dvh] min-w-0 flex-col overflow-hidden rounded-xl border border-line bg-panel p-4 text-paper shadow-xl sm:bottom-6 sm:left-auto sm:right-6 sm:w-[460px]"
        onOpenAutoFocus={event => { event.preventDefault(); closeButton.current?.focus({ preventScroll: true }); }}
        onCloseAutoFocus={event => { event.preventDefault(); if (trigger.current?.isConnected) trigger.current.focus({ preventScroll: true }); }}>
        <div className="flex items-center justify-between gap-3">
          <Dialog.Title className="text-lg font-semibold">{characterName}的随身资料</Dialog.Title>
          <Dialog.Close asChild><button ref={closeButton} type="button" className={buttonClass} aria-label="关闭随身资料">关闭</button></Dialog.Close>
        </div>
        <Dialog.Description className="mt-2 text-xs leading-5 text-mist">查阅任务、回忆、角色资料和规则，关闭后继续阅读原位置。</Dialog.Description>
        <Tabs.Root value={tab} onValueChange={setTab} className="mt-3 flex min-h-0 flex-1 flex-col overflow-hidden">
          <Tabs.List aria-label="随身资料内容" className="grid shrink-0 grid-cols-4 gap-1 border-b border-line pb-2">
            {([['tasks', '任务'], ['memories', '回忆'], ['materials', '角色资料'], ['rules', '规则']] as const).map(([value, label]) => <Tabs.Trigger
              key={value} value={value} className="min-w-0 whitespace-nowrap rounded-md px-1 py-2 text-sm text-mist data-[state=active]:bg-raised data-[state=active]:text-paper focus-visible:outline-2 focus-visible:outline-brass">{label}</Tabs.Trigger>)}
          </Tabs.List>
          <Tabs.Content value="tasks" className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain pt-3">
            {tasks.length ? tasks.map(section => <section key={section.id} className="min-w-0 rounded-md border border-line p-3"><PlayText text={section.text} /></section>)
              : <p className="text-sm leading-6 text-mist">暂时没有可单独查看的任务，请查看角色资料。</p>}
          </Tabs.Content>
          <Tabs.Content value="memories" className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain pt-3">
            <h3 className="font-semibold">已解锁回忆</h3>
            {memories.length ? memories.map(memory => <section key={memory.id} className="min-w-0 rounded-md border border-line p-3">
              <h4 className="mb-2 font-semibold">{memory.title}</h4><PlayText text={memory.text} />
            </section>) : <div className="space-y-2">
              <p className="text-sm leading-6 text-mist">还没有已解锁的回忆。开场已知经历可在角色资料中查看。</p>
              <button type="button" className={buttonClass} onClick={() => setTab('materials')}>查看角色资料</button>
            </div>}
            {triggerHints.length > 0 && <section className="space-y-3 border-t border-line pt-4">
              <h3 className="font-semibold">回忆触发提示</h3>
              <p className="text-sm leading-6 text-mist">触发提示，不是已解锁回忆。阅读提示不会解锁回忆。</p>
              {triggerHints.map(section => <div key={section.id} className="min-w-0 rounded-md border border-line p-3"><PlayText text={section.text} /></div>)}
            </section>}
          </Tabs.Content>
          <Tabs.Content value="materials" className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain pt-3">
            <p className="text-sm leading-6 text-mist">这里是你当前已获得的角色资料，包括开场经历和后续资料。</p>
            {materials.length ? materials.map((material, index) => <section key={`${material.id}:${index}`} className="min-w-0 rounded-md border border-line p-3">
              <h3 className="mb-2 font-semibold">角色资料 {index + 1}</h3><PlayText text={material.text} />
            </section>) : <p className="text-sm leading-6 text-mist">当前没有可查看的角色资料。</p>}
          </Tabs.Content>
          <Tabs.Content value="rules" className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain pt-3">
            {rules.length ? rules.map(section => <section key={section.id} className="min-w-0"><PlayText text={section.text} /></section>)
              : <p className="text-sm leading-6 text-mist">当前没有可查看的玩家规则。</p>}
          </Tabs.Content>
        </Tabs.Root>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
