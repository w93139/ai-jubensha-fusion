'use client';

import { useEffect, useRef, useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import * as Tabs from '@radix-ui/react-tabs';
import PlayText from './PlayText';
import {
  addNotebookClip, emptyNotebook, loadNotebook, notebookExportText, notebookKey,
  NOTEBOOK_NOTE_LIMIT, saveNotebook,
  type NotebookClip, type NotebookDocument,
} from '@/lib/playNotebook';

export interface PlayNotebookProps {
  ownerId: string | null;
  playId: string;
  characterId: string;
  locked: boolean;
  lifted?: boolean;
  inlineTrigger?: boolean;
  memories: NotebookClip[];
  clip?: NotebookClip;
  onClipHandled?: () => void;
}

const entryClass = 'rounded-full border border-brass/60 bg-panel px-4 py-3 text-sm font-medium text-paper shadow-lg hover:bg-raised focus-visible:outline-2 focus-visible:outline-brass disabled:cursor-not-allowed disabled:opacity-50';
const entryPosition = (lifted?: boolean, inlineTrigger?: boolean) => inlineTrigger ? '' : `fixed right-4 z-40 ${lifted ? 'bottom-[calc(42vh+6rem)]' : 'bottom-28'}`;
const buttonClass = 'rounded-md border border-line px-3 py-2 text-sm text-paper hover:bg-raised focus-visible:outline-2 focus-visible:outline-brass disabled:opacity-50';

export default function PlayNotebook(props: PlayNotebookProps) {
  const key = props.locked ? null : notebookKey(props.ownerId, props.playId, props.characterId);
  const [binding, setBinding] = useState<{ key: string | null; blockedClip: NotebookClip | undefined }>(() => ({
    key, blockedClip: key ? undefined : props.clip,
  }));
  // Clear the child synchronously when identity changes. An already-present clip
  // belongs to the old projection; the caller must supply a new clip afterward.
  if (binding.key !== key) {
    setBinding({ key, blockedClip: props.clip });
    return null;
  }
  if (!key) return <button type="button" className={`${entryClass} ${entryPosition(props.lifted, props.inlineTrigger)}`} disabled title="登录并确认当前角色后可用">随身手记</button>;
  return <NotebookSession key={key} {...props} storageKey={key} clip={props.clip === binding.blockedClip ? undefined : props.clip} />;
}

type Snapshot = {
  document: NotebookDocument;
  status: 'loading' | 'ready' | 'saved' | 'unsaved' | 'unavailable';
  error: string;
};

function NotebookSession({ storageKey, playId, characterId, lifted, inlineTrigger, memories, clip, onClipHandled }: PlayNotebookProps & { storageKey: string }) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState('notes');
  const [snapshot, setSnapshot] = useState<Snapshot>(() => ({ document: emptyNotebook(), status: 'loading', error: '' }));
  const [notice, setNotice] = useState('');
  const draft = useRef(emptyNotebook());
  const processedClip = useRef<NotebookClip | undefined>(undefined);
  const mounted = useRef(false);

  function read() {
    const result = loadNotebook(storageKey);
    draft.current = result.ok ? result.document : emptyNotebook();
    setSnapshot({ document: draft.current, status: result.ok ? 'ready' : 'unavailable', error: result.ok ? '' : result.error });
  }

  useEffect(() => {
    mounted.current = true;
    const result = loadNotebook(storageKey);
    draft.current = result.ok ? result.document : emptyNotebook();
    setSnapshot({ document: draft.current, status: result.ok ? 'ready' : 'unavailable', error: result.ok ? '' : result.error });
    return () => { mounted.current = false; };
  }, [storageKey]);

  function persist(document: NotebookDocument) {
    // Stale event callbacks from a removed identity never write to storage.
    if (!mounted.current) return;
    draft.current = document;
    const result = saveNotebook(storageKey, document);
    setSnapshot({ document, status: result.ok ? 'saved' : 'unsaved', error: result.ok ? '' : result.error });
  }

  useEffect(() => {
    if (!clip || snapshot.status === 'loading' || processedClip.current === clip) return;
    processedClip.current = clip;
    setOpen(true);
    setTab('clips');
    if (snapshot.status === 'unavailable') {
      // This effect acknowledges an external clip event and its storage result.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setNotice('尚未读取本机笔记，这条内容未收藏。读取成功后请再次点击收藏。');
    } else {
      const result = addNotebookClip(draft.current, clip);
      if (!result.ok) setNotice(result.error);
      else if (result.duplicate) setNotice('这条内容已在收藏中。');
      else {
        draft.current = result.document;
        const saved = saveNotebook(storageKey, result.document);
        setSnapshot({ document: result.document, status: saved.ok ? 'saved' : 'unsaved', error: saved.ok ? '' : saved.error });
        setNotice(saved.ok ? '已加入本机收藏。' : '已加入当前草稿，尚未保存到本机。');
      }
    }
    onClipHandled?.();
  }, [clip, onClipHandled, snapshot.status, storageKey]);

  function exportText() {
    if (!mounted.current) return;
    let url: string | undefined;
    try {
      const text = notebookExportText(draft.current, playId, characterId);
      url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `剧本杀笔记-${new Date().toISOString().slice(0, 10)}.txt`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      const downloadUrl = url;
      // Allow the browser to consume the download before releasing its URL.
      setTimeout(() => URL.revokeObjectURL(downloadUrl), 1_000);
      setNotice('已发起文本导出，请查看浏览器下载记录。');
    } catch {
      if (url) URL.revokeObjectURL(url);
      setNotice('导出未成功，请复制笔记正文，或允许浏览器下载后重试。');
    }
  }

  const editable = snapshot.status !== 'loading' && snapshot.status !== 'unavailable';
  return <Dialog.Root open={open} onOpenChange={setOpen}>
    <Dialog.Trigger asChild><button type="button" className={inlineTrigger ? 'play-nav-action' : `${entryClass} ${entryPosition(lifted, inlineTrigger)}`}>{inlineTrigger ? '手记' : '随身手记'}</button></Dialog.Trigger>
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
      <Dialog.Content className="fixed bottom-4 left-3 right-3 z-50 flex max-h-[85dvh] min-w-0 flex-col rounded-xl border border-line bg-panel p-4 text-paper shadow-xl sm:bottom-6 sm:left-auto sm:right-6 sm:w-[440px]" aria-describedby="play-notebook-description">
        <div className="flex items-center justify-between gap-3">
          <Dialog.Title className="text-lg font-semibold">随身手记</Dialog.Title>
          <Dialog.Close asChild><button type="button" className={buttonClass} aria-label="关闭笔记本">关闭</button></Dialog.Close>
        </div>
        <Dialog.Description id="play-notebook-description" className="mt-2 text-xs leading-5 text-mist">本机保存 · 不会发给 AI · 不同浏览器不同步。清除浏览器数据会丢失笔记，重要内容请导出。</Dialog.Description>
        <Tabs.Root value={tab} onValueChange={setTab} className="mt-3 flex min-h-0 flex-1 flex-col">
          <Tabs.List aria-label="笔记本内容" className="flex gap-1 border-b border-line pb-2">
            {([['notes', '笔记'], ['clips', '收藏'], ['memories', '回忆']] as const).map(([value, label]) => <Tabs.Trigger key={value} value={value} className="rounded-md px-4 py-2 text-sm text-mist data-[state=active]:bg-raised data-[state=active]:text-paper focus-visible:outline-2 focus-visible:outline-brass">{label}</Tabs.Trigger>)}
          </Tabs.List>
          <Tabs.Content value="notes" className="min-h-0 overflow-y-auto pt-3">
            <label htmlFor="play-notebook-note" className="text-sm text-mist">疑点、人物关系和待核对的说法</label>
            <textarea id="play-notebook-note" value={snapshot.document.note} maxLength={NOTEBOOK_NOTE_LIMIT} disabled={!editable}
              onChange={event => { setNotice(''); persist({ ...draft.current, note: event.target.value }); }}
              placeholder="记下你的判断，也可以从材料或公开台词中收藏重点。"
              className="mt-2 min-h-52 w-full resize-y rounded-md border border-line bg-ink p-3 text-base leading-7 text-paper outline-none focus:border-brass disabled:opacity-50" />
            <p className="mt-1 text-right text-xs text-mist">{snapshot.document.note.length} / {NOTEBOOK_NOTE_LIMIT}</p>
          </Tabs.Content>
          <Tabs.Content value="clips" className="min-h-0 overflow-y-auto pt-3">
            <p className="mb-3 text-xs text-mist">{snapshot.document.clips.length} / 100 条 · 收藏保留当时的文字，不代表说法已经证实。</p>
            {snapshot.document.clips.length === 0 && <p className="py-5 text-sm text-mist">在材料或公开台词旁点击「收藏」，可集中查看。</p>}
            {snapshot.document.clips.map(item => <article key={item.id} className="mb-3 rounded-md border border-line p-3">
              <div className="flex items-start justify-between gap-3"><h3 className="min-w-0 break-words text-sm font-medium">{item.title}</h3>
                <button type="button" className="shrink-0 text-xs text-mist underline" disabled={!editable} aria-label={`移除收藏：${item.title}`}
                  onClick={() => { setNotice(''); persist({ ...draft.current, clips: draft.current.clips.filter(value => value.id !== item.id) }); }}>移除</button></div>
              <div className="mt-2"><PlayText text={item.text} /></div>
            </article>)}
          </Tabs.Content>
          <Tabs.Content value="memories" className="min-h-0 overflow-y-auto pt-3">
            <p className="mb-3 text-xs text-mist">仅显示当前角色已解锁的回忆，随本局进度更新。原卡不会存入本机笔记或随笔记导出。</p>
            {memories.length === 0 && <p className="py-5 text-sm text-mist">目前还没有解锁的回忆。</p>}
            {memories.map(memory => <article key={memory.id} className="mb-3 rounded-md border border-line p-3">
              <h3 className="break-words text-sm font-medium">{memory.title}</h3>
              <div className="mt-2"><PlayText text={memory.text} /></div>
            </article>)}
          </Tabs.Content>
        </Tabs.Root>
        <div className="mt-3 border-t border-line pt-3">
          <p role="status" className="text-xs text-mist">{snapshot.status === 'loading' ? '正在读取本机笔记…' : snapshot.status === 'saved' ? '已保存到本机' : snapshot.status === 'ready' ? '本机笔记已就绪 · 输入即自动保存' : snapshot.status === 'unsaved' ? '当前修改尚未保存' : '本机笔记暂不可用'}</p>
          {snapshot.error && <p role="alert" className="mt-2 text-sm text-red-300">{snapshot.error}</p>}
          {notice && <p role="status" className="mt-2 text-sm text-mist">{notice}</p>}
          <div className="mt-3 flex flex-wrap gap-2">
            <button type="button" className={buttonClass} disabled={!editable} onClick={exportText}>导出笔记和收藏 .txt</button>
            {snapshot.status === 'unsaved' && <button type="button" className={buttonClass} onClick={() => persist(draft.current)}>重试保存</button>}
            {snapshot.status === 'unavailable' && <button type="button" className={buttonClass} onClick={read}>重新读取</button>}
          </div>
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
