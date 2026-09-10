import { useRouter } from 'next/router';
import AuthGuard from '@/components/AuthGuard';
import AppLayout from '@/components/AppLayout';
import { PackagePlayWorkspace } from '@/components/PackagePlayPanel';

export default function PackagePlayPage() {
  const router = useRouter();
  const playId = typeof router.query.play === 'string' ? router.query.play : '';
  const openingSessionId = typeof router.query.opening_session_id === 'string' ? router.query.opening_session_id : '';
  return <AuthGuard><AppLayout gameWorkspace={Boolean(playId)}>{router.isReady && <PackagePlayWorkspace key={router.asPath} playId={playId} openingSessionId={openingSessionId}
    onCreated={id => router.replace({ pathname: '/play/package-play', query: { play: id } })} />}</AppLayout></AuthGuard>;
}
