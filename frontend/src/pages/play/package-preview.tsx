import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/router';
import AuthGuard from '@/components/AuthGuard';
import AppLayout from '@/components/AppLayout';
import PlayText from '@/components/PlayText';
import PlayReferencePanel from '@/components/PlayReferencePanel';
import OpeningImage from '@/components/OpeningImage';
import { useAuthStore } from '@/stores/authStore';
import packagePlayService, { PackagePlayError, preparePackagePlayAttempt, type PackagePlayAttempt } from '@/services/packagePlayService';
import packagePreviewService, { watchPackagePreviewAuth, type PackagePreview, type PreviewRelease } from '@/services/packagePreviewService';

export default function PackagePreviewPage() {
  const router = useRouter();
  const owner = useAuthStore(state => state.isAuthenticated ? state.user?.id : undefined);
  return <AuthGuard><AppLayout>{router.isReady && <OpeningPreview key={`${owner}:${router.asPath}`} />}</AppLayout></AuthGuard>;
}

function OpeningPreview() {
  const router = useRouter();
  const [releases, setReleases] = useState<PreviewRelease[]>([]);
  const [releaseId, setReleaseId] = useState('');
  const [characterId, setCharacterId] = useState('');
  const [preview, setPreview] = useState<PackagePreview>();
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [authInvalid, setAuthInvalid] = useState(false);
  const [startLabel, setStartLabel] = useState('开始游戏');
  const startMode = useRef<'start' | 'check' | 'retry' | 'resume'>('start');
  const playAttempt = useRef<PackagePlayAttempt | undefined>(undefined);
  const savedPlayId = useRef<string | undefined>(undefined);
  const active = useRef<AbortController | null>(null);
  const attempt = useRef<{ signature: string; key: string } | null>(null);
  const invalidated = useRef(false);
  const auth = useRef<ReturnType<typeof watchPackagePreviewAuth> | null>(null);
  const requestedRelease = typeof router.query.release_id === 'string' ? router.query.release_id : '';
  const requestedSession = typeof router.query.session === 'string' ? router.query.session : '';
  useEffect(() => {
    const guard = watchPackagePreviewAuth(() => {
      invalidated.current = true;
      active.current?.abort(); active.current = null; attempt.current = null; playAttempt.current = undefined; savedPlayId.current = undefined;
      setPreview(undefined); setReleases([]); setReleaseId(''); setCharacterId('');
      setBusy(false); setLoading(false); setAuthInvalid(true);
      setError('登录身份已变化，请刷新页面或重新打开开场。');
    });
    auth.current = guard;
    return () => { guard.dispose(); auth.current = null; active.current?.abort(); };
  }, []);
  useEffect(() => {
    if (!router.isReady || invalidated.current || !auth.current?.isCurrent()) return;
    const controller = new AbortController();
    active.current?.abort(); active.current = controller;
    const load = requestedSession
      ? packagePreviewService.read(requestedSession, controller.signal).then(value => {
        if (!controller.signal.aborted && auth.current?.isCurrent()) setPreview(value);
      })
      : packagePreviewService.releases(controller.signal).then(items => {
        if (controller.signal.aborted || !auth.current?.isCurrent()) return;
        setReleases(items); setReleaseId(items.some(item => String(item.id) === requestedRelease) ? requestedRelease : ''); setCharacterId('');
      });
    load.catch(cause => { if (!controller.signal.aborted && auth.current?.isCurrent()) setError(cause instanceof Error ? cause.message : '读取失败。'); })
      .finally(() => { if (!controller.signal.aborted && auth.current?.isCurrent()) { setLoading(false); active.current = null; } });
    return () => controller.abort();
  }, [router.isReady, requestedRelease, requestedSession]);
  const release = releases.find(item => String(item.id) === releaseId);
  const create = async () => {
    if (invalidated.current || !auth.current?.isCurrent() || !release || !release.characters.some(item => item.id === characterId) || active.current) return;
    const signature = JSON.stringify([release.id, characterId]);
    if (attempt.current?.signature !== signature) attempt.current = { signature, key: crypto.randomUUID() };
    const controller = new AbortController(); active.current = controller;
    setBusy(true); setError('');
    try {
      const result = await packagePreviewService.create(release.id, characterId, attempt.current.key, controller.signal);
      if (!controller.signal.aborted && auth.current?.isCurrent()) await router.replace({ pathname: '/play/package-preview', query: { session: result.session_id } });
    } catch (cause) { if (!controller.signal.aborted && auth.current?.isCurrent()) setError(cause instanceof Error ? cause.message : '打开失败。'); }
    finally { if (!controller.signal.aborted && auth.current?.isCurrent()) { active.current = null; setBusy(false); } }
  };
  const start = async () => {
    if (!preview || active.current || invalidated.current || !auth.current?.isCurrent()) return;
    const controller = new AbortController(); active.current = controller;
    const checkOnly = startMode.current === 'check';
    let dispatched = false;
    let saved = false;
    setBusy(true); setError('');
    try {
      let result = await packagePlayService.lookup(preview.session_id, controller.signal);
      if (controller.signal.aborted || !auth.current?.isCurrent()) return;
      if (result === null && savedPlayId.current) throw new Error('saved-game-missing');
      if (result === null && checkOnly) {
        startMode.current = 'retry'; setStartLabel('重试开始游戏');
        setError('还未找到已保存的游戏。可点击“重试开始游戏”继续。');
        return;
      }
      if (result === null) {
        const payload = { opening_session_id: preview.session_id };
        const nextAttempt = preparePackagePlayAttempt(payload, playAttempt.current); playAttempt.current = nextAttempt;
        dispatched = true;
        result = await packagePlayService.create({ ...payload, idempotency_key: nextAttempt.key }, controller.signal);
        if (controller.signal.aborted || !auth.current?.isCurrent()) return;
      }
      // Opening bindings are immutable. Only the saved ID is passed to the game
      // page, which independently validates and reads its full authorized view.
      if (!result || !/^play-[0-9a-f]{32}$/.test(result.play_id)
        || result.opening_session_id !== preview.session_id || result.release_id !== preview.release_id
        || result.version_id !== preview.version_id || result.selected_character_id !== preview.selected_character_id
        || (savedPlayId.current && result.play_id !== savedPlayId.current)) throw new Error('opening-game-binding-mismatch');
      savedPlayId.current = result.play_id; saved = true;
      startMode.current = 'resume'; setStartLabel('继续游戏');
      const navigated = await router.replace({ pathname: '/play/package-play', query: { play: result.play_id } });
      if (navigated === false) throw new Error('navigation-cancelled');
    } catch (cause) {
      if (controller.signal.aborted || !auth.current?.isCurrent()) return;
      if (saved) {
        setError('游戏已保存，页面未能打开。请点击“继续游戏”返回原进度。');
      } else if (dispatched) {
        startMode.current = 'check'; setStartLabel('检查开始结果');
        setError('尚未确认游戏是否开始。请点击“检查开始结果”查看已保存进度。');
      } else {
        if (startMode.current !== 'check') { startMode.current = 'retry'; setStartLabel('重试开始游戏'); }
        setError(cause instanceof PackagePlayError && [401, 403].includes(cause.status)
          ? '请确认当前账号已登录，并有权进入这份开场。'
          : '暂时无法确认游戏进度，请稍后再试；开场资料仍可阅读。');
      }
    } finally {
      if (!controller.signal.aborted && auth.current?.isCurrent()) { active.current = null; setBusy(false); }
    }
  };
  const inputClass = 'mt-2 w-full min-w-0 rounded border border-line bg-ink p-3 text-paper';
  return <main className={`opening-page mx-auto max-w-3xl px-4 pb-24 text-paper ${!authInvalid && !loading && preview ? 'pt-0' : 'pt-20 md:pt-12'}`}>
    {(!preview || loading || authInvalid) && <>
    <p className="text-xs text-brass">选择剧本 · 进入故事</p>
    <h1 className="mt-2 font-dossier text-3xl">选择你的故事</h1>
    <p className="mt-4 text-sm leading-6 text-mist">选择角色，阅读自己的开场资料，然后进入游戏。</p>
    </>}
    {loading && <p role="status" className="mt-6">正在读取……</p>}
    {error && (!preview || authInvalid) && <p role="alert" className="mt-6 rounded border border-red-500/40 p-4 text-red-200">{error}</p>}
    {!authInvalid && !loading && !preview && !requestedSession && <section className="mt-6 space-y-5 rounded border border-line bg-panel p-5" aria-label="选择开场角色">
      <label className="block text-sm">剧本<select className={inputClass} value={releaseId} disabled={busy} onChange={event => { setReleaseId(event.target.value); setCharacterId(''); }}>
        <option value="">请选择剧本</option>{releases.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
      </select></label>
      <label className="block text-sm">你的角色<select className={inputClass} value={characterId} disabled={!release || busy} onChange={event => setCharacterId(event.target.value)}>
        <option value="">请选择角色</option>{release?.characters.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select></label>
      <p className="text-xs leading-5 text-mist">选好角色后，先阅读你的开场资料。游戏会保留本次选择和之后的进度。</p>
      <button className="rounded bg-brass px-4 py-3 text-ink disabled:opacity-40" disabled={busy || !release || !characterId} onClick={create}>{busy ? '正在打开……' : '阅读开场'}</button>
      {!releases.length && <p className="text-sm text-mist">暂时没有可选择的剧本。</p>}
    </section>}
    {!authInvalid && !loading && preview && <OpeningReading preview={preview} onStart={start} starting={busy} startLabel={startLabel} startError={error} />}
    <div className="mt-8 flex justify-center"><Link href="/" className="min-h-11 rounded-lg border border-brass/40 px-5 py-3 text-sm text-brass hover:bg-raised">返回首页</Link></div>
  </main>;
}

export function OpeningReading({ preview, onStart, starting = false, startLabel = '开始游戏', startError = '' }: { preview: PackagePreview; onStart?: () => void; starting?: boolean; startLabel?: string; startError?: string }) {
  const characterName = preview.characters.find(item => item.id === preview.selected_character_id)?.name || '我的角色';
  return <><header aria-label="开场顶部导航" className="opening-topbar sticky top-14 z-30 rounded-b-xl border border-line bg-panel p-3 shadow-lg md:top-0 sm:p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs text-brass">阅读开场</p>
          <h1 className="mt-1 break-words font-dossier text-xl font-semibold sm:text-2xl">{preview.script.title}</h1>
          <p className="mt-1 text-sm text-mist">你的角色：{characterName}</p>
        </div>
        <button type="button" onClick={onStart} disabled={!onStart || starting} className="opening-start group inline-flex min-h-11 shrink-0 items-center justify-center gap-2 rounded-lg bg-brass px-4 py-3 text-sm font-semibold text-ink disabled:opacity-50">
          {starting ? '正在进入…' : startLabel}<span aria-hidden="true" className="opening-start-arrow">→</span>
        </button>
      </div>
      <div className="mt-3 flex items-center justify-between gap-2 border-t border-line pt-2">
        <details className="shrink-0" onKeyDown={event => {
          if (event.key === 'Escape') { event.preventDefault(); event.currentTarget.open = false; event.currentTarget.querySelector('summary')?.focus({ preventScroll: true }); }
        }}>
          <summary className="min-h-11 cursor-pointer rounded-md px-2 py-3 text-sm text-brass">故事介绍</summary>
          <div className="absolute left-0 right-0 top-full mt-1 max-h-[45dvh] overflow-y-auto overscroll-contain rounded-xl border border-brass/40 bg-panel p-4 shadow-xl">
            <h2 className="font-semibold">故事介绍</h2><PlayText text={preview.introduction.text} />
            <p className="mt-3 text-sm leading-7 text-mist">先阅读公开规则和自己的开场资料。准备好后，点击“开始游戏”进入讨论与调查；已经开始过的游戏会继续原进度。</p>
            <button type="button" className="mt-3 block min-h-11 rounded border border-line px-3 py-2 text-sm text-brass" onClick={event => {
              const details = event.currentTarget.closest('details');
              if (details) { details.open = false; details.querySelector('summary')?.focus({ preventScroll: true }); }
            }}>收起说明</button>
          </div>
        </details>
        <nav aria-label="随时查阅" className="play-topbar-primary grid min-w-0 grid-cols-2 gap-1">
          <PlayReferencePanel toolbar scope={`opening:${preview.session_id}:${preview.selected_character_id}`} characterName={characterName} materials={[...preview.private_knowledge, ...preview.private_evidence, ...(preview.reading_supplements || [])]} rulesMaterials={preview.public_knowledge} memories={[]} />
        </nav>
      </div>
      {startError && <p role="alert" className="mt-2 rounded border border-red-500/40 px-3 py-2 text-sm text-red-200">{startError}</p>}
    </header>
    <article className="mt-6 space-y-5" aria-label="我的开场资料">
      {!!preview.reading_supplements?.length && <section className="rounded border border-line bg-panel p-5" aria-label="开场补充资料"><h2 className="text-lg">开场补充资料</h2>{preview.reading_supplements.map(item => <div key={item.id} className="mt-4"><PlayText text={item.text} /></div>)}</section>}
      {([
        ['公开资料与玩法', [...preview.public_knowledge.map(item => ({ ...item, collection: 'knowledge' as const })), ...preview.public_evidence.map(item => ({ ...item, collection: 'evidence' as const }))]],
        ['我的角色资料', [...preview.private_knowledge.map(item => ({ ...item, collection: 'knowledge' as const })), ...preview.private_evidence.map(item => ({ ...item, collection: 'evidence' as const }))]],
      ] as const).map(([title, items]) => <section key={title} className="rounded border border-line bg-panel p-5"><h2 className="text-lg">{title}</h2>
        {items.length ? items.map((item, index) => <div key={`${item.id}-${index}`} className="mt-4 rounded border border-line p-4"><PlayText text={item.text} />
          {preview.visuals?.filter(visual => visual.collection === item.collection && visual.material_id === item.id).map(visual => <OpeningImage key={`${preview.session_id}:${preview.selected_character_id}:${visual.id}`} sessionId={preview.session_id} visualId={visual.id} label={visual.label} />)}
          <p className="mt-2 text-xs text-mist">{item.kind === 'CLAIM' ? '角色说法 · ' : item.kind === 'INFERENCE' ? '推测 · ' : ''}{item.disclosure === 'KEEP_PRIVATE' ? '需保密' : item.disclosure === 'MUST_SHARE' ? '按规则需分享' : item.disclosure === 'MAY_SHARE' ? '允许自行分享' : '公开信息'}</p></div>) : <p className="mt-3 text-sm text-mist">当前没有这类资料。</p>}
      </section>)}
    </article></>;
}
