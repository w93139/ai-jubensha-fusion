import PlayEndingPanel from './PlayEndingPanel';
import { phaseThinkingTime } from '@/lib/playPresentation';
import PlayImageView from './PlayImageView';
import { playImageRotation } from '@/lib/playImageOrientation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { Home } from 'lucide-react';
import { useAuthStore } from '@/stores/authStore';
import PlayText from './PlayText';
import PlayClueDeck from './PlayClueDeck';
import PlayPerformanceSummary from './PlayPerformanceSummary';
import { performanceReadingBlocks } from '@/lib/playPerformance';
import PlaySelect from './PlaySelect';
import PlayNotebook from './PlayNotebook';
import PlayClueCollection from './PlayClueCollection';
import type { AvailableClue } from '@/lib/playClueCollection';
import PlayReferencePanel from './PlayReferencePanel';
import PlayVoiceInput from './PlayVoiceInput';
import PlayGuidedStage from './PlayGuidedStage';
import PlayHostHints from './PlayHostHints';
import PlayRoundWorkspace from './PlayRoundWorkspace';
import PlayPhaseAdvance from './PlayPhaseAdvance';
import { validRoundWorkspace } from '@/lib/playRoundWorkspace';
import PlayTopicExchange from './PlayTopicExchange';
import { latestHumanStatement, questionClarification, questionRefusal } from '@/lib/playQuestionGuide';
import { canTopic, confirmsTopic, topicForRequest, validSinglePlayer, sameTopicReply } from '@/lib/playTopics';
import type { TopicAction, TopicRequest, TopicTurn } from '@/types/packagePlay';
import { canGuide, confirmsGuided, validGuidedPlay } from '@/lib/playGuidance';
import type { GuidedAction, GuidedRequest } from '@/types/packagePlay';
import { usePlaySpeech } from './PlaySpeech';
import { proposalAvailability, validInteractionCount } from '../lib/playInteraction';
import packagePlayService, { PackagePlayError, preparePackagePlayAttempt, watchPackagePlayAuth } from '@/services/packagePlayService';
import type { PackagePlayAttempt } from '@/services/packagePlayService';
import FullGamePanel, { validFullGame } from './FullGamePanel';
import type { FullTableAction, FullTableRequest, FullDecisionAction, FullDecisionRequest, FullPrivateReplyRequest, FullPhoneRequest, FullPhonePauseRequest } from '@/types/packagePlay';
import type { PlayMaterial, PlayMaterialCollection, PackagePlay, PackagePlayAction, PackagePlayAskRequest, PackagePlayMechanics, PackagePlaySpeakRequest, PackagePlayProposalRequest, PackagePlayRespondRequest } from '@/types/packagePlay';

export function validPlayVisuals(view: PackagePlay): boolean {
  if (view.visuals === undefined) return true;
  if (!view.full_game || !Array.isArray(view.visuals) || view.visuals.length > 1000
    || new Set(view.visuals.map(v => v?.id)).size !== view.visuals.length) return false;
  const stable = (id: unknown) => typeof id === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$/.test(id);
  return view.visuals.every(v => v && stable(v.id) && stable(v.material_id)
    && typeof v.label === 'string' && v.label.trim() && Array.from(v.label).length <= 100
    && (v.collection === 'memory' ? view.memories?.entries.some(m => m.id === v.material_id)
      : ['knowledge', 'evidence'].includes(v.collection)
        && [...view[`public_${v.collection}`], ...view[`private_${v.collection}`]].some(m => m.id === v.material_id)));
}

export function PlayImage({ playId, visualId, label }: { playId: string; visualId: string; label: string }) {
  const [url, setUrl] = useState('');
  const [authInvalid, setAuthInvalid] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [rotation, setRotation] = useState(0);
  const [zoom, setZoom] = useState(1);
  const container = useRef<HTMLDivElement>(null);
  const current = useRef<{ controller?: AbortController; url?: string; generation: number;
    auth?: ReturnType<typeof watchPackagePlayAuth> }>({ generation: 0 });
  useEffect(() => {
    const state = current.current;
    const clear = () => {
      state.generation++; state.controller?.abort(); state.controller = undefined;
      if (state.url) URL.revokeObjectURL(state.url); state.url = undefined;
    };
    state.auth = watchPackagePlayAuth(() => { clear(); setUrl(''); setAuthInvalid(true); setLoading(false); setError('登录身份已变化，请重新打开试玩。'); });
    return () => { state.auth?.dispose(); clear(); };
  }, []);
  const open = useCallback(async () => {
    if (!current.current.auth?.isCurrent() || current.current.controller || url) return;
    const state = current.current;
    const generation = state.generation;
    const controller = new AbortController(); state.controller = controller;
    setLoading(true); setError('');
    try {
      const blob = await packagePlayService.image(playId, visualId, controller.signal);
      if (!state.auth?.isCurrent() || generation !== state.generation || controller.signal.aborted) return;
      const orientation = await playImageRotation(blob);
      if (!state.auth?.isCurrent() || generation !== state.generation || controller.signal.aborted) return;
      const next = URL.createObjectURL(blob); state.url = next; setRotation(orientation); setUrl(next);
    } catch {
      if (generation === state.generation && !controller.signal.aborted) setError('原图读取失败，请重试。');
    } finally {
      if (generation === state.generation) { state.controller = undefined; setLoading(false); }
    }
  }, [playId, visualId, url]);
  useEffect(() => {
    if (!container.current || url || authInvalid || error || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { observer.disconnect(); void open(); }
    }, { rootMargin: '160px' });
    observer.observe(container.current);
    return () => observer.disconnect();
  }, [open, url, authInvalid, error]);
  return <div ref={container} className="mt-3 min-w-0" aria-label={label}>
    {!url && <button type="button" disabled={loading || authInvalid} onClick={() => { void open(); }} className="min-h-11 rounded border border-brass px-3 py-2 text-sm text-brass disabled:opacity-50">{loading ? '正在读取原图…' : `查看${label}`}</button>}
    {url && <PlayImageView url={url} label={label} rotation={rotation} onRotate={() => setRotation(value => (value + 90) % 360)} zoom={zoom} onZoom={() => setZoom(value => value === 1 ? 2 : 1)} />}
    {error && <p role="alert" className="mt-2 text-sm text-mist">{error}</p>}
  </div>;
}

export function validPlayResponses(view: PackagePlay): boolean {
  const value = view.role_responses;
  const stable = (id: unknown) => typeof id === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$/.test(id);
  const other = (id: string) => id !== view.selected_character_id && view.characters.some(role => role.id === id);
  const statement = (id: string) => view.discussion?.entries.find(item => item.id === id);
  if (!value || value.schema_version !== 'package-dialogue-view/1.0' || typeof value.available !== 'boolean'
    || !(value.reason === null || typeof value.reason === 'string')
    || !Array.isArray(value.character_ids) || new Set(value.character_ids).size !== value.character_ids.length
    || !value.character_ids.every(other) || !Array.isArray(value.reply_target_ids)
    || new Set(value.reply_target_ids).size !== value.reply_target_ids.length
    || !value.reply_target_ids.every(id => statement(id)?.phase_id === view.current_phase.id)
    || !Array.isArray(value.requests) || value.requests.length > (view.full_game ? 400 : 30)
    || new Set(value.requests.map(item => item?.request_id)).size !== value.requests.length
    || !value.requests.every(item => item && stable(item.request_id) && other(item.character_id)
      && Number.isSafeInteger(item.revision) && item.revision >= 1 && item.revision < view.revision
      && statement(item.reply_to) && statement(item.reply_to)!.sequence <= item.revision
      && ['PENDING','OK','INVALID','UNKNOWN','STALE','EXPIRED'].includes(item.status))
    || !Array.isArray(value.entries) || value.entries.length > (view.full_game ? 400 : 30)) return false;
  let previous = 0;
  return value.entries.every(item => {
    if (!item || Object.keys(item).length !== 8 || !Number.isSafeInteger(item.sequence)
      || item.sequence <= previous || item.sequence > view.revision || item.id !== `response-${item.sequence}`
      || !other(item.speaker) || item.kind !== 'CLAIM' || !stable(item.phase_id)
      || typeof item.text !== 'string' || !item.text.trim() || Array.from(item.text).length > 602
      || statement(item.reply_to)?.phase_id !== item.phase_id || statement(item.reply_to)!.sequence >= item.sequence
      || !Array.isArray(item.modes) || item.modes.length < 1 || item.modes.length > 3
      || !item.modes.every(mode => ['REPORT','INFERENCE','QUESTION','UNCERTAIN'].includes(mode))
      || !value.requests.some(r => r.status === 'OK' && r.character_id === item.speaker
        && r.reply_to === item.reply_to && r.revision + 2 === item.sequence)) return false;
    previous = item.sequence; return true;
  });
}

export function canRequestResponse(view: PackagePlay, character: string, target: string, checkingPrevious = false): boolean {
  return validPlayResponses(view) && (checkingPrevious ? character !== view.selected_character_id && view.characters.some(role => role.id === character) : view.role_responses!.character_ids.includes(character))
    && Boolean(view.discussion?.entries.some(item => item.id === target))
    && (checkingPrevious || (!view.single_player && !view.settled && view.role_responses!.available && !view.pending_ai
      && view.role_responses!.reply_target_ids.includes(target)));
}

export function validPlayProposals(view: PackagePlay): boolean {
  const value = view.investigation_proposals;
  if (!value || value.schema_version !== 'package-proposal-view/1.0' || typeof value.available !== 'boolean'
    || !Array.isArray(value.character_ids) || !Array.isArray(value.entries) || value.entries.length > (view.full_game ? 400 : 30)
    || !Array.isArray(value.requests) || value.requests.length > (view.full_game ? 400 : 30)
    || new Set(value.requests.map(item => item?.request_id)).size !== value.requests.length
    || !value.requests.every(item => item && typeof item.request_id === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$/.test(item.request_id)
      && Number.isSafeInteger(item.revision) && item.revision >= 0 && item.revision < view.revision
      && item.character_id !== view.selected_character_id && view.characters.some(role => role.id === item.character_id)
      && ['PENDING', 'OK', 'INVALID', 'UNKNOWN', 'STALE', 'EXPIRED'].includes(item.status))
    || new Set(value.character_ids).size !== value.character_ids.length
    || !value.character_ids.every(id => id !== view.selected_character_id && view.characters.some(item => item.id === id))) return false;
  let previous = 0;
  return value.entries.every(item => {
    if (!item || !Number.isSafeInteger(item.sequence) || item.sequence <= previous || item.sequence > view.revision
      || item.id !== `proposal-${item.sequence}` || item.kind !== 'CLAIM' || item.speaker === view.selected_character_id
      || !view.characters.some(role => role.id === item.speaker) || typeof item.text !== 'string' || !item.text.trim()
      || typeof item.phase_id !== 'string' || !item.action || typeof item.action.id !== 'string'
      || typeof item.action.label !== 'string' || !item.action.label.trim() || !Number.isSafeInteger(item.action.cost) || item.action.cost < 0
      || !Array.isArray(item.basis) || item.basis.length > 3
      || !item.basis.every(ref => ref && ['knowledge', 'evidence', 'discussion'].includes(ref.collection)
        && typeof ref.id === 'string' && typeof ref.text === 'string' && ['FACT', 'CLAIM', 'INFERENCE'].includes(ref.kind)
        && (ref.collection !== 'discussion' || (ref.kind === 'CLAIM' && view.characters.some(role => role.id === ref.speaker))))) return false;
    previous = item.sequence; return true;
  });
}

export function canRequestProposal(view: PackagePlay, character: string, checkingPrevious = false): boolean {
  return validPlayProposals(view) && character !== view.selected_character_id && view.characters.some(item => item.id === character)
    && (checkingPrevious || (!view.single_player && !view.settled && view.investigation_proposals!.available && !view.pending_ai
      && view.investigation_proposals!.character_ids.includes(character)));
}

export function validPlayDiscussion(view: PackagePlay): boolean {
  const value = view.discussion;
  if (!value || value.schema_version !== 'package-discussion-view/1.0' || value.limit !== (view.full_game ? 600 : 100)
    || !Array.isArray(value.entries) || value.entries.length > value.limit) return false;
  let previous = 0;
  return value.entries.every(item => {
    if (!item || !Number.isSafeInteger(item.sequence) || item.sequence <= previous || item.sequence > view.revision
      || item.id !== `statement-${item.sequence}` || item.kind !== 'CLAIM'
      || !(item.speaker === view.selected_character_id || (view.guided_play && view.characters.some(role => role.id === item.speaker))) || typeof item.phase_id !== 'string'
      || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$/.test(item.phase_id)
      || typeof item.text !== 'string' || !item.text.trim() || Array.from(item.text).length > (item.speaker !== view.selected_character_id && view.guided_play ? 1500 : 1000)) return false;
    previous = item.sequence;
    return true;
  });
}

export function canSpeakInPlay(view: PackagePlay, text: string, checkingPrevious = false): boolean {
  return validPlayDiscussion(view) && (checkingPrevious || (!view.settled && view.status === 'TEXT_PLAY'
    && view.full_game?.phase_kind !== 'FINALE' && view.discussion!.entries.length < view.discussion!.limit))
    && text.trim().length > 0 && Array.from(text.trim()).length <= 1000;
}

export function validPlayMemories(view: PackagePlay): boolean {
  const value = view.memories;
  const stable = (id: unknown) => typeof id === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$/.test(id);
  if (!value || value.schema_version !== 'package-memory-view/1.0' || !Array.isArray(value.entries)
    || value.entries.length > 1000 || new Set(value.entries.map(item => item?.id)).size !== value.entries.length) return false;
  let previous = 0;
  return value.entries.every(item => {
    if (!item || !stable(item.id) || item.character_id !== view.selected_character_id
      || !Number.isSafeInteger(item.sequence) || item.sequence < previous || item.sequence > view.revision
      || !stable(item.phase_id) || typeof item.title !== 'string' || !item.title.trim() || Array.from(item.title).length > 100
      || typeof item.text !== 'string' || !item.text.trim() || Array.from(item.text).length > 40000
      || !['FACT', 'CLAIM', 'INFERENCE'].includes(item.kind) || item.card_disclosure !== 'KEEP_PRIVATE'
      || !['MAY_RETELL', 'MUST_RETELL'].includes(item.retelling) || !item.cause) return false;
    previous = item.sequence;
    if (item.cause.kind === 'OTHER_HEARD_SPEECH') {
      const cause = item.cause;
      const messages = cause.channel === 'PRIVATE' ? view.full_game?.private_discussion || []
        : [...(view.discussion?.entries || []), ...(view.investigation_proposals?.entries || []), ...(view.role_responses?.entries || [])];
      return Boolean(view.full_game && Object.keys(cause).length === 3 && ['PUBLIC', 'PRIVATE'].includes(cause.channel)
        && cause.speaker !== view.selected_character_id && view.characters.some(c => c.id === cause.speaker)
        && messages.some(m => m.sequence === item.sequence && m.speaker === cause.speaker && m.phase_id === item.phase_id));
    }
    if (item.cause.kind === 'OTHER_PUBLIC_SPEECH') {
      const speaker = item.cause.speaker;
      return Object.keys(item.cause).length === 2 && item.sequence > 0 && speaker !== view.selected_character_id
        && view.characters.some(role => role.id === speaker)
        && [...(view.discussion?.entries || []), ...(view.investigation_proposals?.entries || []), ...(view.role_responses?.entries || [])]
          .some(entry => entry.sequence === item.sequence && entry.speaker === speaker && entry.phase_id === item.phase_id);
    }
    const cause = item.cause;
    return cause.kind === 'ACQUIRED_EVIDENCE' && Object.keys(cause).length === 2
      && stable(cause.evidence_id)
      && [...view.public_evidence, ...view.private_evidence].some(evidence => evidence.id === cause.evidence_id);
  });
}

export function matchesPlayRoute(view: PackagePlay, playId: string, openingSessionId: string): boolean {
  return Boolean((playId || openingSessionId) && /^play-[0-9a-f]{32}$/.test(view.play_id)
    && (!playId || view.play_id === playId) && (!openingSessionId || view.opening_session_id === openingSessionId)
    && ((view.status === 'TEXT_PLAY' && view.settled === false) || (view.status === 'SETTLED' && view.settled === true))
    && view.runtime_ready === false && Number.isSafeInteger(view.revision) && view.revision >= 0
    && (view.reading_supplements === undefined || (Array.isArray(view.reading_supplements) && view.reading_supplements.length <= 100
      && new Set(view.reading_supplements.map(item => item?.id)).size === view.reading_supplements.length
      && view.reading_supplements.every(item => item && Object.keys(item).sort().join(',') === 'id,text'
        && typeof item.id === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$/.test(item.id)
        && typeof item.text === 'string' && item.text.trim() && Array.from(item.text).length <= 40_000)))
    && (view.discussion === undefined || validPlayDiscussion(view))
    && (view.memories === undefined || validPlayMemories(view))
    && (view.investigation_proposals === undefined || validPlayProposals(view))
    && (view.role_responses === undefined || validPlayResponses(view)) && validInteractionCount(view.ai_interactions) && validFullGame(view) && validPlayVisuals(view) && validGuidedPlay(view) && validSinglePlayer(view) && validRoundWorkspace(view));
}

export function samePlayBinding(left: PackagePlay, right: PackagePlay): boolean {
  return left.play_id === right.play_id && left.opening_session_id === right.opening_session_id && left.release_id === right.release_id
    && left.version_id === right.version_id && left.package_hash === right.package_hash && left.selected_character_id === right.selected_character_id;
}

export function validPlayMechanics(value: unknown): value is PackagePlayMechanics {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const mechanics = value as PackagePlayMechanics;
  const points = [mechanics.initial_points, mechanics.spent_points, mechanics.remaining_points];
  if (!points.every(number => Number.isSafeInteger(number) && number >= 0)
    || mechanics.spent_points + mechanics.remaining_points !== mechanics.initial_points
    || typeof mechanics.can_finish_phase !== 'boolean' || !Array.isArray(mechanics.available_actions)) return false;
  const seen = new Set<string>();
  return mechanics.available_actions.every(item => {
    if (!item || typeof item !== 'object' || typeof item.id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$/.test(item.id)
      || typeof item.label !== 'string' || !item.label.trim() || !Number.isSafeInteger(item.cost) || item.cost < 0
      || item.cost > mechanics.remaining_points || seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
}

function canFinishPlayPhase(view: PackagePlay): boolean {
  return view.mechanics === undefined || (validPlayMechanics(view.mechanics) && view.mechanics.can_finish_phase);
}

export function canPerformPlayAction(view: PackagePlay, action: PackagePlayAction): boolean {
  if (view.settled || view.status !== 'TEXT_PLAY') return false;
  if (action.action === 'ADVANCE_PHASE') return view.can_advance && !view.phase_complete && canFinishPlayPhase(view);
  if (action.action === 'SETTLE') return view.phase_complete && canFinishPlayPhase(view);
  if (action.action === 'PERFORM_ACTION') {
    if (view.full_game) return false;
    if (!validPlayMechanics(view.mechanics) || !action.target || typeof action.target !== 'object' || Array.isArray(action.target)
      || Object.keys(action).length !== 2 || Object.keys(action.target).length !== 1
      || typeof action.target.action_id !== 'string') return false;
    return view.mechanics.available_actions.some(item => item.id === action.target.action_id);
  }
  if (view.full_game?.phase_kind === 'FINALE' || action.action !== 'SHARE_MATERIAL' || !action.target || (action.target.collection !== 'knowledge' && action.target.collection !== 'evidence')) return false;
  const publicItems = action.target.collection === 'knowledge' ? view.public_knowledge : view.public_evidence;
  const privateItems = action.target.collection === 'knowledge' ? view.private_knowledge : view.private_evidence;
  const observed = privateItems.find(item => item.id === action.target.id);
  return Boolean(observed?.can_share && (observed.disclosure === 'MAY_SHARE' || observed.disclosure === 'MUST_SHARE')
    && !publicItems.some(item => item.id === action.target.id));
}

export function canAskPackageCharacter(view: PackagePlay, characterId: string, question: string, checkingPrevious = false): boolean {
  if (view.single_player && !checkingPrevious) return false;
  return !view.settled && view.status === 'TEXT_PLAY' && (view.model.available || checkingPrevious)
    && characterId !== view.selected_character_id && view.characters.some(item => item.id === characterId)
    && question.trim().length > 0 && Array.from(question.trim()).length <= 1000;
}

const aiStatusLabels = {
  OK: '上次 AI 请求已保存。',
  INVALID: '上次回答未通过核验，没有加入可见记录。可以重新提出问题。',
  UNKNOWN: '上次请求结果未知，不会自动再次调用模型。可以重新提出问题。',
  STALE: '上次回答对应的阶段或材料已变化，没有加入可见记录。可以重新提出问题。',
  EXPIRED: '上次请求已过期，不会自动再次调用模型。可以重新提出问题。',
};

const disclosureLabels = { PUBLIC: '公开信息', MAY_SHARE: '允许自行分享', MUST_SHARE: '按规则需分享', KEEP_PRIVATE: '需保密' };
const kindLabels = { FACT: '已知事实', CLAIM: '角色说法', INFERENCE: '推测' };

export type PackagePlayPanelProps = {
  ownerId?: string | null;
  onTable?: (action: FullTableAction) => void | Promise<boolean>;
  onTopic?: (action: TopicAction) => void;
  onTopicReply?: (turnId: string) => void;
  onCheckTopic?: () => void;
  pendingTopicReplyKey?: string;
  onGuided?: (action: GuidedAction) => void;
  onCheckGuided?: () => void;
  onFinaleVotes?: (confirmedRetry?: boolean) => void;
  finaleVotesNeedsConfirmation?: boolean;
  finaleVotesProgress?: string;
  onDecide?: (actor: string, action: FullDecisionAction) => void;
  onPrivateReply?: (actor: string, target: string) => void;
  onPhone?: (action: 'STEP' | 'PAUSE') => void;
  playId: string;
  openingSessionId: string;
  view?: PackagePlay | null;
  loading: boolean;
  busy: boolean;
  requiresRefresh: boolean;
  error: string;
  notice: string;
  characterId: string;
  question: string;
  retryingQuestion: boolean;
  onCharacterChange: (id: string) => void;
  onQuestionChange: (question: string) => void;
  onAsk: () => void;
  onReload: () => void;
  onCreate: () => void;
  onAction: (action: PackagePlayAction) => void;
  statement?: string;
  retryingStatement?: boolean;
  onStatementChange?: (text: string) => void;
  onSpeak?: () => void;
  proposalCharacter?: string;
  retryingProposal?: boolean;
  onProposalCharacterChange?: (id: string) => void;
  onPropose?: () => void;
  responseCharacter?: string;
  responseTarget?: string;
  retryingResponse?: boolean;
  onResponseCharacterChange?: (id: string) => void;
  onResponseTargetChange?: (id: string) => void;
  onRespond?: () => void;
};

export default function PackagePlayPanel(props: PackagePlayPanelProps) {
  const view = props.view && matchesPlayRoute(props.view, props.playId, props.openingSessionId) ? props.view : undefined;
  const [composerOpen, setComposerOpen] = useState(false);
  const [composerMode, setComposerMode] = useState<'PUBLIC' | 'PRIVATE'>('PUBLIC');
  const [clip, setClip] = useState<{ id: string; title: string; text: string; scope: string }>();
  const [clueSelection, setClueSelection] = useState<{ id: string; scope: string }>();
  const clipScope = JSON.stringify([props.ownerId, view?.play_id, view?.selected_character_id]);
  type Page = 'reading' | 'investigation' | 'discussion' | 'private' | 'finale';
  const guided = Boolean(view?.guided_play);
  const naturalDialogue = Boolean(view?.full_game || view?.role_responses);
  const singlePlayer = Boolean(view?.single_player);
  const currentPage: Page = view?.full_game?.phase_kind === 'FINALE' ? 'finale' : view?.full_game?.phase_kind === 'INVESTIGATION' ? 'investigation' : 'reading';
  const pageScope = `${clipScope}:${view?.current_phase.id}`;
  const [navigation, setNavigation] = useState<{ scope: string; page: Page; history: Page[] }>({ scope: pageScope, page: currentPage, history: [] });
  const page = navigation.scope === pageScope ? navigation.page : currentPage;
  if (navigation.scope !== pageScope) setNavigation({ scope: pageScope, page: currentPage, history: [] });
  const showReading = !guided || page === 'reading';
  const showDiscussion = !guided || page === 'discussion';
  const showFinale = !guided || page === 'finale';
  const showPublicSpeech = showDiscussion || (guided && page === 'investigation');
  const publicEntries = view ? [...(view.discussion?.entries || []), ...(view.role_responses?.entries || [])].filter(entry => showDiscussion || (entry.speaker !== view.selected_character_id && entry.phase_id === view.current_phase.id)).sort((a, b) => a.sequence - b.sequence) : [];
  const speechEntries = useMemo(() => view ? [
    ...(view.discussion?.entries.filter(item => item.speaker !== view.selected_character_id) || []),
    ...(view.role_responses?.entries || []),
    ...(view.full_game?.private_discussion.filter(m => m.speaker !== view.selected_character_id && m.audience.includes(view.selected_character_id)) || []),
  ] : undefined, [view]);
  const speech = usePlaySpeech(clipScope, speechEntries);
  const visibleMemories = view?.memories?.entries || [];
  const notebookClip = useMemo(() => clip?.scope === clipScope ? { id: clip.id, title: clip.title, text: clip.text } : undefined, [clip, clipScope]);
  const selectedClue = useMemo(() => clueSelection?.scope === clipScope ? { id: clueSelection.id } : undefined, [clueSelection, clipScope]);
  const availableClues = useMemo<AvailableClue[]>(() => view ? (['knowledge', 'evidence'] as const).flatMap(collection =>
    [...view[`public_${collection}`], ...view[`private_${collection}`]].map((item, index) => {
      const visuals = view.visuals?.filter(visual => visual.collection === collection && visual.material_id === item.id)
        .map(visual => ({ id: visual.id, label: visual.label })) || [];
      return { collection, materialId: item.id, title: `${collection === 'knowledge' ? '阅读资料' : '调查线索'} · 第 ${index + 1} 段`, text: item.text, visualIds: visuals.map(visual => visual.id), visuals };
    })) : [], [view]);
  const pageRoot = useRef<HTMLElement>(null);
  const topbar = useRef<HTMLElement>(null);
  const overview = useRef<HTMLDetailsElement>(null);
  const hasView = Boolean(view);
  useEffect(() => {
    // An explicit clip from the page may open a tool whose trigger is in More.
    if ((selectedClue || notebookClip) && overview.current) overview.current.open = true;
  }, [selectedClue, notebookClip]);
  useEffect(() => {
    const root = pageRoot.current;
    const header = topbar.current;
    if (!root || !header) return;
    const measure = () => root.style.setProperty('--play-navigation-height', `${Math.ceil(header.getBoundingClientRect().height)}px`);
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(measure);
    observer?.observe(header);
    return () => { observer?.disconnect(); root.style.removeProperty('--play-navigation-height'); };
  }, [hasView]);
  useEffect(() => { if (guided) pageRoot.current?.scrollIntoView?.({ block: 'start', behavior: 'instant' }); }, [pageScope, guided]);
  const closeOverview = () => { if (overview.current) overview.current.open = false; };
  const goToPage = (next: Page, back = false) => {
    closeOverview();
    if (next === 'private') {
      if (guided && page === 'finale') setNavigation(previous => ({ scope: pageScope, page: 'discussion', history: [...previous.history, page].slice(-20) }));
      setComposerMode('PRIVATE'); setComposerOpen(true); if (composerBody.current) composerBody.current.scrollTop = 0; return;
    }
    if (!guided || !view) return;
    setNavigation(previous => ({ scope: pageScope, page: next, history: back ? previous.history.slice(0, -1) : previous.page === next ? previous.history : [...previous.history, previous.page].slice(-20) }));
    if (next === 'finale') setComposerOpen(false);
    pageRoot.current?.scrollIntoView?.({ block: 'start', behavior: 'instant' });
  };
  const navigate = (event: { preventDefault: () => void }, next: Page) => { if (guided) { event.preventDefault(); goToPage(next); } };
  const composerInput = useRef<HTMLTextAreaElement>(null);
  const composerBody = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (props.error && composerOpen && composerBody.current) composerBody.current.scrollTop = 0;
  }, [props.error, composerOpen]);
  const openComposer = () => { if (guided && (page === 'private' || page === 'finale')) goToPage('discussion'); closeOverview(); setComposerOpen(true); setTimeout(() => { if (composerMode === 'PRIVATE') (composerBody.current?.querySelector('[aria-label="通话对象"] input') as HTMLElement | null)?.focus({ preventScroll: true }); else if (singlePlayer) (composerBody.current?.querySelector('[aria-label="当前议题"]') as HTMLElement | null)?.focus({ preventScroll: true }); else composerInput.current?.focus({ preventScroll: true }); }, 0); };
  const [localScope, setLocalScope] = useState(clipScope);
  if (localScope !== clipScope) { setLocalScope(clipScope); setClip(undefined); setClueSelection(undefined); setComposerOpen(false); setComposerMode('PUBLIC'); }
  const locked = props.loading || props.busy || props.requiresRefresh;
  const readingActionsLocked = props.loading || props.requiresRefresh || !props.ownerId;
  const canCreate = !locked && props.view === null && !props.playId && Boolean(props.openingSessionId);
  const actor = view?.characters.find(item => item.id === view.selected_character_id);
  const canAsk = Boolean(view && !locked && canAskPackageCharacter(view, props.characterId, props.question, props.retryingQuestion));
  const canSpeak = Boolean(view && !locked && props.onSpeak && canSpeakInPlay(view, props.statement || '', props.retryingStatement));
  const canPropose = Boolean(view && !locked && props.onPropose && canRequestProposal(view, props.proposalCharacter || '', props.retryingProposal));
  const replyTarget = props.retryingResponse ? view?.discussion?.entries.find(entry => entry.id === props.responseTarget)
    : view ? latestHumanStatement(view) : undefined;
  const replyClarification = !props.retryingResponse && replyTarget ? questionRefusal(replyTarget.text) || questionClarification(replyTarget.text) : undefined;
  const canRespond = Boolean(view && !locked && props.onRespond && !replyClarification
    && (props.retryingResponse || !props.statement?.trim())
    && canRequestResponse(view, props.responseCharacter || '', replyTarget?.id || '', props.retryingResponse));
  const projectedMechanics = view?.mechanics;
  const mechanics = validPlayMechanics(projectedMechanics) ? projectedMechanics : undefined;
  const materials = (title: string, collection: PlayMaterialCollection, items: PlayMaterial[], isPublic: boolean) => {
    const contents = items.map(item => {
      const action: PackagePlayAction = { action: 'SHARE_MATERIAL', target: { collection, id: item.id } };
      const canShare = !isPublic && view && canPerformPlayAction(view, action);
      const sharedBy = item.shared_by_character_id ? view?.characters.find(character => character.id === item.shared_by_character_id)?.name : undefined;
      return <div key={item.id} className="min-w-0">
        <PlayText text={item.text} />
        {view?.visuals?.filter(v => v.collection === collection && v.material_id === item.id).map(v =>
          <PlayImage key={`${clipScope}:${v.id}`} playId={view.play_id} visualId={v.id} label={v.label} />)}
        <p className="mt-3 break-all text-xs leading-6 text-mist">{item.kind ? `${kindLabels[item.kind]} · ` : ''}{isPublic ? '已公开' : disclosureLabels[item.disclosure]}{sharedBy ? ` · 由${sharedBy}公开` : ''}</p>
        {!isPublic && item.retelling && <p className="mt-2 text-xs leading-6 text-brass">原本不能出示；{item.retelling === 'MUST_RETELL' ? '请按角色要求，用自己的话讲清经过。' : '可以用自己的话选择讲述相关经历。'}</p>}
        {!isPublic && item.disclosure === 'MUST_SHARE' && <p className="mt-2 text-xs leading-6 text-brass">请按剧本要求分享。需要你点击公开，页面不会自动替你分享。</p>}
        <div className="mt-3 flex flex-wrap gap-3 text-sm text-brass">
          {props.ownerId && <button type="button" disabled={readingActionsLocked} className="min-h-11 rounded-lg border border-brass/40 px-3 py-2 hover:bg-raised disabled:opacity-40" onClick={() => {
            if (!readingActionsLocked) setClueSelection({ scope: clipScope, id: `${collection}:${item.id}` });
          }}>收藏线索</button>}
        </div>
        {readingActionsLocked && <p role="status" className="mt-2 text-xs text-mist">资料正在更新或需要刷新，完成后可收藏线索。</p>}
        {canShare && <button type="button" aria-label={`公开${collection === 'knowledge' ? '资料' : '线索'} ${item.id}`} disabled={locked}
          onClick={() => { if (!locked && view && canPerformPlayAction(view, action)) props.onAction(action); }}
          className="mt-3 rounded border border-brass px-3 py-2 text-sm text-brass disabled:opacity-50">{collection === 'knowledge' ? '公开这份资料' : '公开这条线索'}</button>}
      </div>;
    });
    return <section aria-label={title} className="min-w-0 rounded border border-line bg-panel p-5">
      <h2 className="text-lg font-semibold">{title}</h2>
      <p className="mt-2 text-xs leading-6 text-mist">{isPublic ? '按当前规则已公开的内容。' : '仅本角色可见；允许公开的材料可手动分享。'}</p>
      {items.length ? collection === 'evidence'
        ? <PlayClueDeck key={`${clipScope}:${title}`} label={title} items={items.map((item, index) => ({ id: item.id, heading: `线索 ${index + 1}`, content: contents[index] }))} />
        : <ul className="mt-4 space-y-4">{(isPublic ? items.map(material => ({ kind: 'material' as const, id: material.id, material })) : performanceReadingBlocks(items)).map(block => <li key={block.id} className="min-w-0 rounded border border-line p-4">
          {block.kind === 'performance' ? <PlayPerformanceSummary key={`${pageScope}:${block.id}`} group={block} renderVisuals={item => view?.visuals?.filter(v => v.collection === collection && v.material_id === item.id).map(v => <PlayImage key={`${clipScope}:${v.id}`} playId={view.play_id} visualId={v.id} label={v.label} />)} /> : contents[items.findIndex(item => item.id === block.id)]}
        </li>)}</ul>
        : <p className="mt-4 text-sm text-mist">当前没有这类已解锁材料。</p>}
    </section>;
  };
  return <main ref={pageRoot} className={`play-page mx-auto min-w-0 max-w-4xl px-4 text-paper ${view ? 'pt-0' : 'pt-20 md:pt-12'} ${composerOpen ? 'pb-[55vh]' : 'pb-32'}`}>
    {view && <header ref={topbar} aria-label="游戏顶部导航" className="play-topbar sticky z-[45] rounded-b-xl border border-line bg-panel p-2 shadow-lg" onClickCapture={event => {
      if (event.currentTarget.contains(event.target as Node) && !(event.target as HTMLElement).closest('[data-play-more]')) closeOverview();
      pageRoot.current?.style.setProperty('--play-navigation-height', `${Math.ceil(event.currentTarget.getBoundingClientRect().height)}px`);
    }}>
      <div className="play-header-row flex min-w-0 items-center justify-between gap-2">
        <Link href="/" className="play-home-link hidden min-h-11 shrink-0 items-center gap-2 rounded-lg border border-brass/50 bg-raised px-3 py-2 text-sm text-brass md:inline-flex"><Home aria-hidden="true" className="h-4 w-4" />返回首页</Link>
        <div className="play-header-title min-w-0">
          <h2 className="truncate font-dossier text-base font-semibold sm:text-lg">{view.script.title}<span className="ml-2 font-sans text-xs font-normal text-brass">{actor?.name || '固定角色'}</span></h2>
          <p aria-label="当前阶段" className="mt-1 truncate text-xs leading-5 text-mist">{view.round_workspace?.phases.find(phase => phase.phase_id === view.current_phase.id)?.title || (view.full_game ? (currentPage === 'finale' ? '结局 · 答卷' : currentPage === 'investigation' ? '调查阶段' : '阅读材料') : view.current_phase.title)}</p>
        </div>
        <div className="play-top-actions flex shrink-0 items-center justify-end gap-2">{guided && <PlayHostHints key={`host-hints:${pageScope}`} view={view} locked={Boolean(locked)} error={props.error} onGuided={props.onGuided} />}
          {!view.settled && !view.phase_complete && <PlayPhaseAdvance key={`phase-advance:${pageScope}`} view={view} hiddenMemorySequences={speech.pendingSequences}
            disabled={Boolean(locked || (guided && currentPage === 'investigation' ? !props.onGuided || !canGuide(view, { action: 'FINISH_INVESTIGATION' }) : !canPerformPlayAction(view, { action: 'ADVANCE_PHASE' })))}
            renderVisual={visual => <PlayImage key={`${clipScope}:recap:${visual.id}`} playId={view.play_id} visualId={visual.id} label={visual.label} />}
            onContinue={() => { if (locked) return; if (guided && currentPage === 'investigation') { if (canGuide(view, { action: 'FINISH_INVESTIGATION' })) props.onGuided?.({ action: 'FINISH_INVESTIGATION' }); } else if (canPerformPlayAction(view, { action: 'ADVANCE_PHASE' })) props.onAction({ action: 'ADVANCE_PHASE' }); }} />}
        </div>
      </div>
      <nav aria-label="游戏内导航" className="play-topbar-primary mt-2 flex min-w-0 items-center gap-1 border-t border-line pt-1">
        {guided && currentPage !== 'reading' ? <button type="button" className="play-nav-action" aria-current={page === currentPage ? 'page' : undefined} onClick={() => goToPage(currentPage)}>{currentPage === 'finale' ? '终局答卷' : '当前调查'}</button> : <a className="play-nav-action" href="#play-reading" onClick={event => navigate(event, 'reading')}>阅读材料</a>}
        {view.discussion ? <a className="play-nav-action" href="#play-discussion" onClick={event => navigate(event, 'discussion')}>公共讨论</a> : <span aria-disabled="true" className="play-nav-action text-mist">公共讨论</span>}
        <PlayReferencePanel toolbar scope={clipScope} characterName={actor?.name || '我的角色'} materials={[...view.private_knowledge, ...view.private_evidence, ...(view.reading_supplements || [])]} rulesMaterials={view.public_knowledge.filter(m => !m.shared_by_character_id)} memories={visibleMemories} locked={props.requiresRefresh || props.loading || !props.ownerId} />
        {guided && view.round_workspace && <PlayRoundWorkspace key={clipScope} view={view} hiddenMemorySequences={speech.pendingSequences} locked={Boolean(readingActionsLocked)} onCollect={(collection, id) => setClueSelection({ scope: clipScope, id: `${collection}:${id}` })} renderVisual={visual => <PlayImage key={`${clipScope}:round:${visual.id}`} playId={view.play_id} visualId={visual.id} label={visual.label} />} />}
        <details data-play-more key={clipScope} ref={overview} className="play-more-tools min-w-0" onKeyDown={event => {
          if (event.key === 'Escape' && event.currentTarget.contains(event.target as Node)) { event.preventDefault(); closeOverview(); event.currentTarget.querySelector('summary')?.focus({ preventScroll: true }); }
        }}>
          <summary className="play-nav-action cursor-pointer">更多</summary>
          <div className="play-more-content absolute left-0 right-0 top-full z-40 mt-1 max-h-[65dvh] overflow-y-auto overscroll-contain rounded-xl border border-brass/40 bg-panel p-3 shadow-xl sm:left-auto sm:w-[360px]">
            <nav aria-label="更多游戏工具" className="play-topbar-tools grid grid-cols-2 gap-2">
              {guided && currentPage !== 'reading' && <a className="play-nav-action" href="#play-reading" onClick={event => navigate(event, 'reading')}>阅读材料</a>}
              {guided && (view.full_game?.phase_kind === 'INVESTIGATION' || Boolean(view.full_game?.private_discussion.length)) && <button type="button" className="play-nav-action" aria-current={page === 'private' ? 'page' : undefined} onClick={() => goToPage('private')}>单独对话</button>}
              <PlayClueCollection onRestoreFocus={() => overview.current?.querySelector('summary')?.focus({ preventScroll: true })} ownerId={props.ownerId || null} playId={view.play_id} characterId={view.selected_character_id} locked={readingActionsLocked} available={availableClues} selection={selectedClue} onSelectionHandled={() => setClueSelection(undefined)} renderVisual={visual => <PlayImage key={`${clipScope}:collected:${visual.id}`} playId={view.play_id} visualId={visual.id} label={visual.label} />} />
              <PlayNotebook key={`${props.ownerId}:${view.play_id}:${view.selected_character_id}`} ownerId={props.ownerId || null} playId={view.play_id} characterId={view.selected_character_id} locked={props.requiresRefresh || props.loading} memories={visibleMemories} clip={notebookClip} inlineTrigger onClipHandled={() => setClip(undefined)} />
              {!guided && view.full_game?.finale && <a className="play-nav-action" href="#play-finale" onClick={event => navigate(event, 'finale')}>终局答卷</a>}
              {view.discussion && <button type="button" className="play-nav-action" onClick={openComposer}>{view.settled ? '查看回应' : singlePlayer ? '议题 / 发言' : '发言 / 提问'}</button>}
              {guided && page !== 'reading' && <button type="button" className="play-nav-action" onClick={() => goToPage(navigation.history.at(-1) || 'reading', true)}>返回{navigation.history.length ? '上一页' : '阅读资料'}</button>}
            </nav>
            <details className="mt-3 border-t border-line pt-2"><summary className="min-h-11 cursor-pointer py-2 text-sm text-brass">故事背景</summary>
              <PlayText text={view.introduction.text} /><p className="mt-3 text-xs leading-6 text-mist">翻页只切换查看内容。已获得的线索、发言和调查消耗都会保存；文中的时间为故事时间。</p>
              <p className="mt-2 text-xs text-mist">已保存记录：{view.revision} 条</p>
            </details>
    <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-line pt-3">
      <button type="button" disabled={props.loading || props.busy || (!props.playId && !props.openingSessionId)} onClick={() => { if (!props.loading && !props.busy) props.onReload(); }}
        className="rounded border border-line px-3 py-2 text-sm disabled:opacity-50">刷新进度</button>
      <Link href={(view?.opening_session_id || props.openingSessionId) ? `/play/package-preview?session=${encodeURIComponent(view?.opening_session_id || props.openingSessionId)}` : '/play/package-preview'} className="text-sm text-brass underline">返回开场阅读</Link>
    </div>
            <button type="button" className="mt-3 min-h-11 w-full rounded border border-line px-3 py-2 text-sm text-brass" onClick={() => { closeOverview(); overview.current?.querySelector('summary')?.focus({ preventScroll: true }); }}>收起工具</button>
          </div>
        </details>
      </nav>
    </header>}
    <div className={view ? 'sr-only' : ''}>
    <p className="text-xs text-brass">独自入局 · 与角色共同推理</p>
    <h1 className="mt-2 font-dossier text-3xl">进入游戏</h1>
    <p className="mt-4 text-sm leading-7 text-mist">{!view
      ? '从已选角色的开场开始，按阶段阅读并与其他角色交流。开始后会显示这个版本可用的调查、对话与结局玩法。'
      : view.full_game
      ? '按阶段阅读，与其他角色交流，共同调查。最后分别封存答卷、指认和信任，所有人提交后查看个人得分与结局。'
      : view?.role_responses
      ? '你可以以角色身份发言、追问其他角色，并阅读自己的私密回忆。AI 的回答是角色说法，需要结合线索判断。当前为文字试玩，尚无自动胜负判定或语音。'
      : '你可以手动推进阶段、分享允许公开的材料，并向其他角色提问。此版本 AI 按问题挑选可公开资料，以原文回答；尚未接入自然对白、自动胜负或语音，仍是文字规则试玩。'}</p>
    </div>
    {props.error && (!composerOpen || !view?.discussion) && <div role="alert" className="play-feedback sticky z-20 mt-5 whitespace-pre-wrap break-words rounded border border-red-400/50 bg-panel p-4 text-sm leading-6 text-red-200 shadow-lg"><p>{props.error}</p>{props.requiresRefresh && <button type="button" disabled={props.loading || props.busy} className="mt-3 min-h-11 rounded border border-brass/50 px-3 py-2 text-brass disabled:opacity-50" onClick={props.onReload}>刷新进度</button>}</div>}
    {props.notice && <p role="status" className="mt-3 break-words border-l-2 border-emerald-500/50 px-3 py-1 text-xs text-mist">{props.notice}</p>}
    {!view && <div className="mt-5 flex flex-wrap items-center gap-4">
      <button type="button" disabled={props.loading || props.busy || (!props.playId && !props.openingSessionId)} onClick={() => { if (!props.loading && !props.busy) props.onReload(); }}
        className="rounded border border-line px-3 py-2 text-sm disabled:opacity-50">刷新进度</button>
      <Link href={props.openingSessionId ? `/play/package-preview?session=${encodeURIComponent(props.openingSessionId)}` : '/play/package-preview'} className="text-sm text-brass underline">返回开场阅读</Link>
    </div>}
    {view && props.pendingTopicReplyKey && view.single_player?.turns.filter(turn => turn.reply_request?.idempotency_key === props.pendingTopicReplyKey).map(turn => <section key={turn.id} aria-label="核对议题回答" className="mt-4 rounded border border-line bg-panel p-4"><p className="text-sm text-mist">“{turn.title}”的回答尚未确认。核对只沿用这次请求。</p><button type="button" disabled={props.loading || props.busy || !props.onTopicReply} className="mt-2 min-h-11 rounded border border-brass/40 px-3 py-2 text-sm text-brass disabled:opacity-40" onClick={() => { if (!props.loading && !props.busy) props.onTopicReply?.(turn.id); }}>检查上次议题回答</button></section>)}
    {view && props.retryingStatement && <section aria-label="核对上次公开发言" className="mt-4 rounded border border-brass/40 bg-panel p-4"><p className="text-sm text-mist">还有一条公开发言未确认，核对后再继续调查。{props.requiresRefresh ? '请先点击“刷新进度”，再检查这条发言。' : '检查会沿用原请求，不会另发一条新消息。'}</p><button type="button" disabled={!canSpeak} className="mt-2 min-h-11 rounded border border-brass/40 px-3 py-2 text-sm text-brass disabled:opacity-40" onClick={() => { if (canSpeak) props.onSpeak?.(); }}>检查上次发言结果</button></section>}
    {view && props.onCheckTopic && <section aria-label="核对议题操作" className="mt-4 rounded border border-line bg-panel p-4"><p className="text-sm text-mist">上次议题操作尚未确认，请先核对原请求。</p><button type="button" disabled={props.loading || props.busy} className="mt-2 min-h-11 rounded border border-brass/40 px-3 py-2 text-sm text-brass disabled:opacity-40" onClick={() => { if (!props.loading && !props.busy) props.onCheckTopic?.(); }}>检查上次议题操作</button></section>}
    {view && singlePlayer && props.retryingResponse && <section aria-label="核对之前的角色回应" className="mt-4 rounded border border-line bg-panel p-4"><p className="text-sm text-mist">之前的角色请求仍需核对，沿用原请求查看结果。</p><button type="button" disabled={!canRespond} className="mt-2 min-h-11 rounded border border-brass/40 px-3 py-2 text-sm text-brass disabled:opacity-40" onClick={() => { if (canRespond) props.onRespond?.(); }}>检查上次回应结果</button></section>}
    {view && naturalDialogue && props.retryingQuestion && <section aria-label="核对之前的材料提问" className="mt-4 rounded border border-line bg-panel p-4">
      <h2 className="text-sm font-semibold">核对之前的材料提问</h2><p className="mt-2 text-sm leading-6 text-mist">之前保存的材料提问尚未确认结果，可检查原请求。新的角色交流请使用集中发言入口。</p>
      <button type="button" disabled={!canAsk} onClick={() => { if (canAsk) props.onAsk(); }} className="mt-3 min-h-11 rounded border border-brass px-3 py-2 text-sm text-brass disabled:opacity-50">检查上次提问结果</button>
    </section>}
    {view && naturalDialogue && view.pending_ai && !props.retryingQuestion && <p role="status" className="mt-4 text-sm text-mist">有一项互动正在处理，刷新查看结果。</p>}
    {props.onCheckGuided && <button type="button" disabled={props.loading || props.busy} onClick={props.onCheckGuided} className="mt-3 min-h-11 rounded border border-brass px-3 py-2 text-sm text-brass">检查上次流程结果</button>}
    {props.loading && <p role="status" className="mt-5 text-sm text-mist">正在读取游戏进度…</p>}
    {!props.loading && !props.playId && !props.openingSessionId && <p className="mt-6 text-sm text-mist">请先选择角色、阅读开场，再点击“开始游戏”。</p>}
    {!props.loading && props.view === null && !props.playId && props.openingSessionId && <section aria-label="进入游戏" className="mt-6 rounded border border-line bg-panel p-5">
      <h2 className="text-lg font-semibold">准备进入游戏</h2>
      <p className="mt-3 text-sm leading-7 text-mist">将使用你已选择的角色。已有进度会继续保留。</p>
      <button type="button" disabled={!canCreate} onClick={() => { if (canCreate) props.onCreate(); }} className="mt-4 rounded bg-brass px-4 py-3 text-sm font-semibold text-ink disabled:opacity-50">{props.busy ? '正在开始…' : '开始游戏'}</button>
    </section>}
    {view && <article aria-label="我的游戏" className="mt-6 min-w-0 space-y-5">
      {view.mechanics !== undefined && !view.full_game && <section aria-label="本轮搜证" className="min-w-0 rounded border border-line bg-panel p-5">
        <h2 className="text-lg font-semibold">本轮搜证</h2>
        <p className="mt-3 text-sm leading-7 text-mist">行动点由本轮所有角色共享，用于执行搜证动作；与角色互动的轮次分别计算。</p>
        {mechanics ? <>
          <p className="mt-3 break-all text-sm leading-7 text-brass">本轮共享行动点：剩余 {mechanics.remaining_points} / {mechanics.initial_points} 点，已用 {mechanics.spent_points} 点。</p>
          {view.settled ? <p className="mt-3 text-sm text-mist">本轮记录已保存，不能继续搜证。</p>
            : mechanics.available_actions.length ? <ul className="mt-4 space-y-3">{mechanics.available_actions.map(item => {
              const action: PackagePlayAction = { action: 'PERFORM_ACTION', target: { action_id: item.id } };
              return <li key={item.id} className="min-w-0 rounded border border-line p-4">
                <h3 className="whitespace-pre-wrap break-all font-semibold">{item.label}</h3>
                <p className="mt-2 text-xs leading-6 text-mist">消耗 {item.cost} 点</p>
                <button type="button" aria-label={`执行搜证：${item.label}`} disabled={locked || !canPerformPlayAction(view, action)}
                  onClick={() => { if (!locked && canPerformPlayAction(view, action)) props.onAction(action); }}
                  className="mt-3 rounded border border-brass px-3 py-2 text-sm text-brass disabled:opacity-50">执行搜证</button>
              </li>;
            })}</ul>
              : <p className="mt-3 text-sm leading-7 text-mist">{mechanics.remaining_points === 0
                ? '本轮共享行动点已用尽，当前没有可执行的搜证动作。'
                : '当前没有可执行的搜证动作。可查看已解锁材料，或刷新核对最新状态。'}</p>}
        </> : <p role="alert" className="mt-3 text-sm text-brass">搜证状态不完整，请刷新进度核对。</p>}
      </section>}
      {view.single_player?.stage && <details aria-label="本轮核心目标" className="rounded-lg border border-brass/30 bg-panel px-4 py-2"><summary className="min-h-11 cursor-pointer py-2 text-sm font-medium text-brass">本轮核心目标<span className="ml-3 inline-block text-xs font-normal text-mist">建议思考 {phaseThinkingTime(view.full_game?.phase_kind)} 分钟 · 不限时</span></summary>{view.settled ? <p className="mt-3 text-sm leading-7">先阅读自己的结局，再核对真相与分项得分。其他角色的结局可以按需展开查看。</p> : <><p className="mt-3 text-sm leading-6">{view.single_player.stage.goal}</p><ol className="mt-3 list-decimal space-y-2 pl-5 text-sm leading-7">{view.single_player.stage.instructions.map((item, index) => <li key={index}>{item}</li>)}</ol><p aria-label="本阶段完成状态" className="mt-3 text-sm leading-7 text-mist">{view.single_player.stage.completion}</p><p className="mt-2 text-xs text-mist">遇到卡点，可随时点击顶部“询问主持人”。</p>{page !== 'private' && view.single_player.topics.some(topic => topic.responders.some(responder => responder.channels.includes('PUBLIC'))) && <button type="button" className="mt-3 min-h-11 rounded border border-brass/40 px-3 py-2 text-sm text-brass" onClick={openComposer}>围绕本轮议题交流</button>}</>}</details>}
      {singlePlayer && !view.settled && !view.single_player?.available && <p role="status" className="text-sm text-mist">当前单人流程暂不可用，已保存资料和记录仍可阅读。</p>}
      {showReading && <>
      <div id="play-reading" className="play-scroll-target" />
      {guided && !singlePlayer && <section aria-label="本局操作说明" className="rounded-lg border border-brass/40 bg-panel p-5"><h2 className="font-semibold">本局操作说明</h2><p className="mt-3 text-sm leading-7 text-mist">本局搜证由你直接选择地点；其他角色会按流程分享必须说明的线索。只有终局指认与信任保留各角色投票。</p></section>}
      {Boolean(view.reading_supplements?.length) && <section aria-label="开场补充资料" className="min-w-0 rounded border border-line bg-panel p-5">
        <h2 className="text-lg font-semibold">开场补充资料</h2>
        <div className="mt-4 space-y-4">{view.reading_supplements!.map(item => <div key={item.id}><PlayText text={item.text} /></div>)}</div>
      </section>}
      {materials('公开资料', 'knowledge', view.public_knowledge, true)}
      {materials('公开线索', 'evidence', view.public_evidence, true)}
      {materials('我的未公开资料', 'knowledge', view.private_knowledge.filter(item => !view.public_knowledge.some(shared => shared.id === item.id)), false)}
      {materials('我的未公开线索', 'evidence', view.private_evidence.filter(item => !view.public_evidence.some(shared => shared.id === item.id)), false)}
      <section aria-label="阶段操作" id="play-phase-actions" className="min-w-0 rounded border border-line bg-panel p-5">
        <h2 className="text-lg font-semibold">阅读与回看</h2>
        {view.settled ? <p className="mt-3 text-sm text-mist">结局已揭晓，资料和记录保留供回看。</p> : guided && currentPage !== 'reading' ? <><p className="mt-3 text-sm text-mist">这里保存已解锁的阅读材料。返回当前步骤继续游戏。</p><button type="button" className="mt-4 min-h-11 rounded border border-line px-4 py-2 text-sm text-brass" onClick={() => goToPage(currentPage)}>回到{currentPage === 'finale' ? '终局答卷' : '当前调查'}</button></> : view.phase_complete ? <>
          <p className="mt-3 text-sm text-mist">{canFinishPlayPhase(view) ? '阶段材料已走到末段。点击结束后才揭晓结尾与真相，之后保留记录供阅读。' : '本轮搜证尚未满足阶段结束条件。满足条件后，才能结束并揭晓结尾与真相。'}</p>
          <button type="button" disabled={locked || !canPerformPlayAction(view, { action: 'SETTLE' })} onClick={() => { if (!locked && canPerformPlayAction(view, { action: 'SETTLE' })) props.onAction({ action: 'SETTLE' }); }} className="mt-4 min-h-11 rounded bg-brass px-4 py-3 text-sm font-semibold text-ink disabled:opacity-40">结束并揭晓真相</button>
        </> : <><p className="mt-3 text-sm leading-7 text-mist">阅读完成后，点击固定顶栏右上角的“进入下一阶段”。你仍可随时回看已获材料。</p>
          {!canPerformPlayAction(view, { action: 'ADVANCE_PHASE' }) && <p className="mt-2 text-sm text-mist">{canFinishPlayPhase(view) ? '当前不能继续推进，请刷新确认最新状态。' : '本轮搜证尚未满足阶段结束条件。'}</p>}</>}
      </section>
      </>}
      {guided && page === 'investigation' && <><PlayGuidedStage view={view} locked={Boolean(locked)} onGuided={props.onGuided} />
        {(view.table_decisions?.requests.some(r => r.status === 'PENDING' && r.action !== 'SEAL_FINALE') || view.phone_turns?.requests.some(r => r.status === 'PENDING')) && <section aria-label="核对旧流程记录" className="rounded-lg border border-brass/40 bg-panel p-5"><h2 className="font-semibold">核对旧流程记录</h2><p className="mt-3 text-sm leading-7 text-mist">上一版有尚未确认的请求。先核对原请求或结束过期等待，再按当前流程调查。检查只读取原请求的处理结果。</p><div className="mt-3 flex flex-wrap gap-3">
          {view.table_decisions?.requests.filter(r => r.status === 'PENDING' && r.action !== 'SEAL_FINALE').map(r => <button key={r.request_id} type="button" disabled={locked || !props.onDecide} className="min-h-11 rounded border border-brass/40 px-3 py-2 text-sm text-brass disabled:opacity-40" onClick={() => { if (!locked) props.onDecide?.(r.character_id, r.action); }}>核对{view.characters.find(c => c.id === r.character_id)?.name}的旧调查请求</button>)}
          {view.phone_turns?.requests.filter(r => r.status === 'PENDING').map(r => <button key={r.request_id} type="button" disabled={locked || !props.onPhone} className="min-h-11 rounded border border-brass/40 px-3 py-2 text-sm text-brass disabled:opacity-40" onClick={() => { if (!locked) props.onPhone?.('STEP'); }}>核对旧电话请求</button>)}
        </div></section>}
        {materials('本局已公开调查线索', 'evidence', view.public_evidence, true)}{materials('我的未公开调查线索', 'evidence', view.private_evidence.filter(item => !view.public_evidence.some(shared => shared.id === item.id)), false)}</>}
      {view.full_game && <div hidden={guided && page !== 'private' && page !== 'finale'} className="space-y-5"><FullGamePanel excludePrivate key={pageScope} ownerId={props.ownerId} view={view} locked={Boolean(locked)} onTable={props.onTable} onDecide={props.onDecide} onPrivateReply={props.onPrivateReply} onPhone={props.onPhone} onTopic={props.onTopic} onTopicReply={props.onTopicReply} pendingTopicReplyKey={props.pendingTopicReplyKey} recoveryLocked={props.loading || props.busy} renderSpeech={speech.render} section={guided ? page === 'private' ? 'private' : currentPage === 'finale' ? 'finale' : 'private' : 'all'} onFinaleVotes={props.onFinaleVotes} finaleVotesNeedsConfirmation={props.finaleVotesNeedsConfirmation} finaleVotesProgress={props.finaleVotesProgress} /></div>}
      {guided && page === 'finale' && !view.settled && <section aria-label="揭晓终局" className="rounded border border-line bg-panel p-5"><p className="text-sm text-mist">所有人封存答卷后，揭晓个人得分与结局。</p><button type="button" disabled={locked || !canPerformPlayAction(view, { action: 'SETTLE' })} onClick={() => { if (!locked && canPerformPlayAction(view, { action: 'SETTLE' })) props.onAction({ action: 'SETTLE' }); }} className="mt-3 min-h-11 rounded bg-brass px-4 py-3 text-sm font-semibold text-ink disabled:opacity-40">结束并揭晓真相</button></section>}
      {showReading && view.memories && <section id="play-memories" aria-label="我的私密回忆" className="min-w-0 rounded border border-brass/40 bg-panel p-5">
        <h2 className="text-lg font-semibold">我的私密回忆</h2>
        <p className="mt-3 text-sm leading-7 text-mist">听到其他角色实际说出关键词，或取得对应线索后，系统会自动授予回忆。自己说词不能触发。原卡只属于你；已想起的内容随本局保存，也可在随身手记中查看。</p>
        {visibleMemories.length ? <ul className="mt-4 space-y-3">{visibleMemories.map(item => { const cause = item.cause; return <li key={item.id} className="min-w-0 rounded border border-line p-4">
          <h3 className="whitespace-pre-wrap break-all font-semibold">{item.title}</h3>
          <p className="mt-2 text-xs leading-6 text-mist">{cause.kind === 'OTHER_PUBLIC_SPEECH'
            ? `听到${view.characters.find(role => role.id === cause.speaker)?.name || '其他角色'}的公开发言后想起`
            : cause.kind === 'OTHER_HEARD_SPEECH'
              ? `听到${view.characters.find(role => role.id === cause.speaker)?.name || '其他角色'}的${cause.channel === 'PRIVATE' ? '电话发言' : '公开发言'}后想起`
            : '取得相关线索后想起'} · {item.sequence === 0 ? '开场' : `第 ${item.sequence} 条记录`}</p>
          <p className="mt-2 text-xs leading-6 text-brass">{item.retelling === 'MUST_RETELL'
            ? '剧本要求你用自己的话把相关内容告诉大家，不能出示原卡。'
            : '你可以用自己的话向大家讲述，不能出示原卡。'}</p>
          <details className="mt-3"><summary className="cursor-pointer text-sm text-brass">阅读这段回忆</summary>
            <p className="mt-3 text-xs text-mist">{item.kind === 'FACT' ? '本角色已知材料' : item.kind === 'CLAIM' ? '角色说法' : '角色推测'} · 尚未成为公开证据</p>
            <PlayText text={item.text} />
            {view.visuals?.filter(v => v.collection === 'memory' && v.material_id === item.id).map(v =>
              <PlayImage key={`${clipScope}:${v.id}`} playId={view.play_id} visualId={v.id} label={v.label} />)}
          </details>
        </li>; })}</ul> : <p className="mt-3 text-sm text-mist">目前还没有想起新的回忆。继续听其他角色发言或调查线索。</p>}
      </section>}
      {showPublicSpeech && view.discussion && <section id="play-discussion" aria-label="公共讨论" className="min-w-0 rounded border border-line bg-panel p-5">
        <h2 className="text-lg font-semibold">{showDiscussion ? '公共讨论' : '本轮角色说明'}</h2>
        <p className="mt-3 text-sm leading-7 text-mist">在这里以你的角色发言或解释。发言会保存为角色说法，不代表证据已证实，也不会公开原卡。</p>
        <p className="mt-2 text-xs leading-6 text-mist">{singlePlayer ? '这里保存大家已经说过的话。你的公开表达不会自动要求 AI 回答；与角色交流请使用当前议题。' : view.role_responses ? '保存发言后，选择一位角色回应。后续追问会结合已经发生的公共对话。' : '保存后可向下方 AI 征求调查建议。材料原文问答仍是独立功能，尚不读取这些发言。'}</p>
        {publicEntries.length ? <ol className="mt-4 space-y-3">{publicEntries.map(entry => <li id={`play-message-${entry.id}`} key={entry.id} className="min-w-0 scroll-mt-20 rounded border border-line p-4">
          <p className="break-all text-sm font-semibold">{view.characters.find(item => item.id === entry.speaker)?.name} · 角色说法</p>
          <p className="mt-1 text-xs text-mist">{entry.phase_id === view.current_phase.id ? '当前阶段' : '之前阶段'} · 第 {entry.sequence} 条记录</p>
          {entry.speaker === view.selected_character_id ? <PlayText text={entry.text} /> : speech.render(entry)}
          <div className="mt-3 flex flex-wrap gap-4 text-xs text-brass">
            {props.ownerId && <button type="button" disabled={readingActionsLocked} onClick={() => { if (!readingActionsLocked) setClip({ scope: clipScope, id: `speech:${entry.id}`, title: `${view.characters.find(c => c.id === entry.speaker)?.name}的发言`, text: entry.text }); }}>收藏这条发言</button>}
          </div>
        </li>)}</ol> : <p className="mt-3 text-sm text-mist">还没有公共发言。</p>}
      </section>}
      {showDiscussion && view.investigation_proposals && (!singlePlayer || props.retryingProposal || view.investigation_proposals.entries.length > 0) && <section aria-label="AI 调查建议" className="min-w-0 rounded border border-line bg-panel p-5">
        <h2 className="text-lg font-semibold">AI 调查建议</h2>
        <p className="mt-3 text-sm leading-7 text-mist">向其他角色询问下一步调查哪里。对方根据自己掌握的材料和你们的讨论给出建议；不会替你执行搜证或投票。</p>
        <p className="mt-2 text-xs leading-6 text-mist">征求一次建议计一轮 AI 互动。你也可以先在底部发言，说明想调查的方向。</p>
        <p role="status" className="mt-3 text-sm text-brass">{proposalAvailability(view, props.proposalCharacter || '')}</p>
        {view.full_game?.phase_kind === 'READING' && <a href="#play-phase-actions" className="mt-3 inline-block text-sm text-brass underline">查看顶栏右上角的阶段操作</a>}
        {((!singlePlayer && !view.settled) || props.retryingProposal) && <div className="mt-4 space-y-3">
          <div className="block text-sm">征求谁的建议<PlaySelect presentation="choices" label="征求谁的建议" value={props.proposalCharacter || ''} disabled={locked || props.retryingProposal} onChange={id => props.onProposalCharacterChange?.(id)} placeholder="请选择 AI 角色" options={view.characters.filter(item => item.id !== view.selected_character_id).map(item => ({ value: item.id, label: item.name }))} /></div>
          <button type="button" disabled={!canPropose} onClick={() => { if (canPropose) props.onPropose?.(); }} className="rounded bg-brass px-4 py-3 text-sm font-semibold text-ink disabled:opacity-50">
            {props.retryingProposal ? '检查上次建议结果' : '征求调查建议'}</button>
        </div>}
        {view.investigation_proposals.entries.length ? <ol className="mt-4 space-y-3">{view.investigation_proposals.entries.map(entry => <li key={entry.id} className="min-w-0 rounded border border-line p-4">
          <h3 className="break-all font-semibold">{view.characters.find(item => item.id === entry.speaker)?.name} · 调查建议</h3>
          <p className="mt-1 text-xs text-mist">{entry.phase_id === view.current_phase.id ? '当前阶段' : '之前阶段'} · 第 {entry.sequence} 条记录 · 仅为建议</p>
          <PlayText text={entry.text} />
          {entry.basis.length > 0 && <details className="mt-3"><summary className="cursor-pointer text-sm text-brass">查看引用的公开信息</summary><ul className="mt-3 space-y-3">{entry.basis.map(ref => <li key={`${ref.collection}:${ref.id}`}>
            <p className="text-xs text-mist">{kindLabels[ref.kind]}{ref.speaker ? ` · ${view.characters.find(item => item.id === ref.speaker)?.name || '角色'}` : ''}</p>
            <PlayText text={ref.text} />
          </li>)}</ul></details>}
        </li>)}</ol> : <p className="mt-3 text-sm text-mist">{view.investigation_proposals.requests.some(request => request.status === 'PENDING') ? '正在等待这次建议的结果，可核对原请求。' : view.investigation_proposals.requests.length ? '本次尚未取得可显示的调查建议。你可以直接选择地点继续，也可另行征求建议。' : '你还没有征求调查建议。建议是可选的，可以直接选择调查地点继续。'}</p>}
      </section>}
      {showDiscussion && <>
      {!naturalDialogue && <section aria-label="向 AI 角色提问" className="min-w-0 rounded border border-line bg-panel p-5">
        <h2 className="text-lg font-semibold">向 AI 角色提问</h2>
        <p className="mt-3 text-sm leading-7 text-mist">回答只使用核验后允许公开的资料原文；没有可回答的资料时也会保留标准答复，不会补编情节。</p>
        {!view.model.available && !view.settled && <p role="status" className="mt-3 text-sm leading-7 text-brass">{view.model.reason === 'QUESTION_LIMIT' ? '本局已达到 AI 互动轮数上限。' : view.model.reason === 'FINALE_SEALING' ? '当前请完成终局答卷与封卷。' : '当前暂时无法开始新的 AI 互动。'}{view.mechanics === undefined ? '已获得的材料仍可阅读。' : '你仍可按规则查看调查进度和已获得的材料。'}</p>}
        {view.pending_ai && <p role="status" className="mt-3 text-sm leading-7 text-brass">有一条提问尚未确认结果。可刷新查看；重试同一提问不会重复调用模型。新操作若仍受占用，会提示刷新。</p>}
        {view.last_ai_status && Object.hasOwn(aiStatusLabels, view.last_ai_status) && <p role="status" className="mt-3 text-sm leading-7 text-mist">{aiStatusLabels[view.last_ai_status]}</p>}
        {view.ai_interactions && <p className="mt-3 text-sm leading-6 text-brass">本局 AI 互动：已发起 {view.ai_interactions.initiated} / 最多 {view.ai_interactions.limit} 轮。角色回应、调查建议和 AI 行动共用轮次；重看已保存结果不重复计数。</p>}
        {!view.settled && <div className="mt-4 min-w-0 space-y-4">
          <div className="block text-sm">提问对象<PlaySelect presentation="choices" label="提问对象" value={props.characterId} disabled={locked || !view.model.available} onChange={props.onCharacterChange} placeholder="请选择其他角色" options={view.characters.filter(item => item.id !== view.selected_character_id).map(item => ({ value: item.id, label: item.name }))} /></div>
          <label className="block text-sm">你的问题<textarea value={props.question} disabled={locked || !view.model.available} onChange={event => props.onQuestionChange(event.target.value)} rows={3} maxLength={2000} className="mt-2 w-full min-w-0 max-w-full rounded border border-line bg-ink p-3 text-paper" /></label>
          <PlayVoiceInput ownerId={props.ownerId} playId={view.play_id} phaseId={view.current_phase.id} revision={view.revision} channel="QUESTION" disabled={locked || !view.model.available || props.retryingQuestion} value={props.question} onChange={props.onQuestionChange} />
          <p className="text-xs text-mist">{Array.from(props.question.trim()).length} / 1000 字符。请围绕人物、经历和线索提问；游戏外任务和索取隐藏资料会被拒绝。</p>
          {Array.from(props.question.trim()).length > 1000 && <p role="alert" className="text-xs text-brass">问题超过 1000 个字符，请缩短后再发送。</p>}
          <button type="button" disabled={!canAsk} onClick={() => { if (canAsk) props.onAsk(); }} className="rounded bg-brass px-4 py-3 text-sm font-semibold text-ink disabled:opacity-50">{props.busy ? '正在保存…' : props.retryingQuestion ? '检查上次提问结果' : '发送问题'}</button>
        </div>}
      </section>}
      {(!naturalDialogue || view.dialogue.length > 0) && <section aria-label="已保存的角色答复" className="min-w-0 rounded border border-line bg-panel p-5">
        <h2 className="text-lg font-semibold">已保存的角色答复</h2>
        {view.dialogue.length ? <ol className="mt-4 space-y-4">{view.dialogue.map((entry, index) => <li key={index} className="min-w-0 rounded border border-line p-4">
          <h3 className="break-all font-semibold">{entry.character_name}</h3><PlayText text={entry.text} />
          {entry.materials.length > 0 && <details className="mt-3"><summary className="cursor-pointer text-sm text-brass">查看本次回答引用的公开资料</summary><ul className="mt-3 space-y-3">{entry.materials.map((item, itemIndex) => <li key={itemIndex} className="min-w-0"><PlayText text={item.text} />{item.kind && <p className="mt-1 text-xs text-mist">{kindLabels[item.kind]}</p>}</li>)}</ul></details>}
        </li>)}</ol> : <p className="mt-3 text-sm text-mist">尚无已保存的角色答复。</p>}
      </section>}
      </>}
      {showFinale && <PlayEndingPanel key={pageScope} view={view} />}
    </article>}
    {view && <>
      {view.discussion && (!guided || page !== 'finale') && <aside onKeyDown={event => { if (event.key === 'Escape' && !event.defaultPrevented) { event.preventDefault(); setComposerOpen(false); event.currentTarget.querySelector<HTMLButtonElement>('button[aria-controls="play-composer"]')?.focus(); } }} aria-label="底部发言面板" className="fixed inset-x-0 bottom-0 z-40 mx-auto max-w-4xl border-t border-brass/40 bg-panel p-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] shadow-2xl md:rounded-t-2xl">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <button type="button" aria-expanded={composerOpen} aria-controls="play-composer" onClick={() => composerOpen ? setComposerOpen(false) : openComposer()} className="rounded-lg bg-brass px-4 py-2 text-sm font-semibold text-ink">{composerOpen ? '收起发言面板' : view.settled ? '查看角色回应' : singlePlayer ? '议题 / 发言' : '发言 / 提问'}</button>
          <a className="text-sm text-brass" href="#play-discussion" onClick={event => navigate(event, 'discussion')}>查看公共讨论</a>
          {composerMode === 'PUBLIC' && view.role_responses?.entries.length ? <a className="text-sm text-brass" href={`#play-message-${view.role_responses.entries.at(-1)!.id}`} onClick={event => navigate(event, 'discussion')}>最新回应</a> : null}
          {props.busy && <span role="status" className="text-xs text-brass">正在处理，请稍候…</span>}
        </div>
          <fieldset aria-label="发言模式" hidden={!composerOpen} className="mt-3 shrink-0 grid grid-cols-2 gap-2">
            <legend className="sr-only">发言模式</legend>
            {(['PUBLIC', 'PRIVATE'] as const).map(mode => <label key={mode} className="cursor-pointer">
              <input type="radio" name="play-composer-mode" value={mode} checked={composerMode === mode} onChange={() => { setComposerMode(mode); if (composerBody.current) composerBody.current.scrollTop = 0; }} className="peer sr-only" />
              <span className="flex min-h-11 items-center justify-center rounded-lg border border-line px-3 py-3 text-sm text-mist peer-checked:border-brass peer-checked:bg-brass/15 peer-checked:text-brass peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-brass">{mode === 'PUBLIC' ? '公开发言' : '单独对话'}</span>
            </label>)}
          </fieldset>
        <div id="play-composer" ref={composerBody} hidden={!composerOpen} className="mt-3 max-h-[42vh] overflow-y-auto overscroll-contain pr-1">
          {props.error && <div role="alert" className="mb-3 whitespace-pre-wrap break-words rounded border border-red-400/50 bg-panel p-3 text-sm leading-6 text-red-200"><p>{props.error}</p>{props.requiresRefresh && <button type="button" disabled={props.loading || props.busy} className="mt-3 min-h-11 rounded border border-brass/50 px-3 py-2 text-brass disabled:opacity-50" onClick={props.onReload}>刷新进度</button>}</div>}

          <div hidden={composerMode !== 'PUBLIC'} aria-label="公开发言模式">
<div>        {(!view.settled || props.retryingStatement) && <div className="mt-4 space-y-3">
          <label className="block text-sm">我的公开发言<textarea value={props.statement || ''} ref={composerInput} rows={2} maxLength={2000}
            disabled={locked} onChange={event => props.onStatementChange?.(event.target.value)}
            className="mt-2 w-full min-w-0 max-w-full rounded border border-line bg-ink p-3 text-paper" /></label>
          <PlayVoiceInput ownerId={props.ownerId} playId={view.play_id} phaseId={view.current_phase.id} revision={view.revision} channel="PUBLIC" active={composerOpen && composerMode === 'PUBLIC'} disabled={locked || props.retryingStatement || !props.onStatementChange} value={props.statement || ''} onChange={text => props.onStatementChange?.(text)} />
          <p className="text-xs text-mist">{Array.from((props.statement || '').trim()).length} / 1000 字符。{view.discussion.entries.length} / {view.discussion.limit} 条已保存。</p>
          {Array.from((props.statement || '').trim()).length > 1000 && <p role="alert" className="text-xs text-brass">发言超过 1000 个字符，请缩短后发送。</p>}
          {view.discussion.entries.length >= view.discussion.limit && <p role="status" className="text-xs text-brass">本次试玩已达到发言上限，已有记录仍可阅读。</p>}
          <button type="button" disabled={!canSpeak} onClick={() => { if (canSpeak) props.onSpeak?.(); }} className="rounded bg-brass px-4 py-3 text-sm font-semibold text-ink disabled:opacity-50">
            {props.retryingStatement ? '检查上次发言结果' : '发送公开发言'}</button>
          {singlePlayer && <p className="text-xs text-mist">可以直接发表你的看法；向角色询问时，请确认下方匹配的具体问题。</p>}
        </div>}
      </div>
{singlePlayer && <PlayTopicExchange key={`${pageScope}:public`} view={view} channel="PUBLIC" questionText={props.statement || latestHumanStatement(view)?.text || ''} locked={Boolean(locked || props.retryingQuestion || props.retryingResponse || props.retryingProposal || props.retryingStatement)} recoveryLocked={props.loading || props.busy} pendingReplyKey={props.pendingTopicReplyKey} onTopic={props.onTopic} onReply={props.onTopicReply} renderSpeech={speech.render} />}
      {!singlePlayer && view.role_responses && <section aria-label="角色回应" className="min-w-0 rounded border border-line bg-panel p-5">
        <h2 className="text-lg font-semibold">角色回应</h2>
        {naturalDialogue && view.ai_interactions && <p className="mt-3 text-sm leading-6 text-brass">本局 AI 互动：已发起 {view.ai_interactions.initiated} / 最多 {view.ai_interactions.limit} 轮。角色回应、调查建议和 AI 行动共用轮次；重看已保存结果不重复计数。</p>}
        <p className="mt-3 text-sm leading-7 text-mist">选择一位角色回应你刚发送的内容。回答会出现在公共讨论中。</p>
        {!view.role_responses.available && <p role="status" className="mt-3 text-sm text-brass">请先保存本阶段的发言。若当前无法继续互动，已有对话仍可阅读。</p>}
        {(!view.settled || props.retryingResponse) && <div className="mt-4 space-y-3">
          {replyTarget && <div aria-label="本次回应的发言" className="rounded border border-line p-3 text-sm"><p className="mb-2 text-mist">{props.retryingResponse ? '正在核对的原发言' : '你刚发送的内容'}</p><PlayText text={replyTarget.text} /></div>}
          {replyClarification && <p role="status" className="text-sm leading-6 text-brass">{replyClarification}</p>}
          {!props.retryingResponse && props.statement?.trim() && <p className="text-sm text-mist">请先发送当前草稿，再请角色回应。</p>}
          <div className="block text-sm">请谁回应<PlaySelect presentation="choices" label="请谁回应" value={props.responseCharacter || ''} disabled={locked || props.retryingResponse} onChange={id => props.onResponseCharacterChange?.(id)} placeholder="请选择 AI 角色" options={view.characters.filter(item => item.id !== view.selected_character_id).map(item => ({ value: item.id, label: item.name }))} /></div>
          <button type="button" disabled={!canRespond} onClick={() => { if (canRespond) props.onRespond?.(); }} className="rounded bg-brass px-4 py-3 text-sm font-semibold text-ink disabled:opacity-50">
            {props.retryingResponse ? '检查上次回应结果' : '请角色回应'}</button>
        </div>}
        <p className="mt-3 text-xs leading-6 text-mist">可在公共讨论中继续查看完整对话。</p>
      </section>}
          </div>
          <div hidden={composerMode !== 'PRIVATE'} aria-label="单独对话模式">
            {view.full_game ? <>
              {view.full_game.phase_kind !== 'INVESTIGATION' && !view.full_game.private_discussion.length && <p className="p-4 text-sm text-mist">进入调查阶段后，可以选择角色单独对话。</p>}
              <FullGamePanel key={`${pageScope}:private-composer`} ownerId={props.ownerId} view={view} locked={Boolean(locked)} onTable={props.onTable} onPrivateReply={props.onPrivateReply} onPhone={props.onPhone} onTopic={props.onTopic} onTopicReply={props.onTopicReply} pendingTopicReplyKey={props.pendingTopicReplyKey} recoveryLocked={props.loading || props.busy} renderSpeech={speech.render} section="private" />
            </> : <p className="p-4 text-sm text-mist">这份游戏暂不支持单独对话，可以在公开发言中与角色交流。</p>}
          </div>
        </div>
      </aside>}
    </>}
  </main>;
}

type PlayWorkspaceProps = { playId: string; openingSessionId: string; onCreated: (playId: string) => Promise<unknown> };
const latestFinaleReceipts = (view: PackagePlay) => (view.table_decisions?.options || []).filter(option => option.action === 'SEAL_FINALE').flatMap(option => {
  const latest = view.table_decisions!.requests.filter(receipt => receipt.action === 'SEAL_FINALE' && receipt.character_id === option.character_id)
    .sort((a, b) => b.revision - a.revision)[0];
  return latest ? [latest] : [];
});
const finaleRetryFingerprint = (view: PackagePlay) => JSON.stringify([view.play_id, view.package_hash, view.selected_character_id, view.revision,
  view.table_decisions?.options, latestFinaleReceipts(view)]);


export function PackagePlayWorkspace({ playId, openingSessionId, onCreated }: PlayWorkspaceProps) {
  const notebookOwner = useAuthStore(state => state.isAuthenticated && state.user ? String(state.user.id) : null);
  const [observedOwner, setObservedOwner] = useState<string | null>(null);
  const [view, setView] = useState<PackagePlay | null>();
  const [loading, setLoading] = useState(Boolean(playId || openingSessionId));
  const [busy, setBusy] = useState(false);
  const [requiresRefresh, setRequiresRefresh] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [finaleVotesProgress, setFinaleVotesProgress] = useState('');
  const [finaleRetryProof, setFinaleRetryProof] = useState<{ fingerprint: string; requestIds: string[] } | null>(null);
  const [checkingGuided, setCheckingGuided] = useState(false);
  const [reload, setReload] = useState(0);
  const [characterId, setCharacterId] = useState('');
  const [question, setQuestion] = useState('');
  const [retryingQuestion, setRetryingQuestion] = useState(false);
  const [statement, setStatement] = useState('');
  const [retryingStatement, setRetryingStatement] = useState(false);
  const [proposalCharacter, setProposalCharacter] = useState('');
  const [retryingProposal, setRetryingProposal] = useState(false);
  const [responseCharacter, setResponseCharacter] = useState('');
  const [responseTarget, setResponseTarget] = useState('');
  const [retryingResponse, setRetryingResponse] = useState(false);
  const [checkingTopic, setCheckingTopic] = useState(false);
  const [pendingTopicReplyKey, setPendingTopicReplyKey] = useState<string>();
  const managed = useRef<{
    active?: AbortController;
    observed?: PackagePlay;
    attempts: Partial<Record<'create' | 'action', PackagePlayAttempt>>;
    ask?: { signature: string; request: PackagePlayAskRequest; rebaseAfterRead?: boolean };
    speak?: { signature: string; request: PackagePlaySpeakRequest };
    propose?: PackagePlayProposalRequest;
    respond?: PackagePlayRespondRequest;
    table?: FullTableRequest;
    decide?: FullDecisionRequest;
    privateReply?: FullPrivateReplyRequest;
    phone?: FullPhoneRequest | FullPhonePauseRequest;
    guided?: GuidedRequest;
    topic?: TopicRequest;
    topicReply?: PackagePlayRespondRequest | FullPrivateReplyRequest;
    topicReplyTurnId?: string;
  }>({ attempts: {} });

  useEffect(() => {
    const guard = watchPackagePlayAuth(() => {
      managed.current.active?.abort(); managed.current = { attempts: {} }; setCheckingTopic(false); setPendingTopicReplyKey(undefined);
      setView(undefined); setLoading(false); setBusy(false); setRequiresRefresh(true);
      setQuestion(''); setCharacterId(''); setRetryingQuestion(false);
      setStatement(''); setRetryingStatement(false); setProposalCharacter(''); setRetryingProposal(false);
      setResponseCharacter(''); setResponseTarget(''); setRetryingResponse(false); setNotice(''); setFinaleVotesProgress(''); setFinaleRetryProof(null); setCheckingGuided(false);
      setError('登录身份已变化，请重新打开试玩。');
    });
    return guard.dispose;
  }, []);

  useEffect(() => {
    if (!playId && !openingSessionId) return;
    const requests = managed.current;
    const readOwner = notebookOwner;
    const controller = new AbortController(); requests.active?.abort(); requests.active = controller;
    const read = playId ? packagePlayService.read(playId, controller.signal) : packagePlayService.lookup(openingSessionId, controller.signal);
    read.then(result => {
      if (controller.signal.aborted) return;
      if (result !== null && (!matchesPlayRoute(result, playId, openingSessionId)
        || (requests.observed && (!samePlayBinding(requests.observed, result) || result.revision < requests.observed.revision)))) {
        throw new PackagePlayError(409, '读取的试玩与已保存角色、版本或操作次数不一致，已保留原有材料。请刷新进度核对。');
      }
      if (result === null && (playId || requests.observed)) throw new PackagePlayError(409, '未找到已保存的试玩，已保留原有材料。请刷新进度核对。');
      if (result) {
        requests.observed = result;
        if (requests.topic && confirmsTopic(result, requests.topic)) { requests.topic = undefined; setCheckingTopic(false); }
        if (requests.topicReply) {
          const original = requests.topicReply;
          const savedTurn = result.single_player?.turns.find(turn => turn.id === requests.topicReplyTurnId
            && turn.reply_to === original.reply_to && turn.character_id === original.character_id
            && turn.channel === (original.schema_version === 'package-dialogue-command/1.0' ? 'PUBLIC' : 'PRIVATE'));
          const ledger = original.schema_version === 'package-dialogue-command/1.0' ? result.role_responses?.requests : result.private_replies?.requests;
          const receipt = ledger?.find(item => item.request_id === original.idempotency_key);
          const originalReceipt = receipt && receipt.revision === original.expected_revision && receipt.character_id === original.character_id && receipt.reply_to === original.reply_to;
          const terminalReply = savedTurn && ['OK', 'FAILED'].includes(savedTurn.status) && sameTopicReply(savedTurn.reply_request, original)
            && originalReceipt && receipt.status !== 'PENDING';
          const fallbackMessages = savedTurn?.channel === 'PUBLIC' ? result.discussion?.entries
            : result.full_game?.private_discussion.filter(message => message.audience.includes(original.character_id)
              && message.call_id === result.full_game?.private_discussion.find(question => question.id === original.reply_to)?.call_id);
          const recordedFallback = typeof savedTurn?.answer === 'string' && fallbackMessages?.some(message => message.speaker === original.character_id
            && message.text === savedTurn.answer && message.phase_id === savedTurn.phase_id && message.sequence > original.expected_revision);
          const savedFallback = savedTurn?.status === 'FALLBACK' && savedTurn.reply_request === null && result.revision > original.expected_revision
            && (receipt ? originalReceipt && !['OK', 'PENDING'].includes(receipt.status) : Array.isArray(ledger) && recordedFallback);
          const expiredReady = savedTurn?.status === 'READY' && (savedTurn.reply_request === null || savedTurn.can_fallback) && !receipt
            && result.revision >= original.expected_revision;
          if (terminalReply || savedFallback || expiredReady) {
            requests.topicReply = undefined; requests.topicReplyTurnId = undefined; setPendingTopicReplyKey(undefined);
          }
        }
        const pendingTopic = result.single_player?.turns.find(turn => turn.status === 'PENDING' && turn.reply_request);
        if (pendingTopic?.reply_request && !requests.topicReply) {
          requests.topicReply = pendingTopic.reply_request; requests.topicReplyTurnId = pendingTopic.id; setPendingTopicReplyKey(pendingTopic.reply_request.idempotency_key);
        }
        if (requests.guided && confirmsGuided(result, requests.guided)) {
          requests.guided = undefined; setCheckingGuided(false);
        }
        if (result.full_game && result.table_commands && requests.table) {
          const receipt = result.table_commands.find(r => r.request_id === requests.table!.idempotency_key);
          if (receipt && (receipt.revision !== requests.table.expected_revision || receipt.action !== requests.table.action))
            throw new PackagePlayError(409, '上次操作与保存回执不一致，请重新读取核对。');
          // GET contains the complete committed table-command ledger. Absence
          // means this key has no committed action at the observed revision.
          requests.table = undefined;
        }
        if (requests.decide && result.table_decisions) {
          const receipt = result.table_decisions.requests.find(r => r.request_id === requests.decide!.idempotency_key);
          if (receipt && (receipt.revision !== requests.decide.expected_revision || receipt.character_id !== requests.decide.character_id || receipt.action !== requests.decide.action))
            throw new PackagePlayError(409, '角色决定与保存回执不一致，请重新读取核对。');
          if (!receipt || receipt.status !== 'PENDING') requests.decide = undefined;
        }
        if (requests.privateReply && result.private_replies) {
          const receipt = result.private_replies.requests.find(r => r.request_id === requests.privateReply!.idempotency_key);
          if (receipt && (receipt.revision !== requests.privateReply.expected_revision || receipt.character_id !== requests.privateReply.character_id || receipt.reply_to !== requests.privateReply.reply_to))
            throw new PackagePlayError(409, '私聊回复与保存回执不一致，请重新读取核对。');
          if (!receipt || receipt.status !== 'PENDING') requests.privateReply = undefined;
        }
        const pendingDecision = result.table_decisions?.requests.find(r => r.status === 'PENDING');
        if (requests.phone) {
          const receipt = requests.phone.action === 'PHONE_STEP'
            ? result.phone_turns?.requests.find(r => r.request_id === requests.phone!.idempotency_key)
            : result.table_commands?.find(r => r.request_id === requests.phone!.idempotency_key && r.action === 'PAUSE_PHONE');
          if (receipt && receipt.revision !== requests.phone.expected_revision) throw new PackagePlayError(409, '电话回执不一致，请重新打开试玩。');
          if (!receipt || !('status' in receipt) || receipt.status !== 'PENDING') requests.phone = undefined;
        }
        const pendingPhone = result.phone_turns?.requests.find(r => r.status === 'PENDING');
        if (pendingPhone && !requests.phone) requests.phone = { schema_version: 'package-phone-command/1.0', action: 'PHONE_STEP',
          expected_revision: pendingPhone.revision, idempotency_key: pendingPhone.request_id };
        if (pendingDecision && !requests.decide) requests.decide = {
          schema_version: 'package-table-decision-command/1.0', expected_revision: pendingDecision.revision,
          idempotency_key: pendingDecision.request_id, character_id: pendingDecision.character_id, action: pendingDecision.action,
        };
        const pendingPrivate = result.private_replies?.requests.find(r => r.status === 'PENDING' && !topicForRequest(result, r.request_id));
        if (pendingPrivate && !requests.privateReply) requests.privateReply = {
          schema_version: 'package-private-dialogue-command/1.0', action: 'RESPOND_PRIVATE',
          expected_revision: pendingPrivate.revision, idempotency_key: pendingPrivate.request_id,
          character_id: pendingPrivate.character_id, reply_to: pendingPrivate.reply_to,
        };
        const pending = result.investigation_proposals?.requests.find(item => item.status === 'PENDING');
        if (pending && !requests.propose) {
          requests.propose = { schema_version: 'package-investigation-command/1.0', action: 'PROPOSE',
            expected_revision: pending.revision, idempotency_key: pending.request_id, character_id: pending.character_id };
          setProposalCharacter(pending.character_id); setRetryingProposal(true);
        }
        const pendingResponse = result.role_responses?.requests.find(item => item.status === 'PENDING' && !topicForRequest(result, item.request_id));
        if (pendingResponse && !requests.respond) {
          requests.respond = { schema_version: 'package-dialogue-command/1.0', action: 'RESPOND',
            expected_revision: pendingResponse.revision, idempotency_key: pendingResponse.request_id,
            character_id: pendingResponse.character_id, reply_to: pendingResponse.reply_to };
          setResponseCharacter(pendingResponse.character_id); setResponseTarget(pendingResponse.reply_to); setRetryingResponse(true);
        }
        if (requests.ask?.rebaseAfterRead) {
          requests.ask.request = { ...requests.ask.request, expected_revision: result.revision };
          delete requests.ask.rebaseAfterRead;
        }
      }
      setObservedOwner(readOwner);
      setView(result); setRequiresRefresh(false); setError('');
    }).catch(cause => {
      if (!controller.signal.aborted) {
        if (cause instanceof PackagePlayError && [401, 403, 404].includes(cause.status)) { requests.observed = undefined; requests.ask = undefined; requests.speak = undefined; requests.propose = undefined; requests.respond = undefined; requests.table = undefined; requests.decide = undefined; requests.privateReply = undefined; requests.phone = undefined; requests.guided = undefined; requests.topic = undefined; requests.topicReply = undefined; requests.topicReplyTurnId = undefined; setCheckingTopic(false); setPendingTopicReplyKey(undefined); setCheckingGuided(false); setResponseCharacter(''); setResponseTarget(''); setRetryingResponse(false); setProposalCharacter(''); setRetryingProposal(false); setStatement(''); setRetryingStatement(false); setQuestion(''); setCharacterId(''); setRetryingQuestion(false); }
        setView(requests.observed);
        setRequiresRefresh(true); setError(cause instanceof PackagePlayError ? cause.message : '读取试玩结果失败，请刷新后核对。');
      }
    }).finally(() => { if (!controller.signal.aborted) { requests.active = undefined; setLoading(false); } });
    return () => controller.abort();
  }, [playId, openingSessionId, reload, notebookOwner]);

  useEffect(() => {
    return () => managed.current.active?.abort();
  }, []);

  const failure = (cause: unknown) => {
    setError(cause instanceof PackagePlayError ? cause.message : '保存失败，请重试；相同操作会复用本次请求标识。');
    if (cause instanceof PackagePlayError && cause.status === 409) setRequiresRefresh(true);
    if (cause instanceof PackagePlayError && [401, 403, 404].includes(cause.status)) {
      managed.current.observed = undefined; managed.current.ask = undefined; managed.current.speak = undefined; managed.current.propose = undefined; managed.current.respond = undefined; managed.current.table = undefined; managed.current.decide = undefined; managed.current.privateReply = undefined; managed.current.phone = undefined; managed.current.guided = undefined; managed.current.topic = undefined; managed.current.topicReply = undefined; managed.current.topicReplyTurnId = undefined; setCheckingTopic(false); setPendingTopicReplyKey(undefined); setCheckingGuided(false); setResponseCharacter(''); setResponseTarget(''); setRetryingResponse(false); setProposalCharacter(''); setRetryingProposal(false); setStatement(''); setRetryingStatement(false); setQuestion(''); setCharacterId(''); setRetryingQuestion(false); setRequiresRefresh(true); setView(undefined);
    }
  };

  const create = async () => {
    const requests = managed.current;
    if (requests.active || requests.guided || requests.topic || requests.topicReply || loading || requiresRefresh || view !== null || playId || !openingSessionId) return;
    const payload = { opening_session_id: openingSessionId };
    const attempt = preparePackagePlayAttempt(payload, requests.attempts.create); requests.attempts.create = attempt;
    const controller = new AbortController(); requests.active = controller;
    setBusy(true); setError(''); setNotice('');
    try {
      const result = await packagePlayService.create({ ...payload, idempotency_key: attempt.key }, controller.signal);
      if (controller.signal.aborted) return;
      if (!matchesPlayRoute(result, '', openingSessionId)) throw new PackagePlayError(409, '返回的试玩与当前开场不一致，请刷新进度核对。');
      delete requests.attempts.create; setObservedOwner(notebookOwner); requests.observed = result; setView(result); setNotice('文字试玩已开始，原开场记录保持不变。');
      try { await onCreated(result.play_id); }
      catch { if (!controller.signal.aborted) setError('试玩已保存，页面地址更新失败。请刷新进度恢复已有记录。'); }
    } catch (cause) { if (!controller.signal.aborted) failure(cause); }
    finally { if (!controller.signal.aborted) { requests.active = undefined; setBusy(false); } }
  };

  const act = async (action: PackagePlayAction) => {
    const requests = managed.current;
    if (requests.active || requests.guided || requests.topic || requests.topicReply || loading || requiresRefresh || !view || !matchesPlayRoute(view, playId, openingSessionId) || !canPerformPlayAction(view, action)) return;
    const payload = { ...action, expected_revision: view.revision };
    const attempt = preparePackagePlayAttempt({ play_id: view.play_id, ...payload }, requests.attempts.action); requests.attempts.action = attempt;
    const controller = new AbortController(); requests.active = controller;
    setBusy(true); setError(''); setNotice('');
    try {
      const result = await packagePlayService.act(view.play_id, { ...payload, idempotency_key: attempt.key }, controller.signal);
      if (controller.signal.aborted) return;
      if (!matchesPlayRoute(result, playId, openingSessionId) || !samePlayBinding(view, result)) throw new PackagePlayError(409, '返回的试玩版本不一致，请刷新进度核对。');
      delete requests.attempts.action;
      if (result.revision >= (requests.observed?.revision ?? view.revision)) { requests.observed = result; setView(result); }
      setNotice(action.action === 'ADVANCE_PHASE' ? '阶段推进已保存。' : action.action === 'SETTLE' ? '结尾已揭晓，记录保留供阅读。'
        : action.action === 'PERFORM_ACTION' ? '搜证结果已保存，行动点和已解锁材料已更新。' : '材料公开已保存。');
      try {
        const refreshed = await packagePlayService.read(view.play_id, controller.signal);
        if (!controller.signal.aborted) {
          if (!matchesPlayRoute(refreshed, playId, openingSessionId) || !samePlayBinding(view, refreshed)
            || refreshed.revision < Math.max(requests.observed?.revision ?? view.revision, result.revision)) throw new Error();
          requests.observed = refreshed; setView(refreshed);
        }
      } catch (cause) {
        if (!controller.signal.aborted) {
          if (cause instanceof PackagePlayError && [401, 403, 404].includes(cause.status)) failure(cause);
          else { setRequiresRefresh(true); setError('操作已保存，但刷新失败。请点击“刷新进度”核对最新结果。'); }
        }
      }
    } catch (cause) { if (!controller.signal.aborted) failure(cause); }
    finally { if (!controller.signal.aborted) { requests.active = undefined; setBusy(false); } }
  };

  const ask = async () => {
    const requests = managed.current;
    if (requests.active || requests.guided || requests.topic || requests.topicReply || loading || requiresRefresh || !view || !matchesPlayRoute(view, playId, openingSessionId)) return;
    const signature = JSON.stringify({ play_id: view.play_id, character_id: characterId, question: question.trim() });
    const previous = requests.ask?.signature === signature ? requests.ask : undefined;
    if (!canAskPackageCharacter(view, characterId, question, Boolean(previous))) return;
    // Retain the entire original request across refreshes after a failed response.
    // A changed observed revision must not turn a retry into a second model call.
    const request = previous?.request ?? { character_id: characterId, question: question.trim(), expected_revision: view.revision, idempotency_key: crypto.randomUUID() };
    requests.ask = { signature, request };
    const controller = new AbortController(); requests.active = controller;
    setBusy(true); setError(''); setNotice(''); setRetryingQuestion(true);
    try {
      const result = await packagePlayService.ask(view.play_id, request, controller.signal);
      if (controller.signal.aborted) return;
      if (!matchesPlayRoute(result, playId, openingSessionId) || !samePlayBinding(view, result)) throw new PackagePlayError(409, '返回的试玩版本不一致，请刷新进度核对。');
      if (result.revision >= (requests.observed?.revision ?? view.revision)) { requests.observed = result; setView(result); }
      if (!result.pending_ai) { requests.ask = undefined; setRetryingQuestion(false); setQuestion(''); }
      setNotice(result.pending_ai ? '提问已受理，尚未确认结果。请刷新或检查上次提问结果。' : '本次提问状态已保存，请查看角色答复和状态说明。');
      try {
        const refreshed = await packagePlayService.read(view.play_id, controller.signal);
        if (!controller.signal.aborted) {
          if (!matchesPlayRoute(refreshed, playId, openingSessionId) || !samePlayBinding(view, refreshed)
            || refreshed.revision < Math.max(requests.observed?.revision ?? view.revision, result.revision)) throw new Error();
          requests.observed = refreshed; setView(refreshed);
          if (!refreshed.pending_ai && result.pending_ai) { requests.ask = undefined; setRetryingQuestion(false); setQuestion(''); }
        }
      } catch (cause) {
        if (!controller.signal.aborted) {
          if (cause instanceof PackagePlayError && [401, 403, 404].includes(cause.status)) failure(cause);
          else { setRequiresRefresh(true); setError('提问状态已保存，但刷新失败。请点击“刷新进度”核对最新结果。'); }
        }
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        if (cause instanceof PackagePlayError && cause.rejectedBeforeDispatch) { requests.ask = undefined; setRetryingQuestion(false); }
        if (cause instanceof PackagePlayError && cause.revisionConflict && requests.ask) requests.ask.rebaseAfterRead = true;
        failure(cause);
      }
    }
    finally { if (!controller.signal.aborted) { requests.active = undefined; setBusy(false); } }
  };

  const speak = async () => {
    const requests = managed.current;
    if (requests.active || requests.guided || requests.topic || requests.topicReply || loading || requiresRefresh || !view || !matchesPlayRoute(view, playId, openingSessionId)) return;
    const signature = JSON.stringify({ play_id: view.play_id, text: statement.trim() });
    const previous = requests.speak?.signature === signature ? requests.speak : undefined;
    if (!canSpeakInPlay(view, statement, Boolean(previous))) return;
    const request: PackagePlaySpeakRequest = previous?.request ?? {
      schema_version: 'package-discussion-command/1.0', action: 'SPEAK',
      expected_revision: view.revision, idempotency_key: crypto.randomUUID(), text: statement.trim(),
    };
    requests.speak = { signature, request };
    const controller = new AbortController(); requests.active = controller;
    setBusy(true); setError(''); setNotice(''); setRetryingStatement(true);
    try {
      const result = await packagePlayService.speak(view.play_id, request, controller.signal);
      if (controller.signal.aborted) return;
      if (!matchesPlayRoute(result, playId, openingSessionId) || !samePlayBinding(view, result)
        || result.revision < (requests.observed?.revision ?? view.revision)
        || !result.discussion?.entries.some(item => item.sequence === request.expected_revision + 1
          && item.speaker === view.selected_character_id && item.text === request.text)) {
        throw new PackagePlayError(409, '尚未确认发言已保存，请刷新进度后检查上次发言结果。');
      }
      requests.observed = result; requests.speak = undefined;
      setView(result); setStatement(''); setRetryingStatement(false); setNotice('公开发言已保存。');
      if (!requests.respond) setResponseTarget(`statement-${request.expected_revision + 1}`);
    } catch (cause) {
      if (!controller.signal.aborted) {
        // Only an explicit server revision conflict proves this request did not
        // append. Unknown outcomes retain the exact key and original revision.
        if (cause instanceof PackagePlayError && cause.revisionConflict) { requests.speak = undefined; setRetryingStatement(false); }
        failure(cause);
      }
    } finally { if (!controller.signal.aborted) { requests.active = undefined; setBusy(false); } }
  };

  const propose = async () => {
    const requests = managed.current;
    if (requests.active || requests.guided || requests.topic || requests.topicReply || loading || requiresRefresh || !view || !matchesPlayRoute(view, playId, openingSessionId)) return;
    const previous = requests.propose?.character_id === proposalCharacter ? requests.propose : undefined;
    if (!canRequestProposal(view, proposalCharacter, Boolean(previous))) return;
    const request: PackagePlayProposalRequest = previous ?? { schema_version: 'package-investigation-command/1.0', action: 'PROPOSE',
      character_id: proposalCharacter, expected_revision: view.revision, idempotency_key: crypto.randomUUID() };
    requests.propose = request;
    const controller = new AbortController(); requests.active = controller;
    setBusy(true); setError(''); setNotice(''); setRetryingProposal(true);
    try {
      const result = await packagePlayService.propose(view.play_id, request, controller.signal);
      if (controller.signal.aborted) return;
      const receipt = result.investigation_proposals?.requests.find(item => item.request_id === request.idempotency_key);
      if (!matchesPlayRoute(result, playId, openingSessionId) || !samePlayBinding(view, result)
        || result.revision < (requests.observed?.revision ?? view.revision)
        || !receipt || receipt.character_id !== request.character_id || receipt.revision !== request.expected_revision
        || (receipt.status === 'OK' && !result.investigation_proposals?.entries.some(entry =>
          entry.sequence === request.expected_revision + 2 && entry.speaker === request.character_id))) {
        throw new PackagePlayError(409, '尚未确认本次建议的结果，请刷新后检查。');
      }
      requests.observed = result; setView(result);
      if (receipt.status !== 'PENDING') { requests.propose = undefined; setRetryingProposal(false); }
      setNotice(receipt.status === 'OK' ? '调查建议已保存；搜证尚未执行。'
        : receipt.status === 'PENDING' ? '建议请求已受理，可刷新或检查上次结果。'
        : '本次未保存新的调查建议。原请求不会自动重发，可以查看最新状态后另行征求建议。');
    } catch (cause) {
      if (!controller.signal.aborted) {
        if (cause instanceof PackagePlayError && cause.revisionConflict) { requests.propose = undefined; setRetryingProposal(false); }
        failure(cause);
      }
    } finally { if (!controller.signal.aborted) { requests.active = undefined; setBusy(false); } }
  };

  const respond = async () => {
    const requests = managed.current;
    if (requests.active || requests.guided || requests.topic || requests.topicReply || loading || requiresRefresh || !view || !matchesPlayRoute(view, playId, openingSessionId)) return;
    const previous = requests.respond;
    const character = previous?.character_id || responseCharacter;
    const target = previous?.reply_to || latestHumanStatement(view)?.id || '';
    if (!previous && (statement.trim() || questionRefusal(latestHumanStatement(view)?.text || '') || questionClarification(latestHumanStatement(view)?.text || ''))) return;
    if (!canRequestResponse(view, character, target, Boolean(previous))) return;
    const request: PackagePlayRespondRequest = previous ?? { schema_version: 'package-dialogue-command/1.0', action: 'RESPOND',
      character_id: character, reply_to: target, expected_revision: view.revision, idempotency_key: crypto.randomUUID() };
    requests.respond = request;
    const controller = new AbortController(); requests.active = controller;
    setBusy(true); setError(''); setNotice(''); setRetryingResponse(true);
    try {
      const result = await packagePlayService.respond(view.play_id, request, controller.signal);
      if (controller.signal.aborted) return;
      const receipt = result.role_responses?.requests.find(item => item.request_id === request.idempotency_key);
      if (!matchesPlayRoute(result, playId, openingSessionId) || !samePlayBinding(view, result)
        || result.revision < (requests.observed?.revision ?? view.revision)
        || !receipt || receipt.character_id !== request.character_id || receipt.reply_to !== request.reply_to
        || receipt.revision !== request.expected_revision
        || (receipt.status === 'OK' && !result.role_responses?.entries.some(entry =>
          entry.sequence === request.expected_revision + 2 && entry.speaker === request.character_id && entry.reply_to === request.reply_to))) {
        throw new PackagePlayError(409, '尚未确认本次角色回应，请刷新后检查上次结果。');
      }
      requests.observed = result; setView(result);
      if (receipt.status !== 'PENDING') { requests.respond = undefined; setRetryingResponse(false); }
      setNotice(receipt.status === 'OK' ? '角色回应已保存。'
        : receipt.status === 'PENDING' ? '正在等待角色回应，可刷新或检查上次结果。'
        : '这次没有保存新的回应。原请求不会自动重发，已有对话和回忆仍保留。');
    } catch (cause) {
      if (!controller.signal.aborted) {
        if (cause instanceof PackagePlayError && (cause.revisionConflict || cause.rejectedBeforeDispatch)) { requests.respond = undefined; setRetryingResponse(false); }
        failure(cause);
      }
    } finally { if (!controller.signal.aborted) { requests.active = undefined; setBusy(false); } }
  };

  const phoneCommand = async (operation: 'STEP' | 'PAUSE') => {
    const requests = managed.current;
    if (requests.active || requests.guided || requests.topic || requests.topicReply || loading || requiresRefresh || !view?.phone_turns || view.settled
      || !matchesPlayRoute(view,playId,openingSessionId)) return;
    const action = operation === 'STEP' ? 'PHONE_STEP' : 'PAUSE_PHONE';
    const pending = view.phone_turns.requests.find(r => r.status === 'PENDING');
    if (view.single_player && operation === 'STEP' && !pending && !requests.phone) return;
    if (requests.phone && requests.phone.action !== action && operation !== 'PAUSE') {
      setError('上次电话操作尚未确认，请先刷新核对。'); return;
    }
    if (operation === 'PAUSE' ? !view.phone_turns.can_pause
      : (!pending && !requests.phone && (!view.phone_turns.available || view.pending_ai))) return;
    const request = requests.phone?.action === action ? requests.phone : operation === 'STEP' && pending
      ? { schema_version:'package-phone-command/1.0', action:'PHONE_STEP', expected_revision:pending.revision,idempotency_key:pending.request_id } as FullPhoneRequest : {
      schema_version: operation === 'STEP' ? 'package-phone-command/1.0' : 'package-phone-pause-command/1.0',
      action, expected_revision:view.revision, idempotency_key:preparePackagePlayAttempt({action}).key,
    } as FullPhoneRequest | FullPhonePauseRequest;
    requests.phone=request;
    const controller=new AbortController();requests.active=controller;setBusy(true);setError('');setNotice('');
    try {
      const result=request.action==='PHONE_STEP'
        ? await packagePlayService.phoneStep(view.play_id,request,controller.signal)
        : await packagePlayService.pausePhone(view.play_id,request,controller.signal);
      if (controller.signal.aborted) return;
      const receipt=request.action==='PHONE_STEP'
        ? result.phone_turns?.requests.find(r=>r.request_id===request.idempotency_key)
        : result.table_commands?.find(r=>r.request_id===request.idempotency_key && r.action==='PAUSE_PHONE');
      if (!matchesPlayRoute(result,playId,openingSessionId) || !samePlayBinding(view,result)
        || result.revision<(requests.observed?.revision ?? view.revision) || !receipt || receipt.revision!==request.expected_revision)
        throw new PackagePlayError(409,'电话操作尚未确认，请刷新核对。');
      if (!('status' in receipt) || receipt.status!=='PENDING') requests.phone=undefined;
      requests.observed=result;setView(result);setNotice('电话进度已保存。');
    } catch (cause) {
      if (!controller.signal.aborted) {
        if (cause instanceof PackagePlayError && (cause.revisionConflict || cause.rejectedBeforeDispatch)) requests.phone=undefined;
        failure(cause);
      }
    } finally { if (!controller.signal.aborted) { requests.active=undefined;setBusy(false); } }
  };

  // A topic saves a canonical human question first. Only the server-issued
  // frozen request may then call a character, including every explicit retry.
  const executeTopicReply = async (current: PackagePlay, turn: TopicTurn, controller: AbortController) => {
    const requests = managed.current;
    const request = turn.reply_request;
    if (!request || controller.signal.aborted) return;
    if (requests.topicReply && !sameTopicReply(requests.topicReply, request))
      throw new PackagePlayError(409, '上次议题回答尚未核对，请先查看原请求。');
    requests.topicReply = request; requests.topicReplyTurnId = turn.id; setPendingTopicReplyKey(request.idempotency_key);
    const result = request.schema_version === 'package-dialogue-command/1.0'
      ? await packagePlayService.respond(current.play_id, request, controller.signal)
      : await packagePlayService.replyPrivate(current.play_id, request, controller.signal);
    if (controller.signal.aborted) return;
    const receipt = request.schema_version === 'package-dialogue-command/1.0'
      ? result.role_responses?.requests.find(item => item.request_id === request.idempotency_key)
      : result.private_replies?.requests.find(item => item.request_id === request.idempotency_key);
    const saved = topicForRequest(result, request.idempotency_key);
    if (!matchesPlayRoute(result, playId, openingSessionId) || !samePlayBinding(current, result)
      || result.revision < (requests.observed?.revision ?? current.revision) || !receipt || !saved
      || receipt.revision !== request.expected_revision || receipt.character_id !== request.character_id || receipt.reply_to !== request.reply_to
      || saved.id !== turn.id || !sameTopicReply(saved.reply_request, request))
      throw new PackagePlayError(409, '尚未确认这次议题回答，请核对原请求。');
    requests.observed = result; setView(result); setRequiresRefresh(false);
    if (receipt.status !== 'PENDING') { requests.topicReply = undefined; requests.topicReplyTurnId = undefined; setPendingTopicReplyKey(undefined); }
    setNotice(receipt.status === 'OK' ? '议题回答已保存，可结合线索继续判断。'
      : receipt.status === 'PENDING' ? '问题已保存，角色回答仍在处理中。可核对原请求。'
      : '问题已保存，本次角色回答未完成。可查看该议题固定答复。');
  };

  const topicReply = async (turnId: string) => {
    const requests = managed.current;
    if (requests.active || requests.topic || requests.guided || loading || !view || !matchesPlayRoute(view, playId, openingSessionId)) return;
    const turn = view.single_player?.turns.find(item => item.id === turnId);
    if (!turn?.reply_request) return;
    const checking = requests.topicReply?.idempotency_key === turn.reply_request.idempotency_key || turn.status === 'PENDING';
    if ((!checking && (turn.can_fallback || requests.ask || requests.respond || requests.propose || requests.privateReply || requests.decide || requests.phone || requests.table || requests.speak
      || requiresRefresh || view.pending_ai || view.settled || !view.single_player?.available || turn.status !== 'READY'
      || turn.phase_id !== view.current_phase.id || (turn.channel === 'PRIVATE' && !view.full_game?.call?.character_ids.includes(turn.character_id)))) || (requests.topicReply && !checking)) return;
    const controller = new AbortController(); requests.active = controller;
    setBusy(true); setError(''); setNotice('');
    try { await executeTopicReply(view, turn, controller); }
    catch (cause) {
      if (!controller.signal.aborted) {
        if (cause instanceof PackagePlayError && (cause.rejectedBeforeDispatch || cause.revisionConflict)) { requests.topicReply = undefined; requests.topicReplyTurnId = undefined; setPendingTopicReplyKey(undefined); }
        setRequiresRefresh(true); failure(cause);
      }
    } finally { if (!controller.signal.aborted) { requests.active = undefined; setBusy(false); } }
  };

  const topicCommand = async (action?: TopicAction) => {
    const requests = managed.current;
    if (requests.active || requests.guided || requests.topicReply || requests.ask || requests.respond || requests.propose || requests.privateReply
      || requests.decide || requests.phone || requests.table || requests.speak || loading || !view || !matchesPlayRoute(view, playId, openingSessionId)) return;
    if (action && (requiresRefresh || requests.topic || !canTopic(view, action))) return;
    const request: TopicRequest | undefined = action ? { ...action, schema_version: 'package-topic-command/1.0',
      expected_revision: view.revision, idempotency_key: preparePackagePlayAttempt(action).key } : requests.topic;
    if (!request) return;
    // Checking a lost ASK result never automatically starts a fresh AI call.
    const automatic = Boolean(action && request.action === 'ASK_TOPIC');
    requests.topic = request; setCheckingTopic(true);
    const controller = new AbortController(); requests.active = controller; setBusy(true); setError(''); setNotice('');
    try {
      const result = await packagePlayService.topic(view.play_id, request, controller.signal);
      if (controller.signal.aborted) return;
      if (!matchesPlayRoute(result, playId, openingSessionId) || !samePlayBinding(view, result)
        || result.revision < (requests.observed?.revision ?? view.revision) || !confirmsTopic(result, request))
        throw new PackagePlayError(409, '议题操作尚未确认，请检查上次议题操作。');
      requests.topic = undefined; setCheckingTopic(false); requests.observed = result; setView(result); setRequiresRefresh(false);
      setNotice(request.action === 'USE_FALLBACK' ? '该议题固定答复已保存。' : '所选问题已保存。');
      if (automatic && request.action === 'ASK_TOPIC' && result.single_player?.available && !result.pending_ai && !result.settled) {
        const payload = request.payload;
        const turn = result.single_player.turns.find(item => item.topic_id === payload.topic_id && item.character_id === payload.character_id
          && item.intent_id === payload.intent_id && item.channel === payload.channel && item.phase_id === result.current_phase.id
          && item.status === 'READY' && !item.can_fallback && item.reply_request?.expected_revision === result.revision);
        if (turn) await executeTopicReply(result, turn, controller);
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        if (requests.topicReply && cause instanceof PackagePlayError && (cause.revisionConflict || cause.rejectedBeforeDispatch)) {
          requests.topicReply = undefined; requests.topicReplyTurnId = undefined; setPendingTopicReplyKey(undefined);
        }
        if (requests.topic && cause instanceof PackagePlayError && (cause.revisionConflict || cause.rejectedBeforeDispatch)) {
          requests.topic = undefined; setCheckingTopic(false);
        }
        setRequiresRefresh(true); failure(cause);
      }
    } finally { if (!controller.signal.aborted) { requests.active = undefined; setBusy(false); } }
  };

  const guidedCommand = async (action?: GuidedAction) => {
    const requests = managed.current;
    if (requests.active || requests.topic || requests.topicReply || loading || !view || !matchesPlayRoute(view, playId, openingSessionId)) return;
    if (action && (requiresRefresh || requests.guided || !canGuide(view, action))) return;
    if (action && (requests.speak || requests.table || requests.decide || requests.phone || requests.ask || requests.respond || requests.propose || requests.privateReply)) {
      setRequiresRefresh(true); setError(requests.speak ? '上一项公开发言还未确认。请先点击“刷新进度”，再点击“检查上次发言结果”，之后可继续调查。' : '上一项操作的结果还未确认，请点击“刷新进度”核对；若仍有待处理请求，请使用对应的“检查上次结果”入口，再继续调查。'); return;
    }
    const request = action ? { ...action, schema_version: 'package-guided-command/1.0' as const,
      expected_revision: view.revision, idempotency_key: preparePackagePlayAttempt(action).key } : requests.guided;
    if (!request) return;
    requests.guided = request; setCheckingGuided(true);
    const controller = new AbortController(); requests.active = controller; setBusy(true); setError(''); setNotice('');
    try {
      const result = await packagePlayService.guided(view.play_id, request, controller.signal);
      if (controller.signal.aborted) return;
      if (!matchesPlayRoute(result, playId, openingSessionId) || !samePlayBinding(view, result)
        || result.revision < (requests.observed?.revision ?? view.revision) || !confirmsGuided(result, request)) throw new PackagePlayError(409, '流程结果尚未确认，请检查上次流程结果。');
      requests.guided = undefined; setCheckingGuided(false); requests.observed = result; setView(result); setRequiresRefresh(false);
      setNotice(request.action === 'REQUEST_HINT' ? '所选提示已保存，可随时重新阅读。' : '当前步骤已更新。');
    } catch (cause) {
      if (!controller.signal.aborted) {
        if (cause instanceof PackagePlayError && (cause.revisionConflict || cause.rejectedBeforeDispatch)) { requests.guided = undefined; setCheckingGuided(false); }
        else setRequiresRefresh(true);
        failure(cause);
      }
    } finally { if (!controller.signal.aborted) { requests.active = undefined; setBusy(false); } }
  };

  const finaleVotes = async (confirmedRetry = false) => {
    const requests = managed.current;
    if (requests.active || requests.guided || requests.topic || requests.topicReply || loading || requiresRefresh || !view || !matchesPlayRoute(view, playId, openingSessionId)
      || view.full_game?.phase_kind !== 'FINALE' || view.settled || view.pending_ai || requests.decide) return;
    const controller = new AbortController(); requests.active = controller;
    setBusy(true); setError(''); setNotice(''); setFinaleVotesProgress('正在请其他玩家提交…');
    let current = view;
    const visited = new Set<string>();
    const acknowledged = confirmedRetry && finaleRetryProof?.fingerprint === finaleRetryFingerprint(view) ? new Set(finaleRetryProof.requestIds) : new Set<string>();
    setFinaleRetryProof(null);
    try {
      const uncertain = latestFinaleReceipts(current).filter(receipt => ['UNKNOWN', 'EXPIRED'].includes(receipt.status) && !acknowledged.has(receipt.request_id));
      if (uncertain.length) {
        const result = await packagePlayService.read(current.play_id, controller.signal);
        if (controller.signal.aborted) return;
        if (!matchesPlayRoute(result, playId, openingSessionId) || !samePlayBinding(current, result) || result.revision < current.revision)
          throw new PackagePlayError(409, '尚未确认未提交角色的状态，请刷新核对。');
        requests.observed = result; current = result; setView(result);
        if (result.pending_ai || result.table_decisions?.requests.some(receipt => receipt.status === 'PENDING')) {
          setNotice('仍有一条提交正在处理。请先核对原请求，不能重新提交。'); setFinaleVotesProgress('等待原请求核对'); return;
        }
        const remaining = latestFinaleReceipts(result).filter(receipt => ['UNKNOWN', 'EXPIRED'].includes(receipt.status));
        if (remaining.length) {
          setFinaleRetryProof({ fingerprint: finaleRetryFingerprint(result), requestIds: remaining.map(receipt => receipt.request_id) });
          setNotice('已核对：原请求未取得可用结果且已停止等待。再次确认后，会为每个尚未提交的角色发起一次新互动；已提交结果保留。');
          setFinaleVotesProgress('等待你确认重新请求');
        } else { setNotice('角色状态已更新。请查看当前提交状态，再决定是否继续。'); setFinaleVotesProgress('已完成状态核对'); }
        return;
      }
      while (!controller.signal.aborted && visited.size < current.characters.length - 1) {
        if (current.settled || current.pending_ai || !current.table_decisions?.available || current.full_game?.phase_kind !== 'FINALE') break;
        // Only a latest terminal receipt explicitly acknowledged after a GET
        // may be replaced by a new attempt. Pending requests keep their key.
        const blocked = latestFinaleReceipts(current).some(receipt => receipt.status === 'PENDING'
          || (['UNKNOWN', 'EXPIRED'].includes(receipt.status) && !acknowledged.has(receipt.request_id)));
        if (blocked) { setNotice('有一条投票结果待核对，已停止连续提交。请先刷新并核对该角色的状态。'); break; }
        const option = current.table_decisions.options.find(o => o.action === 'SEAL_FINALE' && !visited.has(o.character_id));
        if (!option) break;
        visited.add(option.character_id);
        const request: FullDecisionRequest = { schema_version: 'package-table-decision-command/1.0', action: 'SEAL_FINALE',
          character_id: option.character_id, expected_revision: current.revision, idempotency_key: preparePackagePlayAttempt(option).key };
        requests.decide = request;
        const result = await packagePlayService.decide(current.play_id, request, controller.signal);
        if (controller.signal.aborted) return;
        const receipt = result.table_decisions?.requests.find(r => r.request_id === request.idempotency_key);
        if (!matchesPlayRoute(result, playId, openingSessionId) || !samePlayBinding(current, result) || result.revision < current.revision
          || !receipt || receipt.revision !== request.expected_revision || receipt.character_id !== request.character_id || receipt.action !== 'SEAL_FINALE')
          throw new PackagePlayError(409, '投票保存结果尚未确认，已停止连续提交。请刷新核对。');
        if (receipt.status !== 'PENDING') requests.decide = undefined;
        requests.observed = result; current = result; setView(result);
        setFinaleVotesProgress(`已处理 ${visited.size} 位其他玩家`);
        if (receipt.status !== 'OK') { setNotice('这位角色的提交没有完成，已停止连续提交。请核对状态后再操作。'); break; }
      }
      if (!controller.signal.aborted && current.full_game?.finale?.all_sealed) setNotice('所有玩家均已封卷，可以揭晓结果。');
    } catch (cause) {
      if (!controller.signal.aborted) { setRequiresRefresh(true); failure(cause); setFinaleVotesProgress('已停止，请先核对上次投票结果。'); }
    } finally { if (!controller.signal.aborted) { requests.active = undefined; setBusy(false); } }
  };

  const fullCommand = async (action?: FullTableAction, actor?: string, operation?: FullDecisionAction, replyTo?: string): Promise<boolean> => {
    const requests = managed.current;
    if (requests.active || requests.guided || requests.topic || requests.topicReply || loading || requiresRefresh || !view || !view.full_game
      || !matchesPlayRoute(view, playId, openingSessionId)) return false;
    const isDecision = Boolean(actor && operation);
    const isPrivate = Boolean(actor && replyTo);
    let request: FullTableRequest | FullDecisionRequest | FullPrivateReplyRequest;
    if (isPrivate) {
      const pending = view.private_replies?.requests.find(r => r.status === 'PENDING' && !topicForRequest(view, r.request_id));
      if (view.single_player && !pending && !requests.privateReply) return false;
      if (pending && (pending.character_id !== actor || pending.reply_to !== replyTo)) return false;
      if (!pending && (!view.private_replies?.available || view.pending_ai || view.settled
        || !view.private_replies.options.some(o => o.character_id === actor && o.reply_to === replyTo))) return false;
      request = requests.privateReply ?? { schema_version: 'package-private-dialogue-command/1.0', action: 'RESPOND_PRIVATE',
        expected_revision: pending?.revision ?? view.revision, idempotency_key: pending?.request_id ?? preparePackagePlayAttempt({ actor, replyTo }).key,
        character_id: actor!, reply_to: replyTo! };
      if (request.character_id !== actor || request.reply_to !== replyTo) {
        setError('上次私聊还未核对，请先检查上次请求或刷新进度。'); return false;
      }
      requests.privateReply = request;
    } else if (isDecision) {
      const pending = view.table_decisions?.requests.find(r => r.status === 'PENDING');
      if (view.single_player && operation !== 'SEAL_FINALE' && !pending && !requests.decide) return false;
      if (pending && (pending.character_id !== actor || pending.action !== operation)) return false;
      if (!pending && (!view.table_decisions?.available || view.pending_ai || view.settled
        || !view.table_decisions.options.some(o => o.character_id === actor && o.action === operation))) return false;
      request = requests.decide ?? { schema_version: 'package-table-decision-command/1.0',
        expected_revision: pending?.revision ?? view.revision, idempotency_key: pending?.request_id ?? preparePackagePlayAttempt({ actor, operation }).key,
        character_id: actor!, action: operation! };
      if (request.character_id !== actor || request.action !== operation) {
        setError('上次角色决定还未核对，请先检查上次请求或刷新进度。'); return false;
      }
      requests.decide = request;
    } else {
      if (!action || view.settled || view.pending_ai || (view.single_player && action.action === 'PRIVATE_SPEAK')) return false;
      const previous = requests.table;
      if (previous && JSON.stringify({ action: previous.action, payload: 'payload' in previous ? previous.payload : undefined })
        !== JSON.stringify(action)) {
        setError('上次操作尚未确认，请先重试同一操作并核对保存结果。'); return false;
      }
      request = previous ?? { ...action, schema_version: 'package-full-play-command/1.0',
        expected_revision: view.revision, idempotency_key: preparePackagePlayAttempt(action).key };
      requests.table = request;
    }
    const controller = new AbortController(); requests.active = controller;
    setBusy(true); setError(''); setNotice('');
    let saved = false;
    let automaticReply = false;
    try {
      const result = request.schema_version === 'package-private-dialogue-command/1.0'
        ? await packagePlayService.replyPrivate(view.play_id, request, controller.signal)
        : request.schema_version === 'package-table-decision-command/1.0'
        ? await packagePlayService.decide(view.play_id, request, controller.signal)
        : await packagePlayService.table(view.play_id, request, controller.signal);
      if (controller.signal.aborted) return false;
      if (!matchesPlayRoute(result, playId, openingSessionId) || !samePlayBinding(view, result)
        || result.revision < (requests.observed?.revision ?? view.revision)) throw new PackagePlayError(409, '返回的整局记录不一致，请刷新核对。');
      if (isPrivate) {
        const receipt = result.private_replies?.requests.find(r => r.request_id === request.idempotency_key);
        if (!receipt || receipt.character_id !== actor || receipt.reply_to !== replyTo || receipt.revision !== request.expected_revision)
          throw new PackagePlayError(409, '尚未确认这次私聊，请刷新并检查上次请求。');
        if (receipt.status !== 'PENDING') requests.privateReply = undefined;
      } else if (isDecision) {
        const receipt = result.table_decisions?.requests.find(r => r.request_id === request.idempotency_key);
        if (!receipt || receipt.character_id !== actor || receipt.action !== operation || receipt.revision !== request.expected_revision)
          throw new PackagePlayError(409, '尚未确认这次角色决定，请刷新并检查上次请求。');
        if (receipt.status !== 'PENDING') requests.decide = undefined;
      } else {
        if (action?.action === 'PRIVATE_SPEAK') {
          const receipt = result.table_commands?.find(r => r.request_id === request.idempotency_key);
          if (!receipt || receipt.action !== 'PRIVATE_SPEAK' || receipt.revision !== request.expected_revision)
            throw new PackagePlayError(409, '你的私聊保存结果尚未确认，请刷新核对。');
        }
        requests.table = undefined;
      }
      requests.observed = result; setView(result);
      saved = true;
      setNotice(isDecision || isPrivate ? '已更新角色提交状态，请查看结果。' : '操作已保存。');
      if (!result.single_player && !isDecision && !isPrivate && action?.action === 'PRIVATE_SPEAK' && result.full_game?.call
        && !result.pending_ai && result.private_replies?.available && !requests.privateReply) {
        const call = result.full_game.call;
        const message = result.full_game.private_discussion.findLast(m => m.speaker === result.selected_character_id
          && m.call_id === call.id && m.sequence > request.expected_revision && m.text === action.payload.text);
        const option = message && !result.private_replies.requests.some(r => r.reply_to === message.id)
          && result.private_replies.options.find(o => o.reply_to === message.id && call.character_ids.includes(o.character_id));
        if (option) {
          automaticReply = true;
          const reply: FullPrivateReplyRequest = { schema_version: 'package-private-dialogue-command/1.0', action: 'RESPOND_PRIVATE',
            character_id: option.character_id, reply_to: option.reply_to, expected_revision: result.revision,
            idempotency_key: preparePackagePlayAttempt(option).key };
          requests.privateReply = reply;
          const response = await packagePlayService.replyPrivate(result.play_id, reply, controller.signal);
          if (controller.signal.aborted) return false;
          const receipt = response.private_replies?.requests.find(r => r.request_id === reply.idempotency_key);
          if (!matchesPlayRoute(response, playId, openingSessionId) || !samePlayBinding(result, response) || response.revision < result.revision
            || !receipt || receipt.character_id !== reply.character_id || receipt.reply_to !== reply.reply_to || receipt.revision !== reply.expected_revision)
            throw new PackagePlayError(409, '你的私聊已保存，对方回应尚未确认。请刷新核对。');
          if (receipt.status !== 'PENDING') requests.privateReply = undefined;
          requests.observed = response; setView(response);
          setNotice(receipt.status === 'OK' ? '私聊与对方回应已保存。' : '你的私聊已保存，对方暂未完成回应。可核对状态后重试。');
        }
      }
    } catch (cause) {
      if (!controller.signal.aborted) {
        if (cause instanceof PackagePlayError && (cause.revisionConflict || cause.rejectedBeforeDispatch)) {
          if (isPrivate || automaticReply) requests.privateReply = undefined; else if (isDecision) requests.decide = undefined; else requests.table = undefined;
        }
        if (automaticReply && !(cause instanceof PackagePlayError && cause.rejectedBeforeDispatch)) setRequiresRefresh(true);
        failure(cause);
      }
    } finally { if (!controller.signal.aborted) { requests.active = undefined; setBusy(false); } }
    return saved;
  };

  return <PackagePlayPanel ownerId={observedOwner === notebookOwner ? notebookOwner : null} playId={playId} openingSessionId={openingSessionId} view={view} loading={loading} busy={busy} requiresRefresh={requiresRefresh || checkingGuided || checkingTopic} error={error} notice={notice}
    onTopic={action => void topicCommand(action)} onCheckTopic={checkingTopic ? () => void topicCommand() : undefined}
    onTopicReply={turnId => void topicReply(turnId)} pendingTopicReplyKey={pendingTopicReplyKey}
    onTable={action => fullCommand(action)}
    onGuided={action => void guidedCommand(action)} onCheckGuided={checkingGuided ? () => void guidedCommand() : undefined}
    onFinaleVotes={confirmedRetry => void finaleVotes(confirmedRetry)} finaleVotesNeedsConfirmation={Boolean(view && finaleRetryProof?.fingerprint === finaleRetryFingerprint(view))} finaleVotesProgress={finaleVotesProgress} onDecide={(actor, action) => void fullCommand(undefined, actor, action)}
    onPrivateReply={(actor, target) => void fullCommand(undefined, actor, undefined, target)}
    onPhone={action => void phoneCommand(action)}
    responseCharacter={responseCharacter} responseTarget={responseTarget} retryingResponse={retryingResponse} onRespond={respond}
    onResponseCharacterChange={id => { if (!managed.current.active && !managed.current.respond) setResponseCharacter(id); }}
    onResponseTargetChange={id => { if (!managed.current.active && !managed.current.respond) setResponseTarget(id); }}
    proposalCharacter={proposalCharacter} retryingProposal={retryingProposal} onPropose={propose}
    onProposalCharacterChange={id => { if (!managed.current.active) { managed.current.propose = undefined; setRetryingProposal(false); setProposalCharacter(id); } }}
    statement={statement} retryingStatement={retryingStatement} onSpeak={speak}
    onStatementChange={value => { if (!managed.current.active) { managed.current.speak = undefined; setRetryingStatement(false); setStatement(value); } }}
    characterId={characterId} question={question} retryingQuestion={retryingQuestion}
    onCharacterChange={id => { if (!managed.current.active) { managed.current.ask = undefined; setRetryingQuestion(false); setCharacterId(id); } }}
    onQuestionChange={value => { if (!managed.current.active) { managed.current.ask = undefined; setRetryingQuestion(false); setQuestion(value); } }}
    onReload={() => { if (!managed.current.active) { setLoading(Boolean(playId || openingSessionId)); setError(''); setNotice(''); setReload(value => value + 1); } }} onCreate={create} onAction={act} onAsk={ask} />;
}
