import { playerMaterialText } from '@/lib/playPresentation';
import type { PackagePlay, RoundWorkspace } from '@/types/packagePlay';
import { roundMaterial } from './playRoundWorkspace';
import { extractTaskSections, extractRuleSections } from './playReference';

export type RoundSummaryItem = {
  id: string; label: string; excerpt: string;
  material?: string; record?: string;
};
export type RoundSummaryGroup = { title: string; items: RoundSummaryItem[] };

// Contiguous source excerpts, never a generated conclusion. Retain negatives,
// uncertainty and attribution, and visibly mark truncation.
export function summaryExcerpt(text: string, limit = 160): string {
  const normalized = playerMaterialText(text).replace(/^\s{0,3}#{1,6}\s+/gm, '').replace(/\*\*|__/g, '').replace(/\s+/g, ' ').trim();
  const chars = Array.from(normalized);
  return chars.slice(0, limit).join('') + (chars.length > limit ? '…' : '');
}

export function roundSummary(view: PackagePlay, phaseId: string, hiddenMemorySequences: number[] = []): RoundSummaryGroup[] {
  const phase = view.round_workspace?.phases.find(p => p.phase_id === phaseId);
  if (!phase) return [];
  const name = (id: string) => id === view.selected_character_id ? '我' : view.characters.find(c => c.id === id)?.name || '角色';
  const groups: RoundSummaryGroup[] = ['本阶段个人任务', '调查所得', '新增回忆 · 仅自己可见', '资料摘记', '角色说法 · 待核对', '我的发言与判断'].map(title => ({ title, items: [] }));
  const materialItem = (ref: RoundWorkspace['phases'][number]['materials'][number], text: string, label: string): RoundSummaryItem => ({ id: `${ref.collection}:${ref.id}`, material: `${ref.collection}:${ref.id}`, label, excerpt: summaryExcerpt(text) });
  for (const ref of phase.materials) {
    if (ref.collection === 'memory' && hiddenMemorySequences.includes(ref.sequence)) continue;
    const item = roundMaterial(view, ref);
    if (!item) continue;
    if (ref.collection === 'knowledge' && (item.text === view.single_player?.operation_rules || extractRuleSections([item]).length
      || /^(?:#{1,6}\s*)?(?:游戏过程中|扮演要求|玩法说明|游戏须知|角色扮演须知)/.test(item.text.trim()))) continue;
    const privateMaterial = ref.collection === 'memory' || (ref.collection === 'knowledge' ? view.private_knowledge : view.private_evidence).some(m => m.id === ref.id);
    const tasks = ref.collection !== 'memory' && privateMaterial ? extractTaskSections([item]) : [];
    if (tasks.length) groups[0].items.push(...tasks.map(task => ({ ...materialItem(ref, task.text, '个人任务 · 原文摘记'), id: task.id })));
    const investigation = phase.investigations.find(i => i.materials.some(m => m.collection === ref.collection && m.id === ref.id));
    const kind = item.kind === 'CLAIM' ? '材料中的说法' : item.kind === 'INFERENCE' ? '材料中的推测' : '原文摘记';
    const label = 'title' in item ? item.title : investigation ? `${investigation.label} · ${kind}` : `${privateMaterial ? '私人资料' : '公开资料'} · ${kind}`;
    if (tasks.length && !investigation && ref.collection === 'knowledge') continue;
    groups[ref.collection === 'memory' ? 2 : investigation || ref.collection === 'evidence' ? 1 : 3].items.push(materialItem(ref, item.text, label));
  }
  const records = [...(view.discussion?.entries || []), ...(view.role_responses?.entries || []), ...(view.investigation_proposals?.entries || []), ...(view.full_game?.private_discussion || [])]
    .filter(item => !hiddenMemorySequences.includes(item.sequence) && item.phase_id === phaseId && ('audience' in item
      ? Array.isArray(item.audience) && item.audience.includes(view.selected_character_id) && phase.private_message_ids.includes(item.id)
      : phase.statement_ids.includes(item.id)))
    .sort((a, b) => a.sequence - b.sequence);
  for (const record of records) groups[record.speaker === view.selected_character_id ? 5 : 4].items.push({
    id: record.id, record: record.id,
    label: `${name(record.speaker)} · ${'audience' in record ? '双方私聊' : '公开发言'}`,
    excerpt: summaryExcerpt(record.text),
  });
  // Bring the player's own story ahead of public cover introductions. The
  // complete material index stays available separately; this is reading order.
  groups[3].items.sort((a, b) => Number(b.label.startsWith('私人资料')) - Number(a.label.startsWith('私人资料')));
  return groups.filter(group => group.items.length);
}
