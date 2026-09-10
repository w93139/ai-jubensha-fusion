import type { GuidedAction, GuidedRequest, PackagePlay } from '@/types/packagePlay';

const stable = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$/.test(value);
export function validPostGameQa(view: PackagePlay): boolean {
  const qa = view.post_game_qa;
  if (qa === undefined) return true;
  return Boolean(view.settled && view.status === 'SETTLED' && view.settlement && view.full_game?.result
    && qa && qa.schema_version === 'package-post-game-qa/1.0' && Array.isArray(qa.questions) && qa.questions.length <= 100
    && new Set(qa.questions.map(q => q?.id)).size === qa.questions.length
    && qa.questions.every(q => q && stable(q.id) && typeof q.title === 'string' && q.title.trim() && q.title.length <= 200
      && typeof q.text === 'string' && q.text.trim() && Array.from(q.text).length <= 40000));
}
export function validGuidedPlay(view: PackagePlay): boolean {
  if (!validPostGameQa(view)) return false;
  const guided = view.guided_play;
  if (guided !== undefined && (!guided || !view.full_game || guided.schema_version !== 'package-guided-play/1.0'
    || !['available', 'can_investigate', 'can_finish_investigation', 'has_legacy_ballot', 'can_present_required'].every(key =>
      typeof guided[key as keyof typeof guided] === 'boolean'))) return false;
  if (guided?.can_investigate_round !== undefined && typeof guided.can_investigate_round !== 'boolean') return false;
  const last = guided?.last_command;
  if (last !== undefined && last !== null && (!stable(last.idempotency_key)
    || !['INVESTIGATE', 'INVESTIGATE_ROUND', 'FINISH_INVESTIGATION', 'PRESENT_REQUIRED', 'REQUEST_HINT'].includes(last.action)
    || !Number.isSafeInteger(last.sequence) || last.sequence < 1 || last.sequence > view.revision)) return false;
  const hints = view.host_hints;
  if (hints === undefined) return true;
  if (!guided || !hints || hints.schema_version !== 'package-host-hints/1.0' || !Array.isArray(hints.topics) || hints.topics.length > 100
    || !Array.isArray(hints.entries) || hints.entries.length > 300
    || new Set(hints.topics.map(topic => topic?.id)).size !== hints.topics.length
    || !hints.topics.every(topic => topic && stable(topic.id) && stable(topic.phase_id) && topic.max_level === 3
      && typeof topic.title === 'string' && Boolean(topic.title.trim()) && Array.from(topic.title).length <= 200)) return false;
  let previous = 0;
  return new Set(hints.entries.map(entry => `${entry?.topic_id}:${entry?.level}`)).size === hints.entries.length
    && hints.entries.every(entry => {
      if (!entry || ![1, 2, 3].includes(entry.level) || !Number.isSafeInteger(entry.sequence)
        || entry.sequence <= previous || entry.sequence > view.revision
        || typeof entry.text !== 'string' || !entry.text.trim() || Array.from(entry.text).length > 40_000
        || typeof entry.title !== 'string' || !entry.title.trim() || Array.from(entry.title).length > 200
        || !stable(entry.topic_id) || !stable(entry.phase_id)
        || hints.topics.some(topic => topic.id === entry.topic_id && (topic.phase_id !== entry.phase_id || topic.title !== entry.title))) return false;
      previous = entry.sequence; return true;
    });
}

export function canGuide(view: PackagePlay, request: GuidedAction): boolean {
  if (!validGuidedPlay(view) || !view.guided_play?.available || view.pending_ai || view.settled) return false;
  if (request.action === 'INVESTIGATE_ROUND') {
    const ids = request.payload.action_ids;
    const actions = view.mechanics?.available_actions || [];
    return guidedRoundAvailable(view) && Array.isArray(ids) && ids.length <= actions.length
      && new Set(ids).size === ids.length && ids.every(id => actions.some(action => action.id === id))
      && ids.reduce((sum, id) => sum + actions.find(action => action.id === id)!.cost, 0) <= (view.mechanics?.remaining_points ?? 0);
  }
  if (request.action === 'INVESTIGATE') return view.guided_play.can_investigate
    && Boolean(view.mechanics?.available_actions.some(action => action.id === request.payload.action_id));
  if (request.action === 'FINISH_INVESTIGATION') return view.guided_play.can_finish_investigation;
  if (request.action === 'PRESENT_REQUIRED') return view.guided_play.can_present_required;
  if (request.action !== 'REQUEST_HINT') return false;
  const topic = view.host_hints?.topics.find(value => value.id === request.payload.topic_id && value.phase_id === view.current_phase.id);
  if (!topic) return false;
  return [1, 2, 3].includes(request.payload.level) && request.payload.level <= topic.max_level
    && !view.host_hints!.entries.some(entry => entry.topic_id === topic.id && entry.level === request.payload.level);
}

export function confirmsGuided(view: PackagePlay, request: GuidedRequest): boolean {
  const last = view.guided_play?.last_command;
  return Boolean(last?.idempotency_key === request.idempotency_key && last.action === request.action
    && last.sequence > request.expected_revision) || Boolean(request.action === 'REQUEST_HINT'
    && view.host_hints?.entries.some(entry => entry.topic_id === request.payload.topic_id && entry.level === request.payload.level));
}

export function guidedRoundAvailable(view: PackagePlay): boolean {
  return Boolean(view.guided_play?.can_investigate_round && view.full_game?.phase_kind === 'INVESTIGATION');
}
