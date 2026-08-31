import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import { Clock, Loader2, Users } from 'lucide-react';
import AuthGuard from '@/components/AuthGuard';
import AppLayout from '@/components/AppLayout';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import fusionGameService from '@/services/fusionGameService';
import { PublicScript } from '@/types/fusion';

export default function PlayLobby() {
  const router = useRouter();
  const [scripts, setScripts] = useState<PublicScript[]>([]);
  const [busy, setBusy] = useState<number>();
  const [error, setError] = useState('');
  useEffect(() => { fusionGameService.scripts().then(setScripts).catch(error => setError(error.message)); }, []);

  const start = async (id: number) => {
    setBusy(id); setError('');
    try {
      const state = await fusionGameService.createSession(id);
      await router.push(`/play/${state.session.session_id}`);
    } catch (error) { setError(error instanceof Error ? error.message : '建局失败'); setBusy(undefined); }
  };

  return <AuthGuard><AppLayout>
    <main className="relative z-10 mx-auto max-w-6xl px-4 pb-24 pt-20 md:pt-12">
      <p className="font-data text-xs tracking-[0.25em] text-brass">SOLO MYSTERY</p>
      <h1 className="mt-2 font-dossier text-3xl font-bold text-paper">选择今晚的案件</h1>
      <p className="mt-3 max-w-2xl text-mist">你扮演其中一名角色，其余人物由 AI 演绎。每局约 30–45 分钟，随时可以离开后继续。</p>
      {error && <div className="mt-6 border border-red-500/40 bg-red-950/30 p-3 text-sm text-red-200">{error}</div>}
      <div className="mt-8 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {scripts.map(script => <Card key={script.id} className="border-line bg-panel">
          <CardHeader><CardTitle className="font-dossier text-xl text-paper">{script.title}</CardTitle></CardHeader>
          <CardContent>
            <p className="min-h-16 text-sm leading-6 text-mist">{script.description || '一桩等待你揭开的案件。'}</p>
            <div className="my-5 flex gap-4 text-xs text-faint"><span className="flex gap-1"><Users size={14}/>{script.player_count} 角色</span><span className="flex gap-1"><Clock size={14}/>{script.duration_minutes} 分钟</span></div>
            <Button className="w-full bg-brass text-ink hover:bg-brass/80" disabled={busy === script.id} onClick={() => start(script.id)}>{busy === script.id ? <Loader2 className="animate-spin"/> : '进入案件'}</Button>
          </CardContent>
        </Card>)}
      </div>
      {!error && scripts.length === 0 && <p className="mt-16 text-center text-mist">暂无已发布剧本，请管理员完成质检并发布。</p>}
    </main>
  </AppLayout></AuthGuard>;
}

