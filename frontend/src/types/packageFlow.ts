export type FlowMaterialCollection = 'knowledge' | 'evidence';

export type FlowMaterial = {
  id: string;
  text: string;
  disclosure: 'PUBLIC' | 'MAY_SHARE' | 'MUST_SHARE' | 'KEEP_PRIVATE';
  kind?: 'FACT' | 'CLAIM' | 'INFERENCE';
  can_share: boolean;
  shared_by_character_id?: string;
};

export type PackageFlow = {
  flow_id: string;
  opening_session_id: string;
  release_id: number;
  version_id: number;
  package_hash: string;
  selected_character_id: string;
  revision: number;
  runtime_ready: false;
  status: 'RULES_PREVIEW';
  script: { title: string; content_version: string; player_count: number };
  characters: { id: string; name: string }[];
  introduction: { text: string };
  current_phase: { id: string; title: string };
  can_advance: boolean;
  phase_complete: boolean;
  public_knowledge: FlowMaterial[];
  private_knowledge: FlowMaterial[];
  public_evidence: FlowMaterial[];
  private_evidence: FlowMaterial[];
};

export type CreatePackageFlowRequest = { opening_session_id: string; idempotency_key: string };
export type FlowMaterialTarget = { collection: FlowMaterialCollection; id: string };
export type PackageFlowAction = { action: 'ADVANCE_PHASE' } | { action: 'SHARE_MATERIAL'; target: FlowMaterialTarget };
export type PackageFlowActionRequest = PackageFlowAction & { idempotency_key: string; expected_revision: number };
