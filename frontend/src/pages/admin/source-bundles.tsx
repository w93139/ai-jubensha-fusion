import { useEffect, useRef, useState } from 'react';
import AuthGuard from '@/components/AuthGuard';
import AppLayout from '@/components/AppLayout';
import SourceBundlePanel from '@/components/SourceBundlePanel';
import sourceBundleService from '@/services/sourceBundleService';
import { useAuthStore } from '@/stores/authStore';
import type { FrozenSource, SourceBundle, SourceBundleSummary, SourcePreview, SourceVerification } from '@/types/sourceBundle';

function SourceReview() {
  const [bundles, setBundles] = useState<SourceBundleSummary[]>([]);
  const [selectedHash, setSelectedHash] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    sourceBundleService.list(controller.signal).then(result => {
      if (controller.signal.aborted) return;
      setBundles(result);
      setSelectedHash(current => result.some(item => item.bundle_hash === current && item.status === 'FROZEN') ? current : result.find(item => item.status === 'FROZEN')?.bundle_hash || '');
    }).catch(error => { if (!controller.signal.aborted) setError(error.message || '读取列表失败'); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [reload]);
  return <BundleWorkspace key={`${selectedHash}-${reload}`} selectedHash={selectedHash} bundles={bundles} listLoading={loading} listError={error}
    onSelect={setSelectedHash} onReload={() => { setError(''); setLoading(true); setReload(value => value + 1); }} />;
}

function BundleWorkspace({ selectedHash, bundles, listLoading, listError, onSelect, onReload }: {
  selectedHash: string; bundles: SourceBundleSummary[]; listLoading: boolean; listError: string;
  onSelect: (hash: string) => void; onReload: () => void;
}) {
  const [bundle, setBundle] = useState<SourceBundle>();
  const [report, setReport] = useState<SourceVerification>();
  const [preview, setPreview] = useState<SourcePreview>();
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(Boolean(selectedHash));
  const [verifying, setVerifying] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [error, setError] = useState('');
  const requests = useRef<{ preview?: AbortController; verify?: AbortController; imageUrl?: string }>({});

  useEffect(() => {
    const managed = requests.current;
    const controller = new AbortController();
    if (selectedHash) {
      sourceBundleService.bundle(selectedHash, controller.signal).then(result => { if (!controller.signal.aborted) setBundle(result); })
        .catch(error => { if (!controller.signal.aborted) setError(error.message || '读取版本失败'); })
        .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }
    return () => {
      controller.abort(); managed.verify?.abort(); managed.preview?.abort();
      if (managed.imageUrl) URL.revokeObjectURL(managed.imageUrl);
    };
  }, [selectedHash]);

  const showSource = async (source: FrozenSource) => {
    const managed = requests.current;
    managed.preview?.abort();
    if (managed.imageUrl) URL.revokeObjectURL(managed.imageUrl);
    managed.imageUrl = undefined;
    const controller = new AbortController();
    managed.preview = controller;
    setPreview({ sourceId: source.id }); setPreviewLoading(true); setError('');
    try {
      const response = await sourceBundleService.source(selectedHash, source.id, controller.signal);
      if (source.media_type.startsWith('image/')) {
        const blob = await response.blob();
        if (controller.signal.aborted) return;
        managed.imageUrl = URL.createObjectURL(blob);
        setPreview({ sourceId: source.id, imageUrl: managed.imageUrl });
      } else {
        const text = await response.text();
        if (!controller.signal.aborted) setPreview({ sourceId: source.id, text });
      }
    } catch (error) {
      if (!controller.signal.aborted) setError(error instanceof Error ? error.message : '读取材料失败');
    } finally {
      if (!controller.signal.aborted) setPreviewLoading(false);
    }
  };

  const verify = async () => {
    const managed = requests.current;
    managed.verify?.abort();
    const controller = new AbortController();
    managed.verify = controller;
    setVerifying(true); setReport(undefined); setError('');
    try {
      const result = await sourceBundleService.verify(selectedHash, controller.signal);
      if (!controller.signal.aborted) setReport(result);
    } catch (error) {
      if (!controller.signal.aborted) setError(error instanceof Error ? error.message : '核验失败');
    } finally {
      if (!controller.signal.aborted) setVerifying(false);
    }
  };

  return <SourceBundlePanel bundles={bundles} bundle={bundle} selectedHash={selectedHash} query={query}
    loading={loading || listLoading} verifying={verifying} previewLoading={previewLoading} error={error || listError} report={report} preview={preview}
    onSelect={onSelect} onQuery={setQuery} onPreview={showSource} onVerify={verify} onReload={onReload} />;
}

export default function SourceBundlesPage() {
  const user = useAuthStore(state => state.user);
  return <AuthGuard><AppLayout>{user?.is_admin
    ? <SourceReview />
    : <p role="alert" className="mx-auto max-w-3xl px-4 pt-24 text-paper">只有管理员可以查看剧本来源材料。</p>}
  </AppLayout></AuthGuard>;
}
