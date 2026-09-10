export type PlayMaterialCollection = 'knowledge' | 'evidence';

export type PlayMaterial = {
  id: string;
  text: string;
  disclosure: 'PUBLIC' | 'MAY_SHARE' | 'MUST_SHARE' | 'KEEP_PRIVATE';
  kind?: 'FACT' | 'CLAIM' | 'INFERENCE';
  can_share: boolean;
  retelling?: 'MAY_RETELL' | 'MUST_RETELL';
  shared_by_character_id?: string;
};

export type PackagePlayMechanics = {
  remaining_points: number;
  initial_points: number;
  spent_points: number;
  can_finish_phase: boolean;
  available_actions: { id: string; label: string; cost: number }[];
};

export type PackagePlay = {
  post_game_qa?: { schema_version: 'package-post-game-qa/1.0'; questions: { id: string; title: string; text: string }[] };
  single_player?: SinglePlayerView;
  round_workspace?: RoundWorkspace;
  guided_play?: { schema_version: 'package-guided-play/1.0'; available: boolean; can_investigate: boolean;
    can_investigate_round?: boolean; can_finish_investigation: boolean; has_legacy_ballot: boolean; can_present_required: boolean;
    last_command?: { idempotency_key: string; action: GuidedAction['action']; sequence: number } | null };
  host_hints?: { schema_version: 'package-host-hints/1.0';
    topics: { id: string; title: string; phase_id: string; max_level: 3 }[];
    entries: { topic_id: string; title: string; phase_id: string; level: 1 | 2 | 3; text: string; sequence: number }[] };
  ai_interactions?: { schema_version: 'package-ai-interactions/1.0'; initiated: number; limit: number };
  visuals?: { id: string; collection: 'knowledge' | 'evidence' | 'memory'; material_id: string; label: string }[];
  table_commands?: { request_id: string; revision: number; action: FullTableAction['action'] | 'PAUSE_PHONE' }[];
  private_replies?: {
    schema_version: 'package-private-dialogue-view/1.0'; available: boolean;
    options: { character_id: string; reply_to: string }[];
    requests: { request_id: string; character_id: string; reply_to: string; revision: number;
      status: 'PENDING' | 'OK' | 'INVALID' | 'UNKNOWN' | 'STALE' | 'EXPIRED' }[];
  };
  full_game?: FullGameView;
  phone_turns?: { schema_version: 'package-phone-view/1.0'; available: boolean; can_pause: boolean;
    requests: { request_id: string; revision: number; status: 'PENDING' | 'OK' | 'INVALID' | 'UNKNOWN' | 'STALE' | 'EXPIRED' }[] };
  table_decisions?: {
    schema_version: 'package-table-decision-view/1.0'; available: boolean; reason: string | null;
    options: { character_id: string; action: FullDecisionAction }[];
    requests: { request_id: string; character_id: string; action: FullDecisionAction; revision: number;
      status: 'PENDING' | 'OK' | 'INVALID' | 'UNKNOWN' | 'STALE' | 'EXPIRED' }[];
  };
  play_id: string;
  opening_session_id: string;
  release_id: number;
  version_id: number;
  package_hash: string;
  selected_character_id: string;
  revision: number;
  runtime_ready: false;
  status: 'TEXT_PLAY' | 'SETTLED';
  script: { title: string; content_version: string; player_count: number };
  characters: { id: string; name: string }[];
  introduction: { text: string };
  current_phase: { id: string; title: string };
  can_advance: boolean;
  phase_complete: boolean;
  mechanics?: PackagePlayMechanics;
  public_knowledge: PlayMaterial[];
  reading_supplements?: { id: string; text: string }[];
  private_knowledge: PlayMaterial[];
  public_evidence: PlayMaterial[];
  private_evidence: PlayMaterial[];
  settled: boolean;
  settlement: { text: string; truths: { id: string; text: string }[] } | null;
  dialogue: {
    character_id: string;
    character_name: string;
    text: string;
    materials: { collection: PlayMaterialCollection; id: string; text: string; kind?: 'FACT' | 'CLAIM' | 'INFERENCE' }[];
  }[];
  model: { available: boolean; reason: string | null };
  role_responses?: {
    schema_version: 'package-dialogue-view/1.0'; available: boolean; reason: string | null;
    character_ids: string[]; reply_target_ids: string[];
    requests: { request_id: string; character_id: string; reply_to: string; revision: number;
      status: 'PENDING' | 'OK' | 'INVALID' | 'UNKNOWN' | 'STALE' | 'EXPIRED' }[];
    entries: { id: string; sequence: number; phase_id: string; speaker: string; reply_to: string;
      kind: 'CLAIM'; text: string; modes: ('REPORT' | 'INFERENCE' | 'QUESTION' | 'UNCERTAIN')[] }[];
  };
  investigation_proposals?: {
    schema_version: 'package-proposal-view/1.0'; available: boolean; reason: string | null; character_ids: string[];
    requests: { request_id: string; character_id: string; revision: number;
      status: 'PENDING' | 'OK' | 'INVALID' | 'UNKNOWN' | 'STALE' | 'EXPIRED' }[];
    entries: {
      id: string; sequence: number; phase_id: string; speaker: string; kind: 'CLAIM'; text: string;
      action: { id: string; label: string; cost: number };
      basis: { collection: 'knowledge' | 'evidence' | 'discussion'; id: string; text: string;
        kind: 'FACT' | 'CLAIM' | 'INFERENCE'; speaker?: string; sequence?: number }[];
    }[];
  };
  memories?: {
    schema_version: 'package-memory-view/1.0';
    entries: { id: string; character_id: string; title: string; text: string;
      kind: 'FACT' | 'CLAIM' | 'INFERENCE'; card_disclosure: 'KEEP_PRIVATE';
      retelling: 'MAY_RETELL' | 'MUST_RETELL'; sequence: number; phase_id: string;
      cause: { kind: 'OTHER_PUBLIC_SPEECH'; speaker: string } | { kind: 'OTHER_HEARD_SPEECH'; speaker: string; channel: 'PUBLIC' | 'PRIVATE' } | { kind: 'ACQUIRED_EVIDENCE'; evidence_id: string };
    }[];
  };
  discussion?: {
    schema_version: 'package-discussion-view/1.0';
    limit: number;
    entries: { id: string; sequence: number; phase_id: string; kind: 'CLAIM'; speaker: string; text: string }[];
  };
  budget: {
    token_limit: number;
    used_tokens: number;
    reserved_tokens: number;
    cost_limit_cny: string | null;
    used_cost_cny: string;
    reserved_cost_cny: string;
  };
  pending_ai: boolean;
  last_ai_status: 'OK' | 'INVALID' | 'UNKNOWN' | 'STALE' | 'EXPIRED' | null;
};

export type CreatePackagePlayRequest = { opening_session_id: string; idempotency_key: string };
export type GuidedAction = { action: 'INVESTIGATE'; payload: { action_id: string } }
  | { action: 'INVESTIGATE_ROUND'; payload: { action_ids: string[] } }
  | { action: 'FINISH_INVESTIGATION' | 'PRESENT_REQUIRED' }
  | { action: 'REQUEST_HINT'; payload: { topic_id: string; level: 1 | 2 | 3 } };
export type GuidedRequest = GuidedAction & { schema_version: 'package-guided-command/1.0'; expected_revision: number; idempotency_key: string };
export type TopicAction = { action: 'ASK_TOPIC'; payload: { topic_id: string; character_id: string; intent_id: string; channel: 'PUBLIC' | 'PRIVATE' } }
  | { action: 'USE_FALLBACK'; payload: { turn_id: string } };
export type TopicRequest = TopicAction & { schema_version: 'package-topic-command/1.0'; expected_revision: number; idempotency_key: string };
export type TopicTurn = {
  id: string; topic_id: string; title: string; phase_id: string; character_id: string; channel: 'PUBLIC' | 'PRIVATE';
  intent_id: string; question: string; status: 'READY' | 'PENDING' | 'OK' | 'FAILED' | 'FALLBACK';
  reply_to: string; reply_request: PackagePlayRespondRequest | FullPrivateReplyRequest | null;
  can_fallback: boolean; answer?: string;
  receipt_status?: 'OK' | 'INVALID' | 'UNKNOWN' | 'STALE' | 'EXPIRED' | 'PENDING';
};
export type SinglePlayerView = {
  schema_version: 'package-single-player/1.0'; available: boolean;
  stage: { phase_id: string; goal: string; instructions: string[]; completion: string } | null;
  operation_rules: string;
  topics: { id: string; title: string; responders: { character_id: string; channels: ('PUBLIC' | 'PRIVATE')[];
    intents: { id: string; label: string; question: string; available: boolean }[] }[] }[];
  turns: TopicTurn[];
  last_command: { idempotency_key: string; action: TopicAction['action']; sequence: number } | null;
};
export type PlayMaterialTarget = { collection: PlayMaterialCollection; id: string };
export type PackagePlayAction = { action: 'ADVANCE_PHASE' | 'SETTLE' }
  | { action: 'SHARE_MATERIAL'; target: PlayMaterialTarget }
  | { action: 'PERFORM_ACTION'; target: { action_id: string } };
export type PackagePlayActionRequest = PackagePlayAction & { idempotency_key: string; expected_revision: number };
export type PackagePlayAskRequest = { idempotency_key: string; expected_revision: number; character_id: string; question: string };
export type PackagePlaySpeakRequest = {
  schema_version: 'package-discussion-command/1.0'; action: 'SPEAK';
  idempotency_key: string; expected_revision: number; text: string;
};
export type PackagePlayProposalRequest = {
  schema_version: 'package-investigation-command/1.0'; action: 'PROPOSE';
  idempotency_key: string; expected_revision: number; character_id: string;
};

export type PackagePlayRespondRequest = {
  schema_version: 'package-dialogue-command/1.0'; action: 'RESPOND';
  idempotency_key: string; expected_revision: number; character_id: string; reply_to: string;
};

export type FullDecisionAction = 'CAST_BALLOT' | 'BREAK_TIE' | 'SEAL_FINALE';
export type FullBallot = { kind: 'CHOOSE' | 'ABSTAIN' | 'SKIP'; choice_id: string | null };
export type FullSubmission = {
  schema_version: 'structured-finale-submission/1.0';
  answers: { question_id: string; option_ids: string[] }[];
  vote: { accusation_id: string | null; trust_character_id: string | null };
  reflection: string;
};
export type FullTableAction = { action: 'OPEN_BALLOT' | 'STOP_CALL' }
  | { action: 'CAST_BALLOT'; payload: FullBallot }
  | { action: 'BREAK_TIE'; payload: { choice_id: string } }
  | { action: 'START_CALL'; payload: { peer_character_id: string } }
  | { action: 'PRIVATE_SPEAK'; payload: { text: string } }
  | { action: 'SEAL_FINALE'; payload: FullSubmission };
export type FullTableRequest = FullTableAction & {
  schema_version: 'package-full-play-command/1.0'; expected_revision: number; idempotency_key: string;
};
export type FullDecisionRequest = {
  schema_version: 'package-table-decision-command/1.0'; expected_revision: number; idempotency_key: string;
  character_id: string; action: FullDecisionAction;
};
export type FullPrivateReplyRequest = {
  schema_version: 'package-private-dialogue-command/1.0'; expected_revision: number; idempotency_key: string;
  character_id: string; action: 'RESPOND_PRIVATE'; reply_to: string;
};
export type FullPhoneRequest = { schema_version: 'package-phone-command/1.0'; action: 'PHONE_STEP'; expected_revision: number; idempotency_key: string };
export type FullPhonePauseRequest = { schema_version: 'package-phone-pause-command/1.0'; action: 'PAUSE_PHONE'; expected_revision: number; idempotency_key: string };
export type FullGameView = {
  schema_version: 'full-game-view/1.0'; phase_kind: 'READING' | 'INVESTIGATION' | 'FINALE';
  can_open_ballot: boolean; phone_busy: boolean;
  call: { id: string; character_ids: string[] } | null;
  private_discussion: { id: string; sequence: number; phase_id: string; speaker: string; text: string;
    kind: 'CLAIM'; call_id: string; audience: string[] }[];
  ballot: { schema_version: 'collective-round-view/1.0'; decider: string; sealed_count: number; required_count: number;
    choices: { id: string; label: string; cost: number }[];
    ballot: FullBallot | null; status: 'WAITING' | 'TIE' | 'CHOSEN' | 'SKIPPED'; choice_id: string | null; tied_choice_ids: string[] } | null;
  finale: {
    schema_version: 'structured-finale-view/1.0'; sealed: boolean; all_sealed: boolean; submission: FullSubmission | null;
    questions: { id: string; prompt: string; options: { id: string; label: string }[]; max_choices: number }[];
    votes: { schema_version: 'finale-vote-view/1.0'; sealed_count: number; required_count: number;
      ballot: FullSubmission['vote'] | null; accusation_options: { id: string; label: string }[]; trust_character_ids: string[] };
  } | null;
  result: {
    totals: { character_id: string; total_points: number | null; known_points: number; max_points: number }[];
    goals: { id: string; character_id: string; title: string; points: number | null; max_points: number;
      parts: { id: string; points: number | null; max_points: number; explanation: string | null; status: 'ASSESSED' | 'UNASSESSED' }[] }[];
    endings: { character_ids: string[]; ending_id: string | null; texts: string[] }[];
  } | null;
};

export type RoundWorkspace = {
  schema_version: 'package-round-workspace/1.0';
  phases: { phase_id: string; title: string; kind: 'READING' | 'INVESTIGATION' | 'FINALE';
    materials: { collection: 'knowledge' | 'evidence' | 'memory'; id: string; sequence: number }[];
    investigations: { action_id: string; label: string; cost: number; character_id: string; mode: 'PLAYER' | 'AUTO' | 'LEGACY'; sequence: number; materials: { collection: 'knowledge' | 'evidence'; id: string }[] }[];
    statement_ids: string[]; private_message_ids: string[];
  }[];
};
