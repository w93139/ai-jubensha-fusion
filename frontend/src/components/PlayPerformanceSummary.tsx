import type { ReactNode } from 'react';
import type { PerformanceGroup } from '@/lib/playPerformance';
import type { PlayMaterial } from '@/types/packagePlay';
import PlayText from './PlayText';

export default function PlayPerformanceSummary({ group, renderVisuals }: {
  group: PerformanceGroup;
  renderVisuals: (material: PlayMaterial) => ReactNode;
}) {
  if (!group.items.length) return null;
  return <section aria-label="你的表现" className="min-w-0" data-performance-summary>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-xl font-semibold">你的表现</h3>
      <span className="text-xs text-mist">角色要求 · 仅自己可见</span>
    </div>
    {renderVisuals(group.heading)}
    {group.introduction && <div className="mt-3 text-mist"><PlayText text={group.introduction.text} />{renderVisuals(group.introduction)}</div>}
    <ol aria-label="你的表现条目" className="mt-4 list-none divide-y divide-line/60">
      {group.items.map(item => <li key={item.id} className="py-3 first:pt-0 last:pb-0 [&>div]:leading-7 [&_p]:mb-1" data-performance-item>
        <PlayText text={item.text} />
        {renderVisuals(item)}
        {item.retelling && <p className="mt-1 text-xs leading-5 text-brass">{item.retelling === 'MUST_RETELL' ? '需讲述' : '可选择讲述'}</p>}
      </li>)}
    </ol>
    {group.items.some(item => item.retelling) && <p className="mt-4 text-xs leading-6 text-mist">原本不能出示，讲述时请使用自己的话。</p>}
  </section>;
}
