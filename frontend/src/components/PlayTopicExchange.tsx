import { useState } from 'react';
import type { ReactNode } from 'react';
import type { PlaySpeechEntry } from '@/lib/playSpeech';
import { UserRound } from 'lucide-react';
import PlayText from './PlayText';
import { canTopic } from '@/lib/playTopics';
import { questionClarification, questionRefusal, suggestQuestions } from '@/lib/playQuestionGuide';
import type { PackagePlay, TopicAction } from '@/types/packagePlay';

export default function PlayTopicExchange({ view, channel, peer, questionText, locked, recoveryLocked, pendingReplyKey, onTopic, onReply, renderSpeech }: {
  view: PackagePlay; channel: 'PUBLIC' | 'PRIVATE'; peer?: string; locked: boolean; recoveryLocked: boolean;
  renderSpeech?: (entry: PlaySpeechEntry) => ReactNode;
  pendingReplyKey?: string; onTopic?: (action: TopicAction) => void; onReply?: (turnId: string) => void;
  questionText?: string;
}) {
  const scope = `${view.play_id}:${view.selected_character_id}:${view.current_phase.id}:${channel}:${peer || ''}:${channel === 'PRIVATE' ? view.full_game?.call?.id || '' : ''}`;
  const [draft, setDraft] = useState({ scope, text: '' });
  const query = questionText ?? (draft.scope === scope ? draft.text : '');
  const selectionScope = `${scope}:${query}`;
  const [selection, setSelection] = useState({ scope: selectionScope, topic: '', character: '', intent: '' });
  const clarification = questionRefusal(query) || questionClarification(query);
  const suggestions = suggestQuestions(view, query, channel, peer);
  const current = selection.scope === selectionScope ? selection : { scope: selectionScope, topic: '', character: '', intent: '' };
  const single = view.single_player;
  if (!single) return null;
  const topics = single.topics.filter(topic => topic.responders.some(responder => responder.channels.includes(channel)
    && (channel !== 'PRIVATE' || responder.character_id === peer)));
  const topic = topics.find(item => item.id === current.topic);
  const responders = topic?.responders.filter(item => item.channels.includes(channel) && (channel !== 'PRIVATE' || item.character_id === peer)) || [];
  const responder = responders.find(item => item.character_id === (channel === 'PRIVATE' ? peer : current.character));
  const intent = responder?.intents.find(item => item.id === current.intent && item.available);
  const action: TopicAction | undefined = topic && responder && intent ? { action: 'ASK_TOPIC', payload: {
    topic_id: topic.id, character_id: responder.character_id, intent_id: intent.id, channel,
  } } : undefined;
  const disabled = locked || Boolean(pendingReplyKey) || !onTopic || !action || !canTopic(view, action);
  const turns = single.turns.filter(turn => turn.channel === channel && (channel === 'PRIVATE' ? turn.character_id === peer : turn.phase_id === view.current_phase.id));
  const name = (id: string) => view.characters.find(character => character.id === id)?.name || '其他角色';
  const button = 'min-h-11 rounded-lg border border-brass/40 px-4 py-2 text-sm text-brass hover:bg-raised disabled:opacity-40';
  const choices = (label: string, value: string, options: { value: string; label: string }[], onChange: (value: string) => void, kind: 'topic' | 'character' | 'question') => (
    <fieldset disabled={locked || Boolean(pendingReplyKey)} className="min-w-0" aria-label={label}>
      <legend className="mb-2 text-sm text-mist">{label}</legend>
      <div className={kind === 'question' ? 'grid grid-cols-1 gap-2 sm:grid-cols-2' : 'flex flex-wrap gap-2'}>
        {options.map(option => <label key={option.value} className={`relative block min-w-0 cursor-pointer ${kind === 'question' ? '' : kind === 'character' ? 'min-w-24' : 'max-w-full'}`}>
          <input type="radio" name={`${scope}:${label}`} value={option.value} checked={value === option.value}
            disabled={locked || Boolean(pendingReplyKey)} onChange={() => { if (!locked && !pendingReplyKey) onChange(option.value); }}
            className="peer sr-only" />
          <span className={`flex min-h-11 items-center gap-2 border border-line px-3 py-2 text-sm text-paper transition-colors hover:border-brass/60 peer-checked:border-brass peer-checked:bg-brass/15 peer-checked:text-brass peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-brass peer-disabled:cursor-not-allowed peer-disabled:opacity-40 ${kind === 'topic' ? 'rounded-full' : 'rounded-lg'}`}>
            {kind === 'character' && <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brass/10"><UserRound aria-hidden="true" className="h-4 w-4 text-brass" /></span>}
            <span className="min-w-0 break-words [overflow-wrap:anywhere]">{option.label}</span>
            <span aria-hidden="true" className={`ml-auto shrink-0 ${value === option.value ? 'visible' : 'invisible'}`}>✓</span>
          </span>
        </label>)}
      </div>
    </fieldset>
  );
  return <section aria-label={channel === 'PUBLIC' ? '本轮议题交流' : '私聊议题'} className="min-w-0 space-y-4 rounded-lg border border-line bg-panel p-4">
    <h2 className="font-semibold">{channel === 'PUBLIC' ? '本轮议题交流' : '私聊议题'}</h2>
    <p className="text-sm leading-6 text-mist">可以用自己的话描述疑问，再确认具体问法；也可以直接点选本轮议题。</p>
    {channel === 'PRIVATE' && !view.settled && <label className="block text-sm text-mist">你想问什么<textarea rows={2} maxLength={1000} value={query} disabled={locked || !view.full_game?.call}
      onChange={event => setDraft({ scope, text: event.target.value })} className="mt-2 w-full rounded border border-line bg-ink p-3 text-paper" /></label>}
    {query.trim() && single.available && <div aria-label="问题匹配建议" className="space-y-2 rounded border border-brass/40 p-3">
      <p role="status" className="text-sm leading-6 text-brass">{clarification || (suggestions.length ? '这些问题可能与你的疑问有关。请选择问法和角色，确认后再发送。' : '暂时不能确定你想问哪件事。请补充人物、地点或线索，或直接选择下方议题。')}</p>
      {!clarification && suggestions.map(candidate => <button key={`${candidate.topic_id}:${candidate.character_id}:${candidate.intent_id}`} type="button" className={`${button} block w-full text-left`} disabled={locked || Boolean(pendingReplyKey)}
        onClick={() => { if (!locked && !pendingReplyKey) setSelection({ scope: selectionScope, topic: candidate.topic_id, character: candidate.character_id, intent: candidate.intent_id }); }}>
        <span className="block font-medium">询问{name(candidate.character_id)} · {candidate.title}</span><span className="mt-1 block text-paper">{candidate.question}</span>
      </button>)}
    </div>}
    {channel === 'PUBLIC' && view.ai_interactions && <p className="text-xs text-mist">本局 AI 互动：已发起 {view.ai_interactions.initiated} / 最多 {view.ai_interactions.limit} 轮。重看已保存的回答不增加轮次。</p>}
    {!single.available ? <p role="status" className="text-sm text-mist">当前暂不能发起议题交流，已保存的记录仍可回看。</p>
      : topics.length === 0 ? <p className="text-sm text-mist">当前没有可交流的议题。继续阅读或调查，取得相关线索后再来查看。</p>
      : <div className="space-y-3">
        {choices('当前议题', topic?.id || '', topics.map(item => ({ value: item.id, label: item.title })),
          id => setSelection({ scope: selectionScope, topic: id, character: channel === 'PRIVATE' ? peer || '' : '', intent: '' }), 'topic')}
        {topic && channel === 'PUBLIC' && choices('交流对象', responder?.character_id || '', responders.map(item => ({ value: item.character_id, label: name(item.character_id) })),
          id => setSelection({ ...current, character: id, intent: '' }), 'character')}
        {responder && choices('本次问题', intent?.id || '', responder.intents.filter(item => item.available).map(item => ({ value: item.id, label: item.label })),
          id => setSelection({ ...current, intent: id }), 'question')}
        {responder && !responder.intents.some(item => item.available) && <p className="text-sm text-mist">当前没有可发送的问题，可先核对已保存的回答。</p>}
        {intent && <div aria-label="将要发送的问题" className="rounded border border-line p-3 text-sm"><PlayText text={intent.question} /></div>}
        <button type="button" className={button} disabled={disabled} onClick={() => { if (!disabled && action) onTopic?.(action); }}>发送所选问题</button>
      </div>}
    {turns.length > 0 && <details key={`${scope}:${turns.at(-1)?.id}:${turns.at(-1)?.status}`} open><summary className="min-h-11 cursor-pointer py-2 text-sm text-brass">已保存的议题 · {turns.length}</summary><ol aria-label="已保存的议题" className="space-y-3">{[...turns].reverse().map(turn => {
      const checking = pendingReplyKey === turn.reply_request?.idempotency_key || turn.status === 'PENDING';
      const canReply = Boolean(onReply && turn.reply_request && (checking || (single.available && turn.status === 'READY' && !turn.can_fallback)));
      const fallback: TopicAction = { action: 'USE_FALLBACK', payload: { turn_id: turn.id } };
      const publicReply = channel === 'PUBLIC' && turn.status === 'OK' ? view.role_responses?.entries.find(entry => entry.reply_to === turn.reply_to && entry.speaker === turn.character_id) : undefined;
      const fixedReply = channel === 'PUBLIC' && turn.status === 'FALLBACK' ? view.discussion?.entries.find(entry => entry.speaker === turn.character_id
        && entry.phase_id === turn.phase_id && entry.text === turn.answer && entry.sequence > (view.discussion?.entries.find(question => question.id === turn.reply_to)?.sequence ?? view.revision)) : undefined;
      return <li key={turn.id} className="space-y-2 rounded border border-line p-3 text-sm">
        <p className="font-medium">{turn.title} · {name(turn.character_id)}</p><PlayText text={turn.question} />
        <p className="text-xs text-mist">{turn.status === 'FAILED' && ['UNKNOWN', 'EXPIRED'].includes(turn.receipt_status || '') ? '原请求未取得可用结果，已停止等待。可查看固定答复继续，本操作不会重新调用 AI。' : ({ READY: turn.can_fallback ? '问题已保存，当前可查看该议题固定答复。' : '问题已保存，等待角色回答。', PENDING: '角色回答正在处理，可核对原请求。', OK: '角色说法已保存，请结合线索判断。', FAILED: '这次回答未完成，可查看该议题固定答复。', FALLBACK: '已采用该议题固定答复。' })[turn.status]}</p>
        {publicReply && <div aria-label="角色答复" className="rounded-lg bg-raised p-3"><p className="mb-2 text-xs text-brass">{name(turn.character_id)} · 角色说法</p>{renderSpeech ? renderSpeech(publicReply) : <PlayText text={publicReply.text} />}</div>}
        {channel === 'PUBLIC' && turn.status === 'FALLBACK' && turn.answer && <div aria-label="固定答复" className="rounded-lg bg-raised p-3"><p className="mb-2 text-xs text-brass">{name(turn.character_id)} · 固定答复，仍是角色说法</p>{renderSpeech && fixedReply ? renderSpeech(fixedReply) : <PlayText text={turn.answer} />}</div>}
        {canReply && <button type="button" className={button} disabled={checking ? recoveryLocked : locked || Boolean(pendingReplyKey)}
          onClick={() => { if (!(checking ? recoveryLocked : locked || Boolean(pendingReplyKey))) onReply?.(turn.id); }}>{checking ? '检查这次议题回答' : '请角色回答'}</button>}
        {turn.can_fallback && <button type="button" className={button} disabled={locked || Boolean(pendingReplyKey) || !onTopic || !canTopic(view, fallback)}
          onClick={() => { if (!locked && !pendingReplyKey && onTopic && canTopic(view, fallback)) onTopic(fallback); }}>查看该议题固定答复</button>}
      </li>;
    })}</ol></details>}
  </section>;
}
