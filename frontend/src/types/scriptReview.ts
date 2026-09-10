export type ReviewCategory = 'PROVENANCE' | 'TIMELINE' | 'EVIDENCE' | 'KNOWLEDGE_BOUNDARY' | 'PLAYABILITY';
export type FindingStatus = 'OPEN' | 'ACKNOWLEDGED' | 'DISMISSED';

export type ReviewCandidate = {
  id: number;
  script_key: string;
  content_version: string;
  title: string;
  package_hash: string;
  contract_version: string;
};

export type ReviewFinding = {
  id: string;
  category: ReviewCategory;
  severity: 'BLOCKER' | 'WARNING' | 'INFO';
  target: {
    collection: 'introduction' | 'settlement' | 'characters' | 'phases' | 'knowledge' | 'evidence' | 'truth'
      | 'mechanics.actions' | 'mechanics.phase_budgets' | 'memories';
    id: string | null;
  };
  message: string;
  sources: { source_id: string; page?: number | null; anchor?: string | null }[];
};

export type ScriptAuditReport = {
  schema_version: 'script-audit/1.0' | 'script-audit/1.1' | 'script-audit/1.2';
  summary: string;
  coverage: ReviewCategory[];
  findings: ReviewFinding[];
};

export type FindingDisposition = {
  id: number;
  revision: number;
  finding_id: string;
  status: FindingStatus;
  note: string;
  submitted_by: number;
};

export type ScriptAudit = {
  id: number;
  audit_hash: string;
  version_id: number;
  package_hash: string;
  bundle_hash: string;
  source_report_hash: string;
  submitted_by: number;
  report: ScriptAuditReport;
  revision: number;
  dispositions: FindingDisposition[];
  open_blockers: number;
  open_warnings: number;
  publication_ready: false;
};

export type CandidateReview = {
  candidate: ReviewCandidate;
  audits: ScriptAudit[];
  references: { target: ReviewFinding['target']; sources: ReviewFinding['sources'] }[];
  source_files: { id: string; relative_path: string; kind: 'original' | 'normalized' | 'supplement' }[];
};
export type FindingDraft = { status: FindingStatus; note: string };

export type RuleReview = {
  schema_version: 'rule-review/1.0' | 'rule-review/1.1';
  runtime_facts?: { package_contract:'script-package/1.2'; human_players:1; investigation_actor:'SELECTED_HUMAN_ONLY';
    action_success_limit:'ONCE_PER_SESSION'; phase_budget:'SHARED_NO_CARRY'; material_recipient:'DECLARED_OWNER' };
  package_hash: string;
  bundle_hash: string;
  semantic_status: 'UNREVIEWED';
  publication_ready: false;
  row_count: number;
  offset: number;
  limit: number;
  next_offset: number | null;
  rows: {
    target: { collection: string; id: string | null };
    label: string;
    rules: Record<string, unknown>;
    source_count: number;
    sources_truncated: boolean;
    sources: { source_id: string; kind: string; anchor: string | null; page: number | null;
      text: string | null; status: string; truncated: boolean }[];
    notices: { code: string; action_id: string; evidence_id: string }[];
    notice_count: number;
    notices_truncated: boolean;
  }[];
};

export type SubmitAuditRequest = {
  idempotency_key: string;
  expected_package_hash: string;
  bundle_hash: string;
  report: ScriptAuditReport;
};

export type SubmitDispositionRequest = {
  idempotency_key: string;
  expected_package_hash: string;
  expected_audit_hash: string;
  expected_revision: number;
  finding_id: string;
  status: FindingStatus;
  note: string;
};
