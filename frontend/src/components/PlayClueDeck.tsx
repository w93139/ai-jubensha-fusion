import { useRef, useState, type ReactNode } from 'react';
import { ChevronLeft, ChevronRight, Layers } from 'lucide-react';
import styles from './PlayClueDeck.module.css';

/** A reading surface only. Items have already passed the player's visibility filter. */
export default function PlayClueDeck({ label, items }: {
  label: string; items: { id: string; heading: string; content: ReactNode }[];
}) {
  const track = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState(0);
  const current = Math.min(selected, Math.max(0, items.length - 1));
  const move = (index: number) => {
    const next = Math.max(0, Math.min(items.length - 1, index));
    setSelected(next);
    const rail = track.current;
    const card = rail?.children[next] as HTMLElement | undefined;
    if (rail && card) rail.scrollTo({ left: card.offsetLeft, behavior: 'auto' });
  };
  if (!items.length) return null;
  return <div className={styles.deck} role="region" aria-label={`${label}卡组`} aria-roledescription="可横向翻阅的线索卡组">
    <div className={styles.controls}>
      <span className="inline-flex items-center gap-2 text-xs text-mist"><Layers className="h-4 w-4" aria-hidden="true" />左右翻阅 · 卡内上下阅读</span>
      <div className="flex items-center gap-2">
        <button type="button" className={styles.arrow} aria-label={`${label}上一张`} disabled={current === 0} onClick={() => move(current - 1)}><ChevronLeft aria-hidden="true" size={18} /></button>
        <span role="status" aria-live="polite" className="min-w-14 text-center text-xs text-mist">{current + 1} / {items.length}</span>
        <button type="button" className={styles.arrow} aria-label={`${label}下一张`} disabled={current === items.length - 1} onClick={() => move(current + 1)}><ChevronRight aria-hidden="true" size={18} /></button>
      </div>
    </div>
    <div ref={track} className={styles.track} tabIndex={0} aria-label={`${label}左右滚动`} onKeyDown={event => {
      if (event.target !== event.currentTarget) return;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); move(current + (event.key === 'ArrowLeft' ? -1 : 1)); }
      if (event.key === 'Home' || event.key === 'End') { event.preventDefault(); move(event.key === 'Home' ? 0 : items.length - 1); }
    }} onScroll={() => {
      const rail = track.current;
      if (!rail) return;
      let nearest = 0, distance = Infinity;
      Array.from(rail.children).forEach((node, index) => {
        const delta = Math.abs((node as HTMLElement).offsetLeft - rail.scrollLeft);
        if (delta < distance) { nearest = index; distance = delta; }
      });
      setSelected(nearest);
    }}>
      {items.map((item, index) => <article key={item.id} className={styles.card} data-active={index === current} aria-label={item.heading}
        onFocusCapture={() => setSelected(index)}>
        <button type="button" className={styles.cardTitle} aria-pressed={index === current} onClick={() => move(index)}>{item.heading}<span className="text-xs font-normal text-mist">{index + 1} / {items.length}</span></button>
        <div className={styles.body} tabIndex={0} aria-label={`${item.heading}内容`}>{item.content}</div>
      </article>)}
    </div>
  </div>;
}
