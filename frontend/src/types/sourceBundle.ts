export type FrozenSource = {
  id: string;
  relative_path: string;
  kind: 'original' | 'ocr' | 'revised' | 'supplement' | 'reference';
  material_type: 'role' | 'host' | 'clue' | 'rules' | 'source_record' | 'analysis';
  original_paths: string[];
  sha256: string;
  size_bytes: number;
  media_type: string;
};

export type SourceBundleSummary = {
  bundle_hash: string;
  status: 'FROZEN' | 'CORRUPT';
  script_key?: string;
  edition?: string;
  notes?: string[];
  file_count?: number;
  total_bytes?: number;
  publication_ready?: false;
};

export type SourceBundle = SourceBundleSummary & { sources: FrozenSource[] };

export type SourceVerification = {
  report_hash: string;
  bundle_hash: string;
  package_hash: string | null;
  verified_at: string;
  valid: boolean;
  publication_ready: false;
  checked_files: number;
  issues: { code: string; source_id?: string }[];
  issues_truncated: boolean;
};

export type SourcePreview = { sourceId: string; text?: string; imageUrl?: string };
