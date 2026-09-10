import { useState } from 'react';
import type { PackagePlay } from '@/types/packagePlay';
import { playerEndingText } from '@/lib/playPresentation';
import PlayText from './PlayText';

export default function PlayEndingPanel({ view }: { view: PackagePlay }) {
  // Scope disclosure to the current game and role even when props change in place.
  const scope = `${view.play_id}:${view.selected_character_id}`;
  const [expandedScope, setExpandedScope] = useState<string | null>(null);
  if (!view.settled || !view.settlement) return null;
  const result = view.full_game?.result;
  const name = (id: string) => view.characters.find(c => c.id === id)?.name || id;
  const mine = result?.endings.filter(e => e.character_ids.includes(view.selected_character_id) && e.texts.some(text => text.trim())) || [];
  const others = result?.endings.filter(e => !e.character_ids.includes(view.selected_character_id) && e.texts.some(text => text.trim())) || [];
  const otherTotals = result?.totals.filter(t => t.character_id !== view.selected_character_id) || [];
  const expanded = expandedScope === scope;
  const box = 'min-w-0 space-y-4 rounded border border-brass/40 bg-panel p-5';
  const endings = (items: typeof mine) => items.map((ending, i) => <div key={i}>
    <h3 className="text-base font-semibold text-brass">{ending.character_ids.map(name).join('、')}的结局</h3>
    {ending.texts.map((text, j) => <PlayText key={j} text={playerEndingText(text)} />)}
  </div>);
  const scores = (id: string) => result?.totals.filter(t => t.character_id === id).map(total => <details key={id}>
    <summary className="min-h-11 cursor-pointer py-2 text-sm text-brass">{name(id)}：{total.total_points ?? '待核对'} / {total.max_points} 分 · 查看分项得分</summary>
    <ul className="ml-5 mt-2 list-disc space-y-2 text-sm">{result.goals.filter(g => g.character_id === id).map(g => <li key={g.id}>
      {g.title}：{g.points ?? '待核对'} / {g.max_points}
      {g.parts.map(p => <p key={p.id} className="mt-1 whitespace-pre-wrap break-words text-mist">{p.points ?? '待核对'} / {p.max_points} 分 · {p.explanation || '该项未附规则说明。'}</p>)}
    </li>)}</ul>
  </details>);
  return <>
    {result && <section aria-label="我的结局与得分" className={box}>
      <h2 className="text-lg font-semibold">我的结局</h2>
      {mine.length ? endings(mine) : <p className="text-sm text-mist">本局没有单独的角色结局，请查看下方结尾与真相复盘。</p>}
      {scores(view.selected_character_id)}
    </section>}
    <section aria-label="结尾与真相" className={box}>
      <h2 className="text-lg font-semibold">结尾与真相复盘</h2>
      <PlayText text={playerEndingText(view.settlement.text)} />
      <ul className="space-y-4">{view.settlement.truths.map(item => <li key={item.id}><PlayText text={playerEndingText(item.text)} /></li>)}</ul>
    </section>
    {(others.length > 0 || otherTotals.length > 0) && <section aria-label="其他角色结局" className={box}>
      <button type="button" aria-expanded={expanded} aria-controls="play-other-endings" onClick={() => setExpandedScope(expanded ? null : scope)} className="min-h-11 rounded-lg border border-brass px-4 py-3 text-sm font-semibold text-brass hover:bg-brass/10 focus-visible:outline-2 focus-visible:outline-brass">{expanded ? '收起其他角色结局' : '查看其他角色结局'}</button>
      {expanded && <div id="play-other-endings" className="space-y-6">{endings(others)}{otherTotals.map(total => <div key={total.character_id}>{scores(total.character_id)}</div>)}</div>}
    </section>}
  </>;
}
