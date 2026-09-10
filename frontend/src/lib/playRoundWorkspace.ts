import type { PackagePlay, RoundWorkspace } from '@/types/packagePlay';

const stable = (id: unknown): id is string => typeof id === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$/.test(id);
const text = (value: unknown, limit: number): value is string => typeof value === 'string' && Boolean(value.trim()) && Array.from(value).length <= limit;
export function roundMaterial(view: PackagePlay, reference: RoundWorkspace['phases'][number]['materials'][number]) {
  if (reference.collection === 'memory') return view.memories?.entries.find(item => item.id === reference.id);
  if (reference.collection !== 'knowledge' && reference.collection !== 'evidence') return undefined;
  return [...view[`public_${reference.collection}`], ...view[`private_${reference.collection}`]].find(item => item.id === reference.id);
}
export function validRoundWorkspace(view: PackagePlay): boolean {
  const work = view.round_workspace;
  if (work === undefined) return true;
  if (!work || !view.full_game || work.schema_version !== 'package-round-workspace/1.0' || !Array.isArray(work.phases)
    || !work.phases.length || work.phases.length > 100 || new Set(work.phases.map(p => p?.phase_id)).size !== work.phases.length
    || work.phases.at(-1)?.phase_id !== view.current_phase.id) return false;
  const seq = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) >= 0 && Number(value) <= view.revision;
  const materialIds = new Set<string>(); const publicIds = new Set<string>(); const privateIds = new Set<string>();
  const statements = [...(view.discussion?.entries || []), ...(view.role_responses?.entries || []), ...(view.investigation_proposals?.entries || [])];
  return work.phases.every(phase => {
    if (!phase || !stable(phase.phase_id) || !text(phase.title, 200) || !['READING', 'INVESTIGATION', 'FINALE'].includes(phase.kind)
      || !Array.isArray(phase.materials) || phase.materials.length > 2000 || !Array.isArray(phase.investigations) || phase.investigations.length > 1000
      || !Array.isArray(phase.statement_ids) || phase.statement_ids.length > 1000 || !Array.isArray(phase.private_message_ids) || phase.private_message_ids.length > 1000) return false;
    return phase.materials.every(ref => {
      const key = `${ref?.collection}:${ref?.id}`;
      if (!ref || !stable(ref.id) || !seq(ref.sequence) || materialIds.has(key) || !roundMaterial(view, ref)) return false;
      materialIds.add(key); return true;
    }) && phase.investigations.every(item => item && stable(item.action_id) && text(item.label, 200) && seq(item.sequence)
      && Number.isSafeInteger(item.cost) && item.cost >= 0 && ['PLAYER', 'AUTO', 'LEGACY'].includes(item.mode)
      && view.characters.some(actor => actor.id === item.character_id) && Array.isArray(item.materials)
      && item.materials.length <= 1000 && item.materials.every(ref => ref && ['knowledge', 'evidence'].includes(ref.collection)
        && phase.materials.some(material => material.collection === ref.collection && material.id === ref.id)))
      && phase.statement_ids.every(id => {
        if (!stable(id) || publicIds.has(id) || !statements.some(item => item.id === id && item.phase_id === phase.phase_id)) return false;
        publicIds.add(id); return true;
      }) && phase.private_message_ids.every(id => {
        if (!stable(id) || privateIds.has(id) || !view.full_game?.private_discussion.some(item => item.id === id && item.phase_id === phase.phase_id && item.audience.includes(view.selected_character_id))) return false;
        privateIds.add(id); return true;
      });
  });
}
