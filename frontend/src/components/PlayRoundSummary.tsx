import { useState, type ReactNode } from 'react';
import type { PackagePlay } from '@/types/packagePlay';
import { roundSummary, type RoundSummaryItem } from '@/lib/playRoundSummary';

export default function PlayRoundSummary({ view, phaseId, hiddenMemorySequences = [], onSelect, renderSource }: {
  view: PackagePlay; phaseId: string; hiddenMemorySequences?: number[];
  onSelect?: (item: RoundSummaryItem, target: HTMLButtonElement) => void;
  renderSource?: (item: RoundSummaryItem) => ReactNode;
}) {
  const [source, setSource] = useState<string | null>(null);
  const groups = roundSummary(view, phaseId, hiddenMemorySequences);
  return <section aria-label="本阶段关键摘记" className="space-y-3">
    <h3 className="text-sm font-semibold text-brass">关键摘记</h3>
    <p className="text-xs leading-5 text-mist">按本阶段已获内容摘录，完整上下文可查看依据。角色说法仍需核对。</p>
    {!groups.length && <p className="text-sm text-mist">本阶段还没有可整理的新内容。</p>}
    {groups.map(group => <section key={group.title} className="space-y-2"><h4 className="text-xs font-semibold text-brass">{group.title}（{group.items.length}）</h4>
      {group.items.slice(0, 3).map(item => <div key={item.id} data-summary-item={item.id}>
        <SummaryItem item={item} active={source === item.id} onSelect={onSelect} toggle={() => setSource(source === item.id ? null : item.id)} renderSource={renderSource} />
      </div>)}
      {group.items.length > 3 && <details><summary className="min-h-11 cursor-pointer py-2 text-xs text-brass">展开其余 {group.items.length - 3} 条</summary><div className="space-y-2">{group.items.slice(3).map(item => <SummaryItem key={item.id} item={item} active={source === item.id} onSelect={onSelect} toggle={() => setSource(source === item.id ? null : item.id)} renderSource={renderSource} />)}</div></details>}
    </section>)}
  </section>;
}

function SummaryItem({ item, active, onSelect, toggle, renderSource }: {
  item: RoundSummaryItem; active: boolean; toggle: () => void;
  onSelect?: (item: RoundSummaryItem, target: HTMLButtonElement) => void;
  renderSource?: (item: RoundSummaryItem) => ReactNode;
}) {
  return <div className="rounded-lg border border-line bg-panel p-3">
    <p className="text-xs text-mist">{item.label}</p><p className="mt-1 break-words text-sm leading-6 text-paper">{item.excerpt}</p>
    <button type="button" className="mt-1 min-h-11 rounded px-2 py-2 text-xs text-brass hover:bg-raised" aria-expanded={onSelect ? undefined : active} onClick={event => onSelect ? onSelect(item, event.currentTarget) : toggle()}>{active ? '收起依据' : '查看依据'}</button>
    {active && renderSource && <div className="mt-2 border-t border-line pt-3">{renderSource(item)}</div>}
  </div>;
}
