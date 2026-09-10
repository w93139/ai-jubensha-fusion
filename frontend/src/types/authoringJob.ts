import type { ScriptAuditReport } from '@/types/scriptReview';

export type AuthoringJobState = 'QUEUED' | 'RUNNING' | 'NEEDS_RECONCILIATION' | 'BLOCKED' | 'COMPLETED' | 'CANCELLED';
export type AuthoringStep = 'COMPILE' | 'CANDIDATE' | 'AUDIT' | 'DONE';
export type AuthoringSourceReference = { source_id: string; page?: number | null; anchor?: string | null };
export type AuthoringOutputDiagnostic = {
  code: 'TEXT_NOT_IN_MATERIALS' | 'TEXT_OUTSIDE_REFERENCES' | 'INPUT_BINDING_MISMATCH'
    | 'PACKAGE_SCHEMA_INVALID' | 'PACKAGE_DUPLICATE_ID' | 'PACKAGE_SOURCE_NOT_FOUND'
    | 'PACKAGE_SOURCE_PAGE_OUT_OF_RANGE' | 'PACKAGE_SOURCE_LOCATION_MISSING'
    | 'PACKAGE_DUPLICATE_SOURCE_PATH' | 'PACKAGE_ORIGINAL_SOURCE_MISSING' | 'PACKAGE_INVALID_SOURCE_LINK'
    | 'PACKAGE_CHARACTER_COUNT_MISMATCH' | 'PACKAGE_PHASE_NOT_FOUND' | 'PACKAGE_PHASE_CYCLE'
    | 'PACKAGE_UNREACHABLE_PHASE' | 'PACKAGE_INVALID_SETTLEMENT_PHASE' | 'PACKAGE_DUPLICATE_REFERENCE'
    | 'PACKAGE_TRUTH_NOT_FOUND' | 'PACKAGE_INVALID_PUBLIC_SCOPE' | 'PACKAGE_INVALID_PRIVATE_SCOPE'
    | 'PACKAGE_EVIDENCE_NOT_FOUND' | 'PACKAGE_UNSATISFIABLE_RELEASE' | 'PACKAGE_UNREACHABLE_EVIDENCE'
    | 'PACKAGE_INITIAL_KNOWLEDGE_MISSING'
    | 'AUDIT_CANDIDATE_INVALID' | 'AUDIT_COVERAGE_INVALID' | 'AUDIT_DUPLICATE_FINDING_ID'
    | 'AUDIT_CITATION_SCHEMA_INVALID' | 'AUDIT_CITATION_REFERENCE_INVALID' | 'AUDIT_BOUNDED_SCHEMA_INVALID' | 'AUDIT_TARGET_INVALID' | 'AUDIT_TARGET_NOT_FOUND' | 'AUDIT_REFERENCE_MISMATCH' | 'AUDIT_SOURCE_INDEX_UNAVAILABLE' | 'AUDIT_SCHEMA_INVALID';
  entity_path: string;
};
export type CompilerOutput = {
  status: 'CANDIDATE' | 'BLOCKED';
  package?: Record<string, unknown> | null;
  blockers: { code: string; message: string; sources: AuthoringSourceReference[] }[];
};

export type AuthoringAttempt = {
  id: number;
  step: 'COMPILE' | 'AUDIT';
  status: 'RESERVED' | 'IN_FLIGHT' | 'SUCCEEDED' | 'FAILED' | 'UNKNOWN';
  prepared: Record<string, unknown>;
  output: CompilerOutput | ScriptAuditReport | null;
  receipt: (Record<string, unknown> & { output_diagnostics?: AuthoringOutputDiagnostic[] }) | null;
  error_code: string | null;
  output_hash: string | null;
};

export type AuthoringJob = {
  id: number;
  title: string;
  bundle_hash: string;
  source_ids: string[];
  content_version: string;
  player_count: number;
  state: AuthoringJobState;
  step: AuthoringStep;
  revision: number;
  created_at: string;
  updated_at: string;
  candidate_version_id: number | null;
  source_report_hash: string | null;
  error_code: string | null;
  publication_ready: false;
  model_snapshot: { provider: string; model: string; pricing_version: string; [key: string]: unknown };
  attempts: AuthoringAttempt[];
  charged_cost_cny: string;
};

export type AuthoringJobDraft = {
  title: string; content_version: string; player_count: number; source_ids: string[];
  package_contract?: 'script-package/1.2';
  rule_plan?: Record<string, unknown> | null;
  compiler_mode?: 'CONFIRM_FROZEN_TEXT';
  audit_mode?: 'TARGET_SOURCE_INDEXES' | 'BOUNDED_TARGET_SOURCE_INDEXES' | 'DIRECT_BOUNDED_SOURCE_INDEXES' | 'STRICT_BOUNDED_SOURCE_INDEXES' | 'PORTABLE_STRICT_SOURCE_INDEXES' | 'TYPED_STRICT_SOURCE_INDEXES' | 'RUNTIME_CONTEXT_SOURCE_INDEXES' | 'CITATION_CATALOG';
};
export type CreateAuthoringJobRequest = AuthoringJobDraft & { idempotency_key: string; bundle_hash: string };
