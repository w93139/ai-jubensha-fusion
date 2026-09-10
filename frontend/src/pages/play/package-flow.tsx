import { useRouter } from 'next/router';
import AuthGuard from '@/components/AuthGuard';
import AppLayout from '@/components/AppLayout';
import { PackageFlowWorkspace } from '@/components/PackageFlowPanel';

export default function PackageFlowPage() {
  const router = useRouter();
  const flowId = typeof router.query.flow === 'string' ? router.query.flow : '';
  const openingSessionId = typeof router.query.opening_session_id === 'string' ? router.query.opening_session_id : '';
  return <AuthGuard><AppLayout>{router.isReady && <PackageFlowWorkspace key={router.asPath} flowId={flowId} openingSessionId={openingSessionId}
    onCreated={id => router.replace({ pathname: '/play/package-flow', query: { flow: id } })} />}</AppLayout></AuthGuard>;
}
