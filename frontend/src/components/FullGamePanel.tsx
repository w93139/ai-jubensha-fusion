import type { ReactNode } from 'react';
import type { PlaySpeechEntry } from '../lib/playSpeech';
import PlayText from './PlayText';
import PlayTopicExchange from './PlayTopicExchange';
import { topicForRequest } from '@/lib/playTopics';
import type { TopicAction } from '@/types/packagePlay';
import PlaySelect from './PlaySelect';
import { useEffect, useRef, useState } from 'react';
import { questionClarification, questionRefusal } from '@/lib/playQuestionGuide';
import type { PackagePlay, FullTableAction, FullDecisionAction, FullSubmission } from '@/types/packagePlay';

const button = 'rounded border border-brass px-3 py-2 text-sm text-brass disabled:opacity-50';
const field = 'w-full rounded border border-line bg-ink p-3 text-paper';
const box = 'min-w-0 space-y-4 rounded border border-line bg-panel p-5';
const statusText = { PENDING: '等待结果', OK: '已提交', INVALID: '未通过检查，尚未提交', UNKNOWN: '结果未知，尚未提交', STALE: '信息已变化，尚未提交', EXPIRED: '请求已过期，尚未提交' };

export function validFullGame(view: PackagePlay): boolean {
  const full = view.full_game;
  if (!full) return view.table_decisions === undefined;
  const seat = (id: string) => view.characters.some(c => c.id === id);
  const finite = (n: number) => Number.isSafeInteger(n) && n >= 0;
  try {
    if (full.schema_version !== 'full-game-view/1.0' || !['READING', 'INVESTIGATION', 'FINALE'].includes(full.phase_kind)
      || typeof full.can_open_ballot !== 'boolean' || typeof full.phone_busy !== 'boolean'
      || (full.result !== null) !== view.settled || !Array.isArray(full.private_discussion)) return false;
    if (full.call && (!full.phone_busy || full.call.character_ids.length !== 2
      || new Set(full.call.character_ids).size !== 2 || !full.call.character_ids.every(seat)
      || !full.call.character_ids.includes(view.selected_character_id))) return false;
    if (!full.private_discussion.every(m => m.kind === 'CLAIM' && m.id === `private-${m.sequence}`
      && finite(m.sequence) && m.sequence > 0 && m.sequence <= view.revision && seat(m.speaker)
      && typeof m.text === 'string' && Boolean(m.text.trim()) && Array.from(m.text).length <= 1000
      && m.audience.length === 2 && new Set(m.audience).size === 2 && m.audience.every(seat)
      && m.audience.includes(view.selected_character_id) && m.audience.includes(m.speaker))) return false;
    if (full.ballot && (full.ballot.schema_version !== 'collective-round-view/1.0' || !seat(full.ballot.decider)
      || full.phase_kind !== 'INVESTIGATION' || !finite(full.ballot.sealed_count) || full.ballot.sealed_count > 5
      || full.ballot.required_count !== 5 || !['WAITING', 'TIE'].includes(full.ballot.status)
      || !Array.isArray(full.ballot.tied_choice_ids) || !Array.isArray(full.ballot.choices) || !full.ballot.choices.length
      || new Set(full.ballot.choices.map(c => c.id)).size !== full.ballot.choices.length
      || !full.ballot.choices.every(c => typeof c.id === 'string' && typeof c.label === 'string' && finite(c.cost))
      || !full.ballot.tied_choice_ids.every(id => full.ballot!.choices.some(c => c.id === id)))) return false;
    if (full.finale) {
      const f = full.finale;
      if (full.phase_kind !== 'FINALE' || f.schema_version !== 'structured-finale-view/1.0'
        || typeof f.sealed !== 'boolean' || typeof f.all_sealed !== 'boolean' || f.sealed !== Boolean(f.submission)
        || !finite(f.votes.sealed_count) || f.votes.sealed_count > 5 || f.votes.required_count !== 5
        || f.all_sealed !== (f.votes.sealed_count === 5) || !Array.isArray(f.questions) || !f.questions.length
        || new Set(f.questions.map(q => q.id)).size !== f.questions.length
        || !f.questions.every(q => typeof q.id === 'string' && typeof q.prompt === 'string'
          && finite(q.max_choices) && q.max_choices <= q.options.length && q.max_choices <= 10
          && Boolean(q.max_choices) === Boolean(q.options.length) && new Set(q.options.map(o => o.id)).size === q.options.length
          && q.options.every(o => typeof o.id === 'string' && typeof o.label === 'string'))
        || f.votes.trust_character_ids.length !== 4 || new Set(f.votes.trust_character_ids).size !== 4
        || f.votes.trust_character_ids.includes(view.selected_character_id) || !f.votes.trust_character_ids.every(seat)
        || !f.votes.accusation_options.every(o => typeof o.id === 'string' && typeof o.label === 'string')) return false;
    } else if (full.phase_kind === 'FINALE') return false;
    if (full.result && (!full.result.totals.every(t => seat(t.character_id) && finite(t.max_points)
      && t.total_points !== null && finite(t.total_points) && t.total_points <= t.max_points)
      || full.result.totals.length !== 5 || new Set(full.result.totals.map(t => t.character_id)).size !== 5
      || !full.result.endings.every(e => e.character_ids.every(seat) && e.texts.every(t => typeof t === 'string'))
      || !full.result.goals.every(g => seat(g.character_id) && typeof g.title === 'string' && finite(g.max_points)
        && g.points !== null && finite(g.points) && g.points <= g.max_points && Array.isArray(g.parts)
        && g.parts.every(p => typeof p.id === 'string' && finite(p.max_points) && p.points !== null && finite(p.points)
          && p.points <= p.max_points && (p.explanation === null || typeof p.explanation === 'string'))))) return false;
    const decisions = view.table_decisions;
    if (view.table_commands && (!Array.isArray(view.table_commands)
      || new Set(view.table_commands.map(r => r.request_id)).size !== view.table_commands.length
      || !view.table_commands.every(r => typeof r.request_id === 'string' && finite(r.revision) && r.revision < view.revision
        && ['OPEN_BALLOT','CAST_BALLOT','BREAK_TIE','START_CALL','STOP_CALL','PRIVATE_SPEAK','SEAL_FINALE','PAUSE_PHONE'].includes(r.action)))) return false;
    const phone = view.phone_turns;
    if (phone && (phone.schema_version !== 'package-phone-view/1.0' || typeof phone.available !== 'boolean'
      || phone.can_pause !== (full.phone_busy && !full.call) || !Array.isArray(phone.requests)
      || new Set(phone.requests.map(r => r.request_id)).size !== phone.requests.length
      || !phone.requests.every(r => Object.keys(r).sort().join(',') === 'request_id,revision,status'
        && typeof r.request_id === 'string' && finite(r.revision) && r.revision < view.revision
        && Object.hasOwn(statusText,r.status)))) return false;
    const replies = view.private_replies;
    if (replies && (replies.schema_version !== 'package-private-dialogue-view/1.0' || typeof replies.available !== 'boolean'
      || !replies.options.every(o => seat(o.character_id) && o.character_id !== view.selected_character_id
        && full.call?.character_ids.includes(o.character_id) && full.private_discussion.some(m => m.id === o.reply_to
          && m.call_id === full.call?.id && m.speaker !== o.character_id))
      || !replies.requests.every(r => typeof r.request_id === 'string' && seat(r.character_id)
        && r.character_id !== view.selected_character_id && finite(r.revision) && r.revision < view.revision
        && Object.hasOwn(statusText, r.status) && full.private_discussion.some(m => m.id === r.reply_to
          && m.audience.includes(r.character_id) && m.speaker !== r.character_id)))) return false;
    return Boolean(decisions && decisions.schema_version === 'package-table-decision-view/1.0'
      && typeof decisions.available === 'boolean'
      && decisions.options.every(o => seat(o.character_id) && o.character_id !== view.selected_character_id
        && ['CAST_BALLOT', 'BREAK_TIE', 'SEAL_FINALE'].includes(o.action))
      && decisions.requests.every(r => typeof r.request_id === 'string' && seat(r.character_id)
        && r.character_id !== view.selected_character_id && finite(r.revision) && r.revision < view.revision
        && ['CAST_BALLOT', 'BREAK_TIE', 'SEAL_FINALE'].includes(r.action) && Object.hasOwn(statusText, r.status)));
  } catch { return false; }
}

export default function FullGamePanel({ view, locked, onTable, onDecide, onPrivateReply, onPhone, renderSpeech, section = 'all', excludePrivate = false, onFinaleVotes, finaleVotesNeedsConfirmation, finaleVotesProgress, onTopic, onTopicReply, pendingTopicReplyKey, recoveryLocked = locked }: {
  ownerId?: string | null;
  onTopic?: (action: TopicAction) => void;
  onTopicReply?: (turnId: string) => void;
  pendingTopicReplyKey?: string;
  recoveryLocked?: boolean;
  view: PackagePlay; locked: boolean;
  renderSpeech?: (entry: PlaySpeechEntry) => ReactNode;
  onTable?: (action: FullTableAction) => void | Promise<boolean>;
  onDecide?: (actor: string, action: FullDecisionAction) => void;
  onPrivateReply?: (actor: string, target: string) => void;
  onPhone?: (action: 'STEP' | 'PAUSE') => void;
  section?: 'all' | 'investigation' | 'private' | 'finale';
  excludePrivate?: boolean;
  onFinaleVotes?: (confirmedRetry?: boolean) => void;
  finaleVotesNeedsConfirmation?: boolean;
  finaleVotesProgress?: string;
}) {
  const [peer, setPeer] = useState('');
  const callId = view.full_game?.call?.id;
  const [privateDraft, setPrivateDraft] = useState<{ callId: string | undefined; text: string }>({ callId, text: '' });
  const privateText = privateDraft.callId === callId ? privateDraft.text : '';
  const setPrivateText = (value: string | ((current: string) => string)) => setPrivateDraft(current => {
    if (typeof value === 'function' && current.callId !== callId) return current;
    return { callId, text: typeof value === 'function' ? value(current.text) : value };
  });
  const [answers, setAnswers] = useState<Record<string, string[]>>({});
  const [accusation, setAccusation] = useState<string | undefined>();
  const [trust, setTrust] = useState<string | undefined>();
  const [reflection, setReflection] = useState('');
  const messages = useRef<HTMLDivElement>(null);
  const historyPeer = view.full_game?.call?.character_ids.find(id => id !== view.selected_character_id)
    || peer || view.full_game?.private_discussion.at(-1)?.audience.find(id => id !== view.selected_character_id);
  const visibleMessages = view.full_game?.private_discussion.filter(message => message.audience.includes(historyPeer || '')) || [];
  const lastMessage = visibleMessages.at(-1)?.id;
  useEffect(() => { if (messages.current) messages.current.scrollTop = messages.current.scrollHeight; }, [lastMessage, historyPeer]);
  if (!view.full_game || !validFullGame(view)) return null;
  const full = view.full_game;
  const name = (id: string) => view.characters.find(c => c.id === id)?.name || id;
  const disabled = locked || view.pending_ai || view.settled || !onTable;
  const privateLength = Array.from(privateText.trim()).length;
  const privateClarification = questionRefusal(privateText) || questionClarification(privateText);
  const canSendPrivate = !view.single_player && !disabled && !privateClarification && privateLength > 0 && privateLength <= 1000;
  const send = (action: FullTableAction) => disabled ? undefined : onTable?.(action);
  const finale = full.finale;
  const canSeal = finale && !finale.sealed && accusation !== undefined && trust !== undefined
    && finale.questions.every(q => Object.hasOwn(answers, q.id) && answers[q.id].length <= q.max_choices);
  const seal = () => {
    if (!canSeal || disabled || !finale) return;
    const payload: FullSubmission = { schema_version: 'structured-finale-submission/1.0',
      answers: finale.questions.map(q => ({ question_id: q.id, option_ids: answers[q.id] })),
      vote: { accusation_id: accusation || null, trust_character_id: trust || null }, reflection };
    send({ action: 'SEAL_FINALE', payload });
  };
  return <>
    {!view.guided_play && (section === 'all' || section === 'investigation') && full.phase_kind === 'INVESTIGATION' && <section aria-label="集体调查投票" className={box}>
      <h2 className="text-lg font-semibold">共同决定下一次调查</h2>
      <p className="text-sm leading-7 text-mist">每人一票。五席提交后执行选中的调查；平票由本次裁决席决定。只有大家都选择结束，才能提前结束本轮。</p>
      <p>剩余调查点：{view.mechanics?.remaining_points} / {view.mechanics?.initial_points}</p>
      {full.can_open_ballot && <button type="button" className={button} disabled={disabled} onClick={() => send({ action: 'OPEN_BALLOT' })}>开始本次投票</button>}
      {full.ballot && <>
        <p role="status">已提交 {full.ballot.sealed_count} / 5 · 本次裁决席：{name(full.ballot.decider)}</p>
        {full.ballot.ballot ? <p>你的票已封存，等待其他角色。</p> : <div className="flex flex-wrap gap-3">
          {full.ballot.choices.map(o => <button type="button" key={o.id} className={button} disabled={disabled}
            onClick={() => send({ action: 'CAST_BALLOT', payload: { kind: 'CHOOSE', choice_id: o.id } })}>{o.label}（{o.cost} 点）</button>)}
          <button type="button" className={button} disabled={disabled} onClick={() => send({ action: 'CAST_BALLOT', payload: { kind: 'ABSTAIN', choice_id: null } })}>本次不表态</button>
          <button type="button" className={button} disabled={disabled} onClick={() => send({ action: 'CAST_BALLOT', payload: { kind: 'SKIP', choice_id: null } })}>我赞成本轮结束</button>
        </div>}
        {full.ballot.status === 'TIE' && <div className="space-y-3"><p>出现平票，等待{name(full.ballot.decider)}选择。</p>
          {full.ballot.decider === view.selected_character_id && full.ballot.tied_choice_ids.map(id => <button type="button" key={id} className={button} disabled={disabled}
            onClick={() => send({ action: 'BREAK_TIE', payload: { choice_id: id } })}>裁定：{full.ballot!.choices.find(o => o.id === id)?.label || id}</button>)}</div>}
      </>}
    </section>}
    {!excludePrivate && (section === 'all' || section === 'private') && (full.phase_kind === 'INVESTIGATION' || full.private_discussion.length > 0) && <section aria-label="单独对话" className={box}>
      <h2 className="text-lg font-semibold">单独对话</h2><p className="text-sm text-mist">{view.single_player ? '选择一位角色，围绕当前议题交流。这里的对话仅双方可见。' : '用文字与一名角色交流，内容仅双方可见。发送后，对方会回复一次。'}</p>
      {!full.call && !full.phone_busy && <div className="space-y-3"><div className="block">通话对象<PlaySelect presentation="choices" label="通话对象" value={peer} disabled={locked || Boolean(full.ballot)} onChange={setPeer} options={view.characters.filter(c => c.id !== view.selected_character_id).map(c => ({ value: c.id, label: c.name }))} /></div>
          {!view.settled && <button type="button" className={button} disabled={disabled || !peer || Boolean(full.ballot) || full.phase_kind !== 'INVESTIGATION' || Boolean(view.single_player && (!view.single_player.available || !view.single_player.topics.some(topic => topic.responders.some(responder => responder.character_id === peer && responder.channels.includes('PRIVATE')))))} onClick={() => send({ action: 'START_CALL', payload: { peer_character_id: peer } })}>开始对话</button>}</div>}
      {historyPeer && <p className="text-sm font-medium text-brass">与{name(historyPeer)}的私聊记录</p>}
      <div ref={messages} role="log" aria-label={historyPeer ? `${name(historyPeer)}的私聊记录` : '私聊记录'} className="max-h-[28dvh] min-h-24 space-y-4 overflow-y-auto overscroll-contain rounded-xl border border-line bg-[#111820] p-4">
        {visibleMessages.length === 0 && <p className="py-8 text-center text-sm text-mist">{view.settled ? '与该角色暂无对话记录。' : '选择一位角色，开始你们的对话。'}</p>}
        {visibleMessages.map(m => { const mine = m.speaker === view.selected_character_id; return <div key={m.id} className={`flex items-start gap-2 ${mine ? 'flex-row-reverse' : ''}`}>
          <span aria-hidden="true" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#293747] text-sm text-paper">{name(m.speaker).slice(0, 1)}</span>
          <div className={`min-w-0 max-w-[82%] ${mine ? 'text-right' : ''}`}><p className="mb-1 text-xs text-mist">{name(m.speaker)}{mine ? ' · 我' : ''}</p>
            <div className={`rounded-xl px-3 py-2 text-left ${mine ? 'bg-[#394333]' : 'bg-[#24303c]'}`}>{!mine && renderSpeech ? renderSpeech(m) : <PlayText text={m.text} />}</div>
          </div>
        </div>; })}
      </div>
      {full.call ? <><p>正在与{full.call.character_ids.filter(id => id !== view.selected_character_id).map(name).join('、')}单独对话</p>
        {!view.single_player && <><label className="block">说给对方听<textarea className={field} value={privateText} maxLength={2000} disabled={disabled} onChange={e => setPrivateText(e.target.value)} /></label>
        {privateClarification && <p role="status" className="text-sm text-brass">{privateClarification}</p>}
        <p className={`text-xs ${privateLength > 1000 ? 'text-brass' : 'text-mist'}`}>{privateLength} / 1000 字符。请围绕本剧本交流。</p>
        <button type="button" className={button} disabled={!canSendPrivate} onClick={async () => { if (!canSendPrivate) return; const sent = privateText; const saved = await send({ action: 'PRIVATE_SPEAK', payload: { text: sent.trim() } }); if (saved === true) setPrivateText(current => current === sent ? '' : current); }}>发送私聊</button>{' '}</>}
        {view.single_player && <PlayTopicExchange view={view} channel="PRIVATE" peer={historyPeer} locked={locked} recoveryLocked={recoveryLocked} pendingReplyKey={pendingTopicReplyKey} onTopic={onTopic} onReply={onTopicReply} />}
        <button type="button" className={button} disabled={disabled} onClick={() => send({ action: 'STOP_CALL' })}>结束对话</button></>
        : full.phone_busy ? <p>其他角色正在通话。</p> : null}
      {!view.single_player && full.call && view.private_replies?.options.slice(-1).filter(o => !view.private_replies!.requests.some(r => r.character_id === o.character_id && r.reply_to === o.reply_to && ['OK', 'PENDING'].includes(r.status))).map(o => <button type="button" key={o.reply_to} className={button}
        disabled={locked || view.pending_ai || !view.private_replies?.available || !onPrivateReply}
        onClick={() => { if (!locked && !view.pending_ai && view.private_replies?.available) onPrivateReply?.(o.character_id, o.reply_to); }}>{view.private_replies?.requests.some(r => r.character_id === o.character_id && r.reply_to === o.reply_to) ? '重试对方回复' : `请${name(o.character_id)}回复刚才的私聊`}</button>)}
      {view.private_replies?.requests.filter(r => !topicForRequest(view, r.request_id)).slice(-3).map(r => <p key={r.request_id} className="text-sm">{name(r.character_id)}私聊：{statusText[r.status]}
        {r.status === 'PENDING' && <button type="button" className={button} disabled={locked || !onPrivateReply}
          onClick={() => onPrivateReply?.(r.character_id, r.reply_to)}>检查这次私聊请求</button>}</p>)}
      {view.phone_turns && (view.phone_turns.can_pause || view.phone_turns.requests.some(r => r.status === 'PENDING')) && <div className="space-y-3">
        <p className="text-sm text-mist">上一版保存的通话仍在进行，可核对原请求或结束等待。已有文字记录会保留。</p>
        {view.phone_turns.requests.some(r => r.status === 'PENDING') && <button type="button" className={button} disabled={locked || view.settled || !onPhone}
          onClick={() => onPhone?.('STEP')}>检查这次电话请求</button>}
        {view.phone_turns.can_pause && <button type="button" className={button} disabled={locked || view.settled || !onPhone}
          onClick={() => onPhone?.('PAUSE')}>中止等待，释放电话</button>}
        {view.phone_turns.requests.slice(-3).map(r => <p key={r.request_id} className="text-sm">电话进度：{statusText[r.status]}</p>)}
      </div>}
    </section>}
    {(section === 'all' || section === 'finale') && !view.settled && finale && <section id="play-finale" aria-label="我的终局答卷" className={box}>
      <h2 className="text-lg font-semibold">我的终局答卷</h2><p>已封卷 {finale.votes.sealed_count} / 5</p>
      <p className="text-sm leading-7 text-mist">答案由既定规则核算分数，不由 AI 自由打分。每题需作答或明确选择不确定；指认和信任也需选择。一起封存后不能修改，所有人提交后才能揭晓。</p>
      <p className="text-xs text-mist">本版使用选项答卷；补充感想可键盘输入或使用系统听写，不支持网页录音。封卷前请逐项核对。</p>
      {!finale.sealed && <>
        {finale.questions.map(q => <fieldset key={q.id} className="space-y-2 rounded border border-line p-3" disabled={disabled}>
          <legend className="whitespace-pre-wrap break-all">{q.prompt}{q.max_choices ? `（最多选 ${q.max_choices} 项）` : ''}</legend>
          {!q.options.length && <p className="text-sm text-mist">本局尚未获得可选的具体依据，仍需明确选择不确定。</p>}
          <div className="grid grid-flow-row grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4" aria-label="答案选项">
          {q.options.map(o => <label key={o.id} className="flex min-h-11 min-w-0 cursor-pointer items-start gap-2 rounded-lg border border-line px-3 py-2 text-sm leading-6 has-checked:border-brass has-checked:bg-brass/10"><input type="checkbox" className="mt-1 h-4 w-4 shrink-0" checked={answers[q.id]?.includes(o.id) || false}
            disabled={disabled || (!answers[q.id]?.includes(o.id) && (answers[q.id]?.length || 0) >= q.max_choices)}
            onChange={e => setAnswers(current => { const next = { ...current };
              const selected = e.target.checked ? [...(current[q.id] || []), o.id] : (current[q.id] || []).filter(id => id !== o.id);
              if (selected.length) next[q.id] = selected; else delete next[q.id]; return next;
            })} />{o.label}</label>)}
          <label className="flex min-h-11 min-w-0 cursor-pointer items-start gap-2 rounded-lg border border-line px-3 py-2 text-sm leading-6 has-checked:border-brass has-checked:bg-brass/10"><input type="checkbox" className="mt-1 h-4 w-4 shrink-0" checked={Object.hasOwn(answers, q.id) && answers[q.id].length === 0}
            onChange={e => setAnswers(current => { const next = { ...current }; if (e.target.checked) next[q.id] = []; else delete next[q.id]; return next; })} />我暂时无法判断</label>
          </div>
        </fieldset>)}
        <label className="block">主案指认<PlaySelect label="主案指认" value={accusation ?? '__unset'} disabled={disabled} onChange={v => setAccusation(v === '__unset' ? undefined : v)} options={[{ value: '__unset', label: '请选择' }, { value: '', label: '弃权' }, ...finale.votes.accusation_options.map(o => ({ value: o.id, label: o.label }))]} /></label>
        <label className="block">我信任谁<PlaySelect label="我信任谁" value={trust ?? '__unset'} disabled={disabled} onChange={v => setTrust(v === '__unset' ? undefined : v)} options={[{ value: '__unset', label: '请选择' }, { value: '', label: '不投信任票' }, ...finale.votes.trust_character_ids.map(id => ({ value: id, label: name(id) }))]} /></label>
        <label className="block">补充感想（选填，不计分）<textarea className={field} value={reflection} maxLength={2000} disabled={disabled} onChange={e => setReflection(e.target.value)} /></label>
      </>}
      <div aria-label="终局投票操作" className="flex flex-wrap items-center gap-3">
        {finale.sealed ? <p role="status" className="text-sm">你的答卷、指认和信任已封存。</p> : <button type="button" className={button} disabled={disabled || !canSeal} onClick={seal}>确认并封存我的答卷和两票</button>}
        {onFinaleVotes && !finale.all_sealed && <button type="button" className={button} disabled={locked || view.pending_ai || !view.table_decisions?.available || !view.table_decisions.options.some(o => o.action === 'SEAL_FINALE')} onClick={() => { if (!locked && !view.pending_ai && view.table_decisions?.available) onFinaleVotes(Boolean(finaleVotesNeedsConfirmation)); }}>{finaleVotesNeedsConfirmation ? '确认，重新请求未提交角色' : '请其他角色一起提交'}</button>}
        {finaleVotesNeedsConfirmation && <p className="text-sm leading-6 text-brass">原请求已停止等待。确认后会为每个尚未提交的角色发起一次新互动，已封存的答卷保留。</p>}
        {finaleVotesProgress && <p role="status" className="text-sm text-mist">{finaleVotesProgress}</p>}
      </div>
    </section>}
    {(section === 'all' || section === 'finale') && !view.settled && view.table_decisions && (view.table_decisions.options.length > 0 || view.table_decisions.requests.length > 0) && <section aria-label="AI 正式提交" className={box}>
      <h2 className="text-lg font-semibold">其他角色的决定</h2><p className="text-sm text-mist">{onFinaleVotes && full.phase_kind === 'FINALE' ? '终局使用上方“请其他角色一起提交”。角色按自己的已知信息依次提交；失败时停止，保留已提交的结果。' : '让角色按自己的已知信息提交；提交失败会保留未提交状态。'}</p>
      {!view.table_decisions.available && <p role="status">当前暂时无法开始新的 AI 互动，已有记录仍可阅读。</p>}
      <div className="flex flex-wrap gap-3">{view.table_decisions.options.filter(o => o.action !== 'SEAL_FINALE' || !onFinaleVotes).map(o => <button key={`${o.character_id}-${o.action}`} type="button" className={button}
        disabled={locked || view.pending_ai || !view.table_decisions?.available || !onDecide}
        onClick={() => onDecide?.(o.character_id, o.action)}>请{name(o.character_id)}{o.action === 'SEAL_FINALE' ? '封卷' : o.action === 'BREAK_TIE' ? '裁决平票' : '投票'}</button>)}</div>
      {view.table_decisions.requests.slice(-5).map(r => <p key={r.request_id} className="text-sm">{name(r.character_id)}：{statusText[r.status]}
        {r.status === 'PENDING' && <button type="button" className={`${button} ml-3`} disabled={locked || !onDecide} onClick={() => onDecide?.(r.character_id, r.action)}>检查这次请求</button>}</p>)}
    </section>}

  </>;
}
