import { Button } from '@/components/ui/button';
import type { Evidence } from '@/types/fusion';

interface FusionEvidencePanelProps {
  publicEvidence: Evidence[];
  privateEvidence: Evidence[];
  onReveal: (evidenceId: number) => void;
  busy?: boolean;
}

export default function FusionEvidencePanel({
  publicEvidence,
  privateEvidence,
  onReveal,
  busy = false,
}: FusionEvidencePanelProps) {
  // A player's acquired evidence can remain in private_evidence after sharing.
  // Display it once, in the public section, using the server's visibility state.
  const publicById = new Map(publicEvidence.map(evidence => [evidence.id, evidence]));
  for (const evidence of privateEvidence) {
    if (evidence.visibility === 'PUBLIC' && !publicById.has(evidence.id)) {
      publicById.set(evidence.id, evidence);
    }
  }
  const sharedEvidence = [...publicById.values()];
  const unsharedEvidence = privateEvidence.filter(evidence => !publicById.has(evidence.id));

  return <div className="space-y-6">
    <section aria-label="公开线索">
      <h3 className="font-dossier text-lg">公开线索 <span className="text-sm text-faint">({sharedEvidence.length})</span></h3>
      <p className="mt-1 text-xs text-faint">所有角色可见，点击线索查看详情。</p>
      <div className="mt-3 space-y-2">
        {sharedEvidence.map(evidence => <EvidenceCard key={evidence.id} evidence={evidence} isPublic />)}
        {sharedEvidence.length === 0 && <p className="text-sm text-faint">尚无线索被公开</p>}
      </div>
    </section>

    <section aria-label="我的未公开线索">
      <h3 className="font-dossier text-lg">我的未公开线索 <span className="text-sm text-faint">({unsharedEvidence.length})</span></h3>
      <p className="mt-1 text-xs text-faint">仅你可见，公开后其他角色才能看到。</p>
      <div className="mt-3 space-y-2">
        {unsharedEvidence.map(evidence => <EvidenceCard key={evidence.id} evidence={evidence} isPublic={false} onReveal={onReveal} busy={busy} />)}
        {unsharedEvidence.length === 0 && <p className="text-sm text-faint">暂无未公开线索</p>}
      </div>
    </section>
  </div>;
}

function EvidenceCard({ evidence, isPublic, onReveal, busy }: {
  evidence: Evidence;
  isPublic: boolean;
  onReveal?: (evidenceId: number) => void;
  busy?: boolean;
}) {
  return <details className="rounded border border-line">
    <summary className="cursor-pointer rounded p-3 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-brass">
      <strong>{evidence.name}</strong>
      <span className={`ml-2 text-xs ${isPublic ? 'text-brass' : 'text-faint'}`}>{isPublic ? '已公开' : '仅你可见'}</span>
    </summary>
    <div className="border-t border-line px-3 pb-3 pt-2">
      {evidence.location && <p className="mb-2 text-xs text-faint">发现地点：{evidence.location}</p>}
      <p className="whitespace-pre-wrap break-words text-sm leading-6 text-mist">{evidence.description || '暂无详细描述'}</p>
      {!isPublic && onReveal && <Button size="sm" variant="outline" className="mt-3 border-brass/40 text-brass" disabled={busy} onClick={() => onReveal(evidence.id)}>公开这条线索</Button>}
    </div>
  </details>;
}
