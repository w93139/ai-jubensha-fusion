import PlayImageView from './PlayImageView';
import { playImageRotation } from '@/lib/playImageOrientation';
import { useCallback, useEffect, useRef, useState } from 'react';
import packagePreviewService, { watchPackagePreviewAuth } from '@/services/packagePreviewService';

/** Authenticated opening images stay local; no public image proxy or source path. */
export default function OpeningImage({ sessionId, visualId, label }: { sessionId: string; visualId: string; label: string }) {
  const root = useRef<HTMLDivElement>(null);
  const [url, setUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [invalid, setInvalid] = useState(false);
  const [rotation, setRotation] = useState(0);
  const [zoom, setZoom] = useState(1);
  const state = useRef<{ generation: number; controller?: AbortController; url?: string;
    auth?: ReturnType<typeof watchPackagePreviewAuth> }>({ generation: 0 });
  const load = useCallback(async () => {
    const current = state.current;
    if (!current.auth?.isCurrent() || current.controller || current.url) return;
    const generation = current.generation;
    const controller = new AbortController(); current.controller = controller;
    setLoading(true); setError('');
    try {
      const blob = await packagePreviewService.image(sessionId, visualId, controller.signal);
      if (controller.signal.aborted || generation !== current.generation || !current.auth?.isCurrent()) return;
      const orientation = await playImageRotation(blob);
      if (controller.signal.aborted || generation !== current.generation || !current.auth?.isCurrent()) return;
      const next = URL.createObjectURL(blob); current.url = next; setRotation(orientation); setUrl(next);
    } catch {
      if (!controller.signal.aborted && generation === current.generation) setError('原图读取失败，请重试。');
    } finally {
      if (generation === current.generation) { current.controller = undefined; setLoading(false); }
    }
  }, [sessionId, visualId]);
  const latestLoad = useRef(load);
  useEffect(() => { latestLoad.current = load; }, [load]);
  useEffect(() => {
    const current = state.current;
    const clear = () => {
      current.generation++; current.controller?.abort(); current.controller = undefined;
      if (current.url) URL.revokeObjectURL(current.url); current.url = undefined;
    };
    current.auth = watchPackagePreviewAuth(() => {
      clear(); setUrl(''); setInvalid(true); setLoading(false); setError('登录身份已变化，请重新打开开场。');
    });
    let observer: IntersectionObserver | undefined;
    if (root.current && typeof IntersectionObserver !== 'undefined') {
      observer = new IntersectionObserver(entries => {
        if (entries.some(entry => entry.isIntersecting)) { observer?.disconnect(); void latestLoad.current(); }
      }, { rootMargin: '120px' });
      observer.observe(root.current);
    }
    return () => { observer?.disconnect(); current.auth?.dispose(); clear(); };
  }, []);
  return <div ref={root} className="mt-3 min-w-0 rounded-lg border border-line p-3">
    <p className="text-sm text-brass">{label}</p>
    {!url && <button type="button" disabled={loading || invalid} onClick={() => void load()} className="mt-2 min-h-11 rounded border border-brass px-3 py-2 text-sm text-brass disabled:opacity-50">{loading ? '正在读取原图…' : error ? '重试原图' : '查看原图'}</button>}
    {url && <PlayImageView url={url} label={label} rotation={rotation} onRotate={() => setRotation(value => (value + 90) % 360)} zoom={zoom} onZoom={() => setZoom(value => value === 1 ? 2 : 1)} />}
    {error && <p role="alert" className="mt-2 text-sm text-mist">{error}</p>}
  </div>;
}
