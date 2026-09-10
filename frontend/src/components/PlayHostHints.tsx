import { useState } from 'react';
import { CircleHelp } from 'lucide-react';
import * as Dialog from '@radix-ui/react-dialog';
import * as AlertDialog from '@radix-ui/react-alert-dialog';
import type { GuidedAction, PackagePlay } from '@/types/packagePlay';
import { playerHostText } from '@/lib/playPresentation';
import { canGuide, validPostGameQa } from '@/lib/playGuidance';
import PlayText from './PlayText';

export default function PlayHostHints({ view, locked, error, onGuided }: {
  view: PackagePlay; locked: boolean; error: string; onGuided?: (action: GuidedAction) => void;
}) {
  const [open, setOpen] = useState(false);
  const [qaChoice, setQaChoice] = useState<{ scope: string; id: string } | null>(null);
  const qaScope = `${view.play_id}:${view.selected_character_id}`;
  const qaQuestions = view.settled && validPostGameQa(view) ? view.post_game_qa?.questions || [] : [];
  const qaAnswer = qaChoice?.scope === qaScope ? qaQuestions.find(q => q.id === qaChoice.id) : undefined;
  const [topicId, setTopicId] = useState('');
  const [confirm, setConfirm] = useState<string | null>(null);
  const topics = view.host_hints?.topics.filter(topic => topic.phase_id === view.current_phase.id) || [];
  const topic = topics.find(value => value.id === topicId) || topics[0];
  const entries = view.host_hints?.entries.filter(entry => entry.topic_id === topic?.id) || [];
  const history = view.host_hints?.entries.filter(entry => entry.phase_id !== view.current_phase.id || !topics.some(topic => topic.id === entry.topic_id)) || [];
  const button = 'min-h-11 rounded-lg border border-brass/40 px-3 py-2 text-sm text-brass hover:bg-raised disabled:opacity-40';
  const request = (next: 1 | 2 | 3) => {
    if (!topic || locked || !onGuided) return;
    const action: GuidedAction = { action: 'REQUEST_HINT', payload: { topic_id: topic.id, level: next } };
    if (canGuide(view, action)) onGuided(action);
  };
  return <Dialog.Root open={open} onOpenChange={value => { setOpen(value); if (!value) setConfirm(null); }}>
    <Dialog.Trigger asChild><button type="button" className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-brass/60 bg-brass/10 px-3 py-2 text-sm font-semibold text-brass hover:bg-brass/20"><CircleHelp aria-hidden="true" className="h-5 w-5 shrink-0" />{view.settled ? '结局答疑' : '询问主持人'}</button></Dialog.Trigger>
    <Dialog.Portal><Dialog.Overlay className="fixed inset-0 z-[70] bg-black/50" />
      <Dialog.Content className="fixed bottom-4 left-3 right-3 z-[70] flex max-h-[85dvh] min-w-0 flex-col rounded-xl border border-line bg-panel p-4 text-paper shadow-xl sm:left-auto sm:right-6 sm:w-[480px]">
        <div className="flex items-center justify-between gap-3"><Dialog.Title className="text-lg font-semibold">{view.settled ? '结局答疑' : '主持提示'}</Dialog.Title><Dialog.Close asChild><button type="button" className={button}>关闭</button></Dialog.Close></div>
        <Dialog.Description className="mt-2 text-sm leading-6 text-mist">{view.settled ? '还有哪里没想明白？' : '你想了解哪件事？'}</Dialog.Description>
        <div className="mt-4 min-h-0 overflow-y-auto overscroll-contain">
          {view.settled ? <div className="space-y-4">
            <div aria-label="结局问题" className="flex flex-wrap gap-2">{qaQuestions.map(question => <button key={question.id} type="button" className={button} aria-pressed={qaAnswer?.id === question.id} onClick={() => setQaChoice({ scope: qaScope, id: question.id })}>{question.title}</button>)}</div>
            {qaAnswer ? <section aria-label="主持人解答" className="rounded-lg border border-brass/40 bg-ink p-4"><h3 className="mb-3 font-medium text-brass">{qaAnswer.title}</h3><PlayText text={playerHostText(qaAnswer.text)} /></section> : <p className="text-sm text-mist">{qaQuestions.length ? '选一个问题，听主持人解答。' : '可以先回看结尾与真相复盘。'}</p>}
          </div> : <>
          {!topics.length && <p className="text-sm text-mist">当前步骤暂没有可请求的主持提示。</p>}
          <div className="flex flex-wrap gap-2">{topics.map(value => <button key={value.id} type="button" className={button} aria-pressed={topic?.id === value.id} disabled={locked} onClick={() => { setTopicId(value.id); setConfirm(null); }}>{value.title}</button>)}</div>
          {topic && <><h3 className="mt-4 font-medium">{topic.title}</h3>
            {entries.length === 0 && <p className="mt-3 text-sm text-mist">尚未查看这个问题的提示。</p>}
            {entries.map(entry => <section key={`${entry.topic_id}:${entry.level}`} className="mt-3 rounded-lg border border-line p-3"><h4 className="mb-2 text-sm text-brass">{entry.level === 1 ? '一点方向' : entry.level === 2 ? '关键提示' : '问题答案'}</h4><PlayText text={playerHostText(entry.text)} /></section>)}
            <div className="mt-4 flex flex-wrap gap-2">{([1, 2, 3] as const).map(next => <button key={next} type="button" className={button}
              disabled={locked || !onGuided || !canGuide(view, { action: 'REQUEST_HINT', payload: { topic_id: topic.id, level: next } })}
              onClick={() => { if (locked || !onGuided || !canGuide(view, { action: 'REQUEST_HINT', payload: { topic_id: topic.id, level: next } })) return; if (next === 3) setConfirm(topic.id); else request(next); }}>{entries.some(entry => entry.level === next) ? next === 3 ? '已查看答案' : next === 1 ? '已查看方向' : '已查看关键提示' : next === 1 ? '给我一点方向' : next === 2 ? '再具体一点' : '揭晓当前问题'}</button>)}</div>
          </>}
          {history.length > 0 && <details className="mt-5 rounded-lg border border-line p-3"><summary className="min-h-11 cursor-pointer py-2 text-sm text-brass">回看已保存的提示（{history.length} 条）</summary><div className="mt-3 space-y-3">{history.map(entry => <section key={`${entry.topic_id}:${entry.level}`} className="rounded-lg border border-line p-3"><h3 className="mb-2 text-sm font-medium">{entry.title} · {entry.level === 1 ? '一点方向' : entry.level === 2 ? '关键提示' : '问题答案'}</h3><PlayText text={playerHostText(entry.text)} /></section>)}</div></details>}
          {locked && <p role="status" className="mt-3 text-sm text-mist">正在处理，请稍候…</p>}
          {error && <p role="alert" className="mt-3 text-sm text-red-300">{error}</p>}
          </>}
        </div>
        <AlertDialog.Root open={confirm !== null} onOpenChange={value => { if (!value) setConfirm(null); }}><AlertDialog.Portal>
          <AlertDialog.Overlay className="fixed inset-0 z-[80] bg-black/60" />
          <AlertDialog.Content className="fixed left-3 right-3 top-1/2 z-[80] -translate-y-1/2 rounded-xl border border-brass/40 bg-panel p-5 text-paper shadow-xl sm:left-1/2 sm:w-[420px] sm:-translate-x-1/2">
            <AlertDialog.Title className="font-semibold">查看这个问题的答案？</AlertDialog.Title><AlertDialog.Description className="mt-3 text-sm leading-7 text-mist">将揭晓「{topic?.title}」的答案。</AlertDialog.Description>
            <div className="mt-4 flex gap-3"><AlertDialog.Cancel asChild><button type="button" className={button}>继续自己推理</button></AlertDialog.Cancel><AlertDialog.Action asChild><button type="button" className={button} disabled={locked || confirm !== topic?.id} onClick={() => { if (confirm === topic?.id) request(3); setConfirm(null); }}>查看答案</button></AlertDialog.Action></div>
          </AlertDialog.Content>
        </AlertDialog.Portal></AlertDialog.Root>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
