import type { PackagePlay } from '@/types/packagePlay';

export function validInteractionCount(value: PackagePlay['ai_interactions']): boolean {
  return value === undefined || Boolean(value && value.schema_version === 'package-ai-interactions/1.0'
    && Number.isSafeInteger(value.initiated) && value.initiated >= 0
    && Number.isSafeInteger(value.limit) && value.limit > 0 && value.initiated <= value.limit);
}

/** Explain the actual server gate, without guessing from hidden monetary limits. */
export function proposalAvailability(view: PackagePlay, character: string): string {
  const proposal = view.investigation_proposals;
  if (!proposal) return '';
  if (view.settled) return '本局已结束，已有调查建议仍可阅读。';
  if (view.full_game?.phase_kind === 'READING') return '现在是阅读材料的步骤。读完后点击顶栏右上角的“进入下一阶段”，进入共同调查后可征求建议。';
  if (view.full_game?.phase_kind === 'FINALE') return '现在是终局答卷步骤，调查已经结束。';
  if (view.pending_ai) return '正在等待上一轮互动结果，完成后再征求建议。';
  if (proposal.reason === 'NO_OPTIONS') return '当前没有双方都能调查的目标。请查看本轮调查进度、已解锁地点和剩余行动点。';
  if (proposal.reason === 'QUESTION_LIMIT') return '本局已达到 AI 互动轮数上限，已有资料和建议仍可阅读。';
  if (!proposal.available) return '当前暂时无法征求建议，请刷新试玩核对；已有资料和建议仍可阅读。';
  if (!character) return '请选择一位角色，再点击“征求调查建议”。';
  if (!proposal.character_ids.includes(character)) return '这位角色目前没有与你共同可调查的目标，请选择其他角色。';
  return view.guided_play ? '可以征求这位角色的建议；建议会保存在下方，搜证地点由你选择。'
    : '可以征求这位角色的建议；建议会保存在下方，搜证仍由你们共同决定。';
}
