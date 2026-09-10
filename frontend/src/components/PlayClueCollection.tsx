'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import PlayText from './PlayText';
import { playerMaterialText } from '@/lib/playPresentation';
import {
  addCollectedClue, clueCollectionKey, clueId, emptyClueCollection, loadClueCollection,
  resolveCollectedClue, saveClueCollection, type AvailableClue, type ClueCollection, type ClueVisual,
} from '@/lib/playClueCollection';

export interface PlayClueCollectionProps {
  ownerId: string | null;
  playId: string;
  characterId: string;
  locked: boolean;
  available: AvailableClue[];
  selection?: { id: string };
  onSelectionHandled?: () => void;
  onRestoreFocus?: () => void;
  renderVisual: (visual: ClueVisual) => ReactNode;
}
const buttonClass = 'min-h-11 rounded-lg border border-brass/40 px-3 py-2 text-sm text-brass hover:bg-raised disabled:opacity-40';

export default function PlayClueCollection(props: PlayClueCollectionProps) {
  const key = props.locked ? null : clueCollectionKey(props.ownerId, props.playId, props.characterId);
  const [binding, setBinding] = useState(() => ({ key, blocked: key ? undefined : props.selection }));
  if (binding.key !== key) {
    setBinding({ key, blocked: props.selection });
    return null;
  }
  if (!key) return <button type="button" disabled className="play-nav-action" title="登录并确认当前资料后可用">我收藏的线索</button>;
  return <ClueSession key={key} {...props} storageKey={key} selection={props.selection === binding.blocked ? undefined : props.selection} />;
}

function ClueSession({ storageKey, available, selection, onSelectionHandled, onRestoreFocus, renderVisual }: PlayClueCollectionProps & { storageKey: string }) {
  const [open, setOpen] = useState(false);
  const [selectedId, setSelectedId] = useState<string>();
  const [query, setQuery] = useState('');
  const [snapshot, setSnapshot] = useState<{ document: ClueCollection; status: 'loading' | 'ready' | 'saved' | 'unsaved' | 'unavailable'; error: string }>(() => ({ document: emptyClueCollection(), status: 'loading', error: '' }));
  const [notice, setNotice] = useState('');
  const current = useRef(emptyClueCollection());
  const mounted = useRef(false);
  const processed = useRef<typeof selection>(undefined);
  const opener = useRef<HTMLElement | null>(null);
  function read() {
    const result = loadClueCollection(storageKey);
    current.current = result.ok ? result.document : emptyClueCollection();
    setSnapshot({ document: current.current, status: result.ok ? 'ready' : 'unavailable', error: result.ok ? '' : result.error });
  }
  useEffect(() => {
    mounted.current = true;
    const result = loadClueCollection(storageKey);
    current.current = result.ok ? result.document : emptyClueCollection();
    setSnapshot({ document: current.current, status: result.ok ? 'ready' : 'unavailable', error: result.ok ? '' : result.error });
    return () => { mounted.current = false; };
  }, [storageKey]);
  function persist(document: ClueCollection) {
    if (!mounted.current) return;
    current.current = document;
    const result = saveClueCollection(storageKey, document);
    setSnapshot({ document, status: result.ok ? 'saved' : 'unsaved', error: result.ok ? '' : result.error });
  }
  useEffect(() => {
    if (!selection || processed.current === selection || snapshot.status === 'loading') return;
    processed.current = selection;
    opener.current = typeof document !== 'undefined' && typeof HTMLElement !== 'undefined' && document.activeElement instanceof HTMLElement ? document.activeElement : null;
    // Consume only an explicit player action, resolved against this projection.
    setOpen(true);
    setSelectedId(selection.id); setQuery('');
    const item = available.find(value => clueId(value) === selection.id);
    // This effect acknowledges the external selection and its storage result.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (snapshot.status === 'unavailable') setNotice('本机收藏尚未读取成功，请重试读取后再次收藏。');
    else if (!item) setNotice('这条线索当前不可读取，未加入收藏。请刷新资料后重试。');
    else {
      const result = addCollectedClue(current.current, item);
      if (!result.ok) setNotice(result.error);
      else if (result.duplicate) setNotice('这条线索已在收藏中。');
      else {
        current.current = result.document;
        const saved = saveClueCollection(storageKey, result.document);
        setSnapshot({ document: result.document, status: saved.ok ? 'saved' : 'unsaved', error: saved.ok ? '' : saved.error });
        setNotice(saved.ok ? '已加入我收藏的线索。' : '已加入当前草稿，尚未保存到本机。');
      }
    }
    onSelectionHandled?.();
  }, [selection, snapshot.status, available, onSelectionHandled, storageKey]);
  const editable = snapshot.status !== 'loading' && snapshot.status !== 'unavailable';
  const visible = snapshot.document.entries.map(saved => ({ saved, item: resolveCollectedClue(saved, available) }));
  const matches = visible.filter(({ item }) => !query.trim() || (item && `${item.title}\n${playerMaterialText(item.text)}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())));
  const selected = matches.find(({ saved }) => clueId(saved) === selectedId) || matches[0];
  const selectedItem = selected?.item;
  return <Dialog.Root open={open} onOpenChange={setOpen}>
    <Dialog.Trigger asChild><button type="button" className="play-nav-action" onClick={event => { opener.current = event.currentTarget; }}>我收藏的线索</button></Dialog.Trigger>
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-[70] bg-black/50" />
      <Dialog.Content onCloseAutoFocus={event => {
        const target = opener.current;
        if (target?.isConnected && target.matches('button,a[href],input,textarea,select,[tabindex]') && !target.matches(':disabled') && target.getClientRects().length && !target.closest('details:not([open])')) {
          event.preventDefault(); target.focus({ preventScroll: true });
        } else if (onRestoreFocus) { event.preventDefault(); onRestoreFocus(); }
        opener.current = null;
      }} className="fixed bottom-4 left-3 right-3 z-[70] flex max-h-[85dvh] min-w-0 flex-col rounded-xl border border-line bg-panel p-4 text-paper shadow-xl sm:bottom-6 sm:left-auto sm:right-6 sm:w-[min(640px,calc(100vw-48px))]" aria-describedby="play-clues-description">
        <div className="flex items-center justify-between gap-3"><Dialog.Title className="text-lg font-semibold">我收藏的线索</Dialog.Title><Dialog.Close asChild><button type="button" className={buttonClass} aria-label="关闭线索收藏">关闭</button></Dialog.Close></div>
        <Dialog.Description id="play-clues-description" className="mt-2 text-xs leading-5 text-mist">点选一条查看完整内容。收藏仅保存在本机，不会公开或发给 AI。</Dialog.Description>
        {visible.length > 0 && <label className="mt-3 text-xs text-mist">搜索收藏<input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="输入人物、地点或关键词" className="mt-1 min-h-11 w-full rounded-lg border border-line bg-ink px-3 text-sm text-paper" /></label>}
        <div className="mt-3 min-h-0 overflow-y-auto overscroll-contain">
          {editable && visible.length === 0 && <p className="py-6 text-sm leading-7 text-mist">还没有收藏线索。在阅读材料或调查线索旁点击「收藏线索」，即可在这里随时查看。</p>}
          {visible.length > 0 && matches.length === 0 && <p role="status" className="py-4 text-sm text-mist">没有匹配的收藏，试试其他关键词。</p>}
          <div aria-label="收藏索引" className="grid max-h-36 grid-cols-2 gap-2 overflow-y-auto overscroll-contain pr-1 sm:grid-cols-3">{matches.map(({ saved, item }) => <button key={clueId(saved)} type="button" aria-label={`查看收藏：${item?.title || '当前不可读取的收藏'}`} aria-pressed={selected && clueId(selected.saved) === clueId(saved)} aria-controls="play-collected-detail" onClick={() => setSelectedId(clueId(saved))} className={`min-h-16 min-w-0 rounded-lg border p-2 text-left hover:bg-raised ${selected && clueId(selected.saved) === clueId(saved) ? 'border-brass bg-brass/10' : 'border-line'}`}>
            <span className="block truncate text-xs font-medium text-brass">{item?.title || '当前不可读取的收藏'}</span><span className="mt-1 line-clamp-2 break-words text-xs leading-5 text-mist">{item ? Array.from(playerMaterialText(item.text).replace(/[#*\n]/g, ' ').trim()).slice(0, 48).join('') : '暂时无法查看'}</span>
          </button>)}</div>
          {selected && <article id="play-collected-detail" aria-label="选中收藏详情" key={clueId(selected.saved)} className="mt-3 min-w-0 rounded-lg border border-line p-3">
            <div className="flex items-start justify-between gap-3"><h3 className="min-w-0 break-words font-medium">{selectedItem?.title || '当前不可读取的收藏'}</h3><button type="button" disabled={!editable} className={`${buttonClass} shrink-0`} aria-label={`取消收藏：${selectedItem?.title || '当前不可读取的收藏'}`} onClick={() => { setNotice(''); persist({ version: 1, entries: current.current.entries.filter(value => clueId(value) !== clueId(selected.saved)) }); }}>取消收藏</button></div>
            {selectedItem ? <><div className="mt-3"><PlayText text={selectedItem.text} /></div>{open && selectedItem.visuals.map(visual => renderVisual(visual))}</> : <p className="mt-2 text-sm text-mist">资料已变化或不在当前可见范围内。刷新确认资料后再查看；本机副本不会代替读取权限。</p>}
          </article>}
        </div>
        <div className="mt-3 border-t border-line pt-3">
          <p role="status" className="text-xs text-mist">{snapshot.status === 'loading' ? '正在读取本机收藏…' : snapshot.status === 'saved' ? '已保存到本机' : snapshot.status === 'ready' ? `${visible.filter(value => value.item).length} 条可查看 · 最多收藏 100 条` : snapshot.status === 'unsaved' ? '当前改动尚未保存' : '本机收藏暂不可用'}</p>
          {snapshot.error && <p role="alert" className="mt-2 text-sm text-red-300">{snapshot.error}</p>}
          {notice && <p role="status" className="mt-2 text-sm text-mist">{notice}</p>}
          {snapshot.status === 'unsaved' && <button type="button" className={`mt-3 ${buttonClass}`} onClick={() => persist(current.current)}>重试保存</button>}
          {snapshot.status === 'unavailable' && <button type="button" className={`mt-3 ${buttonClass}`} onClick={read}>重新读取</button>}
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
