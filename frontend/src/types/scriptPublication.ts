import type { ReviewCandidate, ScriptAudit, ScriptAuditReport } from '@/types/scriptReview';

export type ModelFindingDecision = {
  job_id: number;
  finding_id: string;
  status: 'ACKNOWLEDGED' | 'DISMISSED';
  note: string;
};

export type PublicationApproval = {
  id: number;
  version_id: number;
  approval_hash: string;
  package_hash: string;
  bundle_hash: string;
  basis_hash: string;
  source_report_hash: string;
  submitted_by: number;
  note: string;
  model_dispositions: ModelFindingDecision[];
  policy_version: string;
  created_at: string;
  valid: boolean;
};

export type PublicationRelease = {
  id: number;
  version_id: number;
  release_hash: string;
  approval_id: number;
  approval_hash: string;
  package_hash: string;
  bundle_hash: string;
  basis_hash: string;
  source_report_hash: string;
  submitted_by: number;
  created_at: string;
  current_approval_valid: boolean;
};

export type PublicationGate = {
  candidate: ReviewCandidate & { manifest_hash: string; player_count: number };
  basis_hash: string;
  bundle_hash: string | null;
  manual_reports: ScriptAudit[];
  model_reports: {
    job_id: number;
    state: string;
    attempt_id: number | null;
    status: 'SUCCEEDED' | 'FAILED' | 'UNKNOWN' | 'IN_FLIGHT' | 'RESERVED' | 'MISSING';
    output_hash: string | null;
    receipt_hash: string | null;
    report: ScriptAuditReport | null;
    error_code: string | null;
  }[];
  checks: { code: string; passed: boolean; message: string }[];
  can_approve: boolean;
  can_publish: boolean;
  approval: PublicationApproval | null;
  release: PublicationRelease | null;
};

export type ApprovePublicationRequest = {
  idempotency_key: string;
  expected_package_hash: string;
  bundle_hash: string;
  expected_basis_hash: string;
  model_dispositions: ModelFindingDecision[];
  note: string;
};

export type PublishPackageRequest = {
  idempotency_key: string;
  approval_id: number;
  expected_approval_hash: string;
  expected_basis_hash: string;
};

export type CandidateDocument = {
  id: number;
  script_key: string;
  content_version: string;
  package_hash: string;
  manifest_hash: string;
  package: Record<string, unknown>;
  publication_ready: false;
};

export type PublicationFindingDraft = {
  status: '' | ModelFindingDecision['status'];
  note: string;
};
