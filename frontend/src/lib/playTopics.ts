import type { PackagePlay, TopicAction, TopicRequest, TopicTurn } from '@/types/packagePlay';

const stable = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$/.test(value);
const text = (value: unknown, maximum: number, empty = false): value is string => typeof value === 'string'
  && (empty || Boolean(value.trim())) && Array.from(value).length <= maximum;
const unique = <T,>(items: T[], key: (item: T) => string) => new Set(items.map(key)).size === items.length;

export function validSinglePlayer(view: PackagePlay): boolean {
  const single = view.single_player;
  if (single === undefined) return true;
  if (!single || !view.full_game || !view.guided_play || single.schema_version !== 'package-single-player/1.0'
    || typeof single.available !== 'boolean' || !text(single.operation_rules, 40_000, !single.available)
    || !Array.isArray(single.topics) || single.topics.length > 100 || !Array.isArray(single.turns) || single.turns.length > 2000) return false;
  const stage = single.stage;
  if (stage === null ? single.available : !stage || stage.phase_id !== view.current_phase.id || !text(stage.goal, 2000)
    || !Array.isArray(stage.instructions) || stage.instructions.length > 30 || !stage.instructions.every(item => text(item, 4000))
    || !text(stage.completion, 4000)) return false;
  const other = (id: string) => id !== view.selected_character_id && view.characters.some(character => character.id === id);
  if (!unique(single.topics, topic => topic?.id)
    || !single.topics.every(topic => topic && stable(topic.id) && text(topic.title, 200) && Array.isArray(topic.responders)
      && topic.responders.length <= 20 && unique(topic.responders, responder => responder?.character_id)
      && topic.responders.every(responder => responder && other(responder.character_id) && Array.isArray(responder.channels)
        && responder.channels.length > 0 && responder.channels.length <= 2 && unique(responder.channels, channel => channel)
        && responder.channels.every(channel => ['PUBLIC', 'PRIVATE'].includes(channel)) && Array.isArray(responder.intents)
        && responder.intents.length <= 20 && unique(responder.intents, intent => intent?.id)
        && responder.intents.every(intent => intent && stable(intent.id) && text(intent.label, 200) && text(intent.question, 1000)
          && typeof intent.available === 'boolean')))) return false;
  const last = single.last_command;
  if (last !== null && (!last || !stable(last.idempotency_key) || !['ASK_TOPIC', 'USE_FALLBACK'].includes(last.action)
    || !Number.isSafeInteger(last.sequence) || last.sequence < 1 || last.sequence > view.revision)) return false;
  return unique(single.turns, turn => turn?.id) && unique(single.turns, turn => turn?.reply_to)
    && unique(single.turns.filter(turn => turn?.reply_request), turn => turn.reply_request!.idempotency_key) && single.turns.every(turn => {
    if (!turn || !stable(turn.id) || !stable(turn.topic_id) || !stable(turn.intent_id) || !stable(turn.phase_id)
      || !other(turn.character_id) || !text(turn.title, 200) || !text(turn.question, 1000) || !stable(turn.reply_to)
      || !['PUBLIC', 'PRIVATE'].includes(turn.channel) || !['READY', 'PENDING', 'OK', 'FAILED', 'FALLBACK'].includes(turn.status)
      || typeof turn.can_fallback !== 'boolean' || (turn.status === 'PENDING' && turn.can_fallback)
      || (turn.receipt_status !== undefined && !['OK', 'INVALID', 'UNKNOWN', 'STALE', 'EXPIRED', 'PENDING'].includes(turn.receipt_status)) || (turn.answer !== undefined && !text(turn.answer, 40_000))) return false;
    const message = turn.channel === 'PUBLIC' ? view.discussion?.entries.find(entry => entry.id === turn.reply_to)
      : view.full_game?.private_discussion.find(entry => entry.id === turn.reply_to && entry.audience.includes(turn.character_id));
    if (!message || message.phase_id !== turn.phase_id || message.speaker !== view.selected_character_id || message.text !== turn.question) return false;
    const request = turn.reply_request;
    if (request === null) return turn.status !== 'PENDING' && turn.status !== 'OK';
    const receipt = (turn.channel === 'PUBLIC' ? view.role_responses?.requests : view.private_replies?.requests)
      ?.find(item => item.request_id === request.idempotency_key);
    if (turn.answer !== undefined && !['OK', 'FALLBACK'].includes(turn.status)) return false;
    if (receipt && (receipt.character_id !== turn.character_id || receipt.reply_to !== turn.reply_to || receipt.revision !== request.expected_revision)) return false;
    if ((turn.status === 'READY' && (receipt || request.expected_revision !== view.revision || turn.phase_id !== view.current_phase.id)) || (turn.status === 'PENDING' && receipt?.status !== 'PENDING')
      || (turn.status === 'OK' && receipt?.status !== 'OK')
      || (turn.status === 'FAILED' && (!receipt || ['OK', 'PENDING'].includes(receipt.status)))
      || (turn.receipt_status !== undefined && turn.receipt_status !== receipt?.status)) return false;
    return Boolean(request && stable(request.idempotency_key) && Number.isSafeInteger(request.expected_revision)
      && request.expected_revision >= message.sequence && request.expected_revision <= view.revision
      && request.character_id === turn.character_id && request.reply_to === turn.reply_to
      && (turn.channel === 'PUBLIC' ? request.schema_version === 'package-dialogue-command/1.0' && request.action === 'RESPOND'
        : request.schema_version === 'package-private-dialogue-command/1.0' && request.action === 'RESPOND_PRIVATE'));
  });
}

export function canTopic(view: PackagePlay, action: TopicAction): boolean {
  const single = view.single_player;
  if (!single || !validSinglePlayer(view) || view.pending_ai || view.settled) return false;
  if (action.action === 'USE_FALLBACK') return single.turns.some(turn => turn.id === action.payload.turn_id && turn.can_fallback);
  if (!single.available) return false;
  const { topic_id, character_id, intent_id, channel } = action.payload;
  if (channel === 'PRIVATE' && !view.full_game?.call?.character_ids.includes(character_id)) return false;
  return Boolean(single.topics.find(topic => topic.id === topic_id)?.responders.some(responder => responder.character_id === character_id
    && responder.channels.includes(channel) && responder.intents.some(intent => intent.id === intent_id && intent.available)));
}

export function confirmsTopic(view: PackagePlay, request: TopicRequest): boolean {
  const receipt = view.single_player?.last_command;
  return Boolean(receipt && receipt.idempotency_key === request.idempotency_key && receipt.action === request.action
    && receipt.sequence > request.expected_revision);
}

export function topicForRequest(view: PackagePlay, key: string): TopicTurn | undefined {
  return view.single_player?.turns.find(turn => turn.reply_request?.idempotency_key === key);
}

export function sameTopicReply(left: TopicTurn['reply_request'], right: TopicTurn['reply_request']): boolean {
  return Boolean(left && right && left.schema_version === right.schema_version && left.action === right.action
    && left.idempotency_key === right.idempotency_key && left.expected_revision === right.expected_revision
    && left.character_id === right.character_id && left.reply_to === right.reply_to);
}
