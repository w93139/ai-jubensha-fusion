import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import AppLayout from '@/components/AppLayout';
import { useAuthStore } from '@/stores/authStore';
import packagePlayService, { watchPackagePlayAuth, type PlayLibraryItem } from '@/services/packagePlayService';
import packagePreviewService, { type PreviewRelease } from '@/services/packagePreviewService';

const action = 'inline-flex min-h-11 items-center justify-center rounded-lg bg-brass px-5 py-3 text-sm font-semibold text-ink transition-colors hover:bg-brass/85 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-brass';
const secondary = 'inline-flex min-h-11 items-center justify-center rounded-lg border border-brass/40 px-4 py-3 text-sm text-brass hover:bg-raised focus-visible:outline focus-visible:outline-2 focus-visible:outline-brass';
const card = 'rounded-xl border border-line bg-panel p-5 sm:p-6';
export const savedPlayHref = (item: PlayLibraryItem) => `/play/package-play?play=${encodeURIComponent(item.play_id)}`;

function SavedGame({ item, recent = false }: { item: PlayLibraryItem; recent?: boolean }) {
  return <article className={`${card} ${recent ? 'border-brass/40' : ''}`}>
    {recent && <p className="mb-3 text-xs text-brass">最近一局</p>}
    <h3 className="break-words font-dossier text-xl text-paper">{item.title}</h3>
    <p className="mt-2 text-sm text-mist">{item.character_name} · {item.phase_label}</p>
    <p className="mt-2 text-xs text-mist">最近进度 <time dateTime={item.updated_at}>{new Date(item.updated_at).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</time></p>
    <Link className={`mt-5 ${recent ? action : secondary}`} href={savedPlayHref(item)}>{item.settled ? '查看结局' : '继续游戏'}</Link>
  </article>;
}

/** A mounted library is bound to one authenticated identity. Token changes retire it. */
export function PlayerLibrary({ recordsOnly = false }: { recordsOnly?: boolean }) {
  const [items, setItems] = useState<PlayLibraryItem[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [releases, setReleases] = useState<PreviewRelease[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(!recordsOnly);
  const [catalogError, setCatalogError] = useState('');
  const [invalid, setInvalid] = useState(false);
  const [retry, setRetry] = useState(0);
  const guard = useRef<ReturnType<typeof watchPackagePlayAuth> | null>(null);
  const controllers = useRef(new Set<AbortController>());
  const pending = useRef(false);
  const offset = useRef(0);
  const loadPage = async (reset = false) => {
    if (pending.current || !guard.current?.isCurrent()) return;
    pending.current = true; setLoading(true); setError('');
    const controller = new AbortController(); controllers.current.add(controller);
    try {
      const page = await packagePlayService.library(reset ? 0 : offset.current, 12, controller.signal);
      if (controller.signal.aborted || !guard.current?.isCurrent()) return;
      offset.current = (reset ? 0 : offset.current) + page.items.length;
      setItems(previous => reset ? page.items : [...previous, ...page.items.filter(item => !previous.some(old => old.play_id === item.play_id))]);
      setHasMore(page.has_more);
    } catch {
      if (!controller.signal.aborted && guard.current?.isCurrent()) setError('暂时无法读取游戏记录。请重试，你的进度仍保留在原游戏中。');
    } finally {
      controllers.current.delete(controller);
      if (!controller.signal.aborted && guard.current?.isCurrent()) { pending.current = false; setLoading(false); }
    }
  };
  useEffect(() => {
    const active = controllers.current;
    guard.current = watchPackagePlayAuth(() => {
      active.forEach(controller => controller.abort());
      setInvalid(true); setItems([]); setReleases([]); setError(''); setCatalogError('');
    });
    void loadPage(true);
    if (!recordsOnly) {
      const controller = new AbortController(); active.add(controller);
      packagePreviewService.releases(controller.signal).then(result => {
        if (controller.signal.aborted || !guard.current?.isCurrent()) return;
        if (!Array.isArray(result) || result.some(item => !Number.isSafeInteger(item.id) || item.id < 1 || typeof item.title !== 'string' || !item.title.trim() || !Array.isArray(item.characters))) throw new Error('invalid catalogue');
        setReleases(result);
      }).catch(() => {
        if (!controller.signal.aborted && guard.current?.isCurrent()) setCatalogError('暂时无法读取可玩的剧本，请重试。');
      }).finally(() => {
        active.delete(controller);
        if (!controller.signal.aborted && guard.current?.isCurrent()) setCatalogLoading(false);
      });
    }
    return () => { guard.current?.dispose(); active.forEach(controller => controller.abort()); pending.current = false; };
    // A retry starts a fresh request set; identity changes are handled by the parent key and token guard.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recordsOnly, retry]);
  if (invalid) return <div className={card}><p role="alert">登录状态已变化，请刷新首页后继续。</p><button className={`mt-4 ${secondary}`} onClick={() => window.location.reload()}>刷新首页</button></div>;
  return <>
    <section aria-label="我的游戏记录" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="text-xl font-semibold">{recordsOnly ? '我的记录' : '回到故事里'}</h2>{!recordsOnly && <Link className={secondary} href="/play/records">查看全部记录</Link>}</div>
      {loading && !items.length && <p role="status" className={card}>正在读取游戏记录……</p>}
      {error && <div role="alert" className={card}><p>{error}</p><button className={`mt-3 ${secondary}`} disabled={loading} onClick={() => void loadPage(!items.length)}>重试读取记录</button></div>}
      {!loading && !error && !items.length && <div className={card}><p className="text-mist">还没有开始过游戏。选择一个故事，开启你的第一局。</p><Link href="/play/package-preview" className={`mt-4 ${action}`}>开始新游戏</Link></div>}
      {recordsOnly ? <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{items.map(item => <SavedGame key={item.play_id} item={item} />)}</div> : items[0] && <SavedGame item={items[0]} recent />}
      {recordsOnly && hasMore && <button className={secondary} disabled={loading} onClick={() => void loadPage()}>{loading ? '正在读取……' : '加载更多记录'}</button>}
    </section>
    {!recordsOnly && <section aria-label="开始新游戏" className="mt-9 space-y-4">
      <h2 className="text-xl font-semibold">开始新游戏</h2>
      <p className="text-sm leading-6 text-mist">选择故事与角色，阅读开场后开始推理。</p>
      {catalogLoading && <p role="status">正在读取故事……</p>}
      {catalogError && <div role="alert" className={card}><p>{catalogError}</p><button className={`mt-3 ${secondary}`} onClick={() => { setCatalogLoading(true); setCatalogError(''); setRetry(value => value + 1); }}>重试读取剧本</button></div>}
      {!catalogLoading && !catalogError && !releases.length && <p className={card}>暂时没有可玩的剧本。</p>}
      <div className="grid gap-4 sm:grid-cols-2">{releases.map(item => <article key={item.id} className={card}>
        <p className="text-xs text-brass">单人推理 · AI 角色陪伴</p><h3 className="mt-3 break-words font-dossier text-2xl">{item.title}</h3>
        <p className="mt-2 text-sm text-mist">{item.player_count} 个角色</p>
        <Link className={`mt-5 ${action}`} href={`/play/package-preview?release_id=${item.id}`}>选择角色</Link>
      </article>)}</div>
    </section>}
  </>;
}

export default function PlayHome({ recordsOnly = false }: { recordsOnly?: boolean }) {
  const { user, isAuthenticated, isLoading } = useAuthStore();
  return <AppLayout><div className="mx-auto max-w-5xl px-5 py-8 sm:px-8 md:py-12">
    <header className="mb-8 flex flex-wrap items-start justify-between gap-4"><div><p className="text-xs text-brass">人生海海</p><h1 className="mt-2 font-dossier text-3xl">{recordsOnly ? '我的游戏记录' : '每个故事，都由你走进去'}</h1><p className="mt-3 text-sm leading-6 text-mist">{recordsOnly ? '继续未完的推理，或重温已经揭晓的故事。' : '独自进入故事，与 AI 角色交流，寻找属于你的答案。'}</p></div>{recordsOnly && <Link className={secondary} href="/">返回首页</Link>}</header>
    {isLoading ? <p role="status">正在确认登录状态……</p> : isAuthenticated && user ? <PlayerLibrary key={user.id} recordsOnly={recordsOnly} /> : <section className={card}><h2 className="text-xl">开始你的故事</h2><p className="mt-3 text-sm text-mist">登录后选择剧本，也可以继续之前保存的游戏。</p><Link className={`mt-5 ${action}`} href={`/auth/login?returnUrl=${encodeURIComponent(recordsOnly ? '/play/records' : '/')}`}>登录并继续</Link></section>}
  </div></AppLayout>;
}
