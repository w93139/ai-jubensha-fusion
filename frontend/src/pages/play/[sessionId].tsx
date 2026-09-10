import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/router';
import { ChevronRight, Loader2, LockKeyhole, Search, Send, Vote } from 'lucide-react';
import AuthGuard from '@/components/AuthGuard';
import AppLayout from '@/components/AppLayout';
import FusionEvidencePanel from '@/components/FusionEvidencePanel';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import fusionGameService from '@/services/fusionGameService';
import { FusionEvent, FusionState } from '@/types/fusion';

const phaseNames: Record<string, string> = {
  CHARACTER_SELECTION: '选择角色', SCRIPT_READING: '阅读私本', BACKGROUND: '案件背景', INTRODUCTION: '自我介绍',
  EVIDENCE_ROUND_1: '第一轮搜证', INVESTIGATION: '线索质询', EVIDENCE_ROUND_2: '第二轮搜证',
  DISCUSSION: '圆桌讨论', VOTING: '最终投票', RUNOFF_VOTING: '加赛投票', REVELATION: '真相复盘', ENDED: '已结束',
};
const order = Object.keys(phaseNames);

export default function FusionRoom() {
  const router = useRouter();
  const sessionId = String(router.query.sessionId || '');
  const [state, setState] = useState<FusionState>();
  const [events, setEvents] = useState<FusionEvent[]>([]);
  const [input, setInput] = useState('');
  const [target, setTarget] = useState<number>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [panel, setPanel] = useState<'evidence' | 'role' | null>(null);

  const refreshEvents = useCallback(async (after = 0) => {
    if (!sessionId) return;
    const incoming = await fusionGameService.events(sessionId, after);
    setEvents(current => after ? [...current, ...incoming.filter(item => !current.some(old => old.event_id === item.event_id))] : incoming);
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) return;
    fusionGameService.state(sessionId).then(value => { setState(value); refreshEvents(); }).catch(error => setError(error.message));
    const socket = new WebSocket(fusionGameService.websocketUrl(sessionId));
    socket.onmessage = message => {
      const body = JSON.parse(message.data);
      if (body.payload?.session) setState(body.payload);
      if (body.events) setEvents(current => [...current, ...body.events.filter((item: FusionEvent) => !current.some(old => old.event_id === item.event_id))]);
      if (body.type === 'ERROR') setError(body.payload?.message || '操作失败');
    };
    return () => socket.close();
  }, [sessionId, refreshEvents]);

  const act = async (type: string, payload: Record<string, unknown> = {}) => {
    if (!state || busy) return;
    setBusy(true); setError('');
    try {
      const next = await fusionGameService.action(sessionId, type, payload);
      setState(next); await refreshEvents(state.last_event_id);
    } catch (error) { setError(error instanceof Error ? error.message : '操作失败'); }
    finally { setBusy(false); }
  };

  const messages = useMemo(() => events.filter(event => ['PUBLIC_MESSAGE', 'QUESTION_ASKED', 'AI_MESSAGE', 'EVIDENCE_REVEALED', 'PHASE_CHANGED'].includes(event.type)), [events]);
  if (!state) return <AuthGuard><div className="flex min-h-screen items-center justify-center bg-ink text-mist">{error || <Loader2 className="animate-spin"/>}</div></AuthGuard>;

  const phase = state.phase;
  const canTalk = ['INTRODUCTION', 'INVESTIGATION', 'DISCUSSION'].includes(phase);
  const canAdvance = ['BACKGROUND', 'INTRODUCTION', 'EVIDENCE_ROUND_1', 'INVESTIGATION', 'EVIDENCE_ROUND_2', 'DISCUSSION', 'REVELATION'].includes(phase);

  return <AuthGuard><AppLayout showSidebar={false} isGamePage>
    <div className="relative z-10 flex min-h-screen flex-col bg-ink text-paper">
      <header className="sticky top-14 z-30 border-b border-line bg-ink/95 px-4 py-3 backdrop-blur md:top-0">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-3">
          <div><p className="text-xs text-brass">{state.script.title}</p><h1 className="font-dossier text-lg">{phaseNames[phase]}</h1></div>
          <div className="text-right text-xs text-faint">{Math.min(order.indexOf(phase) + 1, order.length)} / {order.length}</div>
        </div>
        <div className="mx-auto mt-2 h-1 max-w-6xl overflow-hidden rounded bg-raised"><div className="h-full bg-brass transition-all" style={{ width: `${((order.indexOf(phase) + 1) / order.length) * 100}%` }}/></div>
      </header>

      <main className="mx-auto grid w-full max-w-6xl flex-1 gap-4 px-4 py-5 pb-36 md:grid-cols-[1fr_340px] md:pb-24">
        <section className="min-w-0">
          {phase === 'CHARACTER_SELECTION' && <Stage title="选择你的身份" description="你可以选择包括凶手在内的任意非受害者角色。选定后无法更换。">
            <div className="grid gap-3 sm:grid-cols-2">{state.characters.map(character => <button key={character.id} disabled={busy} onClick={async () => { setBusy(true); try { setState(await fusionGameService.selectCharacter(sessionId, character.id)); await refreshEvents(); } catch (error) { setError(error instanceof Error ? error.message : '选角失败'); } finally { setBusy(false); } }} className="rounded border border-line bg-panel p-4 text-left hover:border-brass/60"><strong className="font-dossier text-lg">{character.name}</strong><p className="mt-1 text-sm text-mist">{character.profession || '身份未知'} · {character.gender || '未知'}</p></button>)}</div>
          </Stage>}

          {phase === 'SCRIPT_READING' && state.my_role && <Stage title={`你的身份：${state.my_role.name}`} description="以下内容只有你能看到。">
            <PrivateBlock label="人物经历">{state.my_role.background}</PrivateBlock><PrivateBlock label="不可公开的秘密">{state.my_role.secret}</PrivateBlock><PrivateBlock label="个人目标">{state.my_role.objective}</PrivateBlock>
            {state.my_role.is_murderer && <div className="mt-4 border border-red-500/40 bg-red-950/30 p-4 text-red-100">你是凶手。你可以说谎，但不能使用规则之外的行动。</div>}
            <Button className="mt-6 w-full bg-brass text-ink" disabled={busy} onClick={() => act('ready')}>我已读完，进入案件</Button>
          </Stage>}

          {phase === 'BACKGROUND' && <Stage title={state.background?.title || '案件背景'} description={state.background?.setting}><p className="whitespace-pre-wrap leading-7 text-mist">{state.background?.incident}</p></Stage>}

          {['EVIDENCE_ROUND_1', 'EVIDENCE_ROUND_2'].includes(phase) && <Stage title="选择搜证地点" description="每次行动发现一条尚未被带走的线索；你可以选择暂时隐藏或公开。">
            <div className="grid gap-3 sm:grid-cols-2">{state.locations.map(location => <button key={location.id} disabled={busy} onClick={() => act('search_location', { location_id: location.id })} className="flex items-start gap-3 rounded border border-line bg-panel p-4 text-left hover:border-brass/60"><Search className="mt-1 shrink-0 text-brass" size={18}/><span><strong>{location.name}</strong><small className="mt-1 block text-mist">{location.description}</small></span></button>)}</div>
          </Stage>}

          {['VOTING', 'RUNOFF_VOTING'].includes(phase) && <Stage title={phase === 'VOTING' ? '写下你的指认' : '最高票加赛'} description="投票提交后不可修改；AI 角色将按各自判断同时投票。">
            <div className="grid gap-3 sm:grid-cols-2">{state.characters.filter(character => character.id !== state.my_role?.id).map(character => <Button key={character.id} disabled={busy} variant="outline" className="h-auto justify-start border-line p-4 text-paper" onClick={() => act('cast_vote', { suspect_character_id: character.id })}><Vote className="mr-3 text-brass" size={18}/>{character.name} · {character.profession}</Button>)}</div>
          </Stage>}

          {['REVELATION', 'ENDED'].includes(phase) && <Stage title="真相复盘" description={state.revelation?.verdict ? '本局形成了有效指认。' : '本局未形成有效指认。'}>
            <div className="rounded border border-brass/40 bg-brass/10 p-5"><p className="text-xs text-brass">真正的凶手</p><p className="mt-1 font-dossier text-2xl">{state.revelation?.murderer?.name || '未知'}</p></div>
            <pre className="mt-4 whitespace-pre-wrap rounded border border-line bg-panel p-4 font-sans text-sm leading-6 text-mist">{JSON.stringify(state.revelation?.truth || {}, null, 2)}</pre>
          </Stage>}

          {!['CHARACTER_SELECTION', 'SCRIPT_READING', 'BACKGROUND', 'EVIDENCE_ROUND_1', 'EVIDENCE_ROUND_2', 'VOTING', 'RUNOFF_VOTING', 'REVELATION', 'ENDED'].includes(phase) && <Stage title={phaseNames[phase]} description="所有公开发言与线索都会保存在案件记录中。"><MessageFeed events={messages}/></Stage>}
          {error && <div className="mt-4 border border-red-500/40 bg-red-950/30 p-3 text-sm text-red-200">{error}</div>}
        </section>

        <aside className="hidden rounded border border-line bg-panel p-4 md:block"><SideContent state={state} act={act} busy={busy}/></aside>
      </main>

      <div className="fixed bottom-0 left-0 right-0 z-40 border-t border-line bg-ink/95 p-3 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-end gap-2">
          <Button variant="outline" className="border-line" onClick={() => setPanel(panel === 'role' ? null : 'role')}><LockKeyhole size={17}/><span className="ml-1 hidden sm:inline">私本</span></Button>
          <Button variant="outline" className="border-line" onClick={() => setPanel(panel === 'evidence' ? null : 'evidence')}><Search size={17}/><span className="ml-1 hidden sm:inline">证据</span></Button>
          {canTalk && <><Textarea value={input} onChange={event => setInput(event.target.value)} className="max-h-24 min-h-10 resize-none border-line bg-panel" placeholder={target ? '向选中的角色提问…' : '公开发言…'}/><Button disabled={busy || !input.trim()} className="bg-brass text-ink" onClick={() => { const value = input; setInput(''); target ? act('ask_question', { target_character_id: target, content: value }) : act('send_message', { content: value }); }}><Send size={17}/></Button></>}
          {canAdvance && <Button disabled={busy} onClick={() => act('advance_phase')} className="ml-auto bg-brass text-ink">下一阶段<ChevronRight size={17}/></Button>}
        </div>
      </div>

      {panel && <div className="fixed inset-x-0 bottom-16 z-30 max-h-[60vh] overflow-auto rounded-t-xl border border-line bg-panel p-5 shadow-2xl md:hidden"><button className="float-right text-faint" onClick={() => setPanel(null)}>关闭</button><SideContent state={state} act={act} busy={busy} roleOnly={panel === 'role'}/></div>}
      {canTalk && <div className="fixed right-3 top-32 z-20 flex max-h-56 w-40 flex-col gap-1 overflow-auto rounded border border-line bg-panel/95 p-2 text-xs md:right-8"><button className={`rounded p-2 text-left ${!target ? 'bg-brass/20 text-brass' : 'text-mist'}`} onClick={() => setTarget(undefined)}>公开发言</button>{state.characters.filter(c => c.id !== state.my_role?.id).map(c => <button key={c.id} className={`rounded p-2 text-left ${target === c.id ? 'bg-brass/20 text-brass' : 'text-mist'}`} onClick={() => setTarget(c.id)}>问 {c.name}</button>)}</div>}
    </div>
  </AppLayout></AuthGuard>;
}

function Stage({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) { return <div><h2 className="font-dossier text-2xl">{title}</h2>{description && <p className="mt-2 mb-6 text-sm leading-6 text-mist">{description}</p>}{children}</div>; }
function PrivateBlock({ label, children }: { label: string; children?: React.ReactNode }) { return <div className="mt-3 rounded border border-line bg-panel p-4"><p className="mb-2 text-xs text-brass">{label}</p><p className="whitespace-pre-wrap text-sm leading-6 text-mist">{children || '暂无'}</p></div>; }
function MessageFeed({ events }: { events: FusionEvent[] }) { return <div className="space-y-3">{events.map(event => <div key={event.event_id} className="rounded border border-line bg-panel p-3"><p className="text-xs text-brass">{event.type === 'AI_MESSAGE' ? 'AI 角色' : event.type === 'PHASE_CHANGED' ? '主持人' : '案件记录'}</p><p className="mt-1 whitespace-pre-wrap text-sm text-mist">{String(event.payload.content || event.payload.phase || (event.type === 'EVIDENCE_REVEALED' ? '一条线索被公开' : ''))}</p></div>)}</div>; }
function SideContent({ state, act, busy, roleOnly = false }: {
  state: FusionState;
  act: (type: string, payload?: Record<string, unknown>) => Promise<void>;
  busy: boolean;
  roleOnly?: boolean;
}) {
  return <div>
    {!roleOnly && <FusionEvidencePanel publicEvidence={state.public_evidence} privateEvidence={state.private_evidence} onReveal={evidenceId => { void act('reveal_evidence', { evidence_id: evidenceId }); }} busy={busy} />}
    <h3 className={`${roleOnly ? '' : 'mt-6'} font-dossier text-lg`}>私人剧本</h3>
    <p className="mt-2 text-sm leading-6 text-mist">{state.my_role?.background || '选择角色后可见'}</p>
    <p className="mt-3 text-xs text-brass">秘密</p>
    <p className="mt-1 text-sm leading-6 text-mist">{state.my_role?.secret || '—'}</p>
  </div>;
}
