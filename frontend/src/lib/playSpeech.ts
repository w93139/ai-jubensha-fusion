/** Presentation only. Entries must already be validated and audible to this viewer. */
export interface PlaySpeechEntry {
  id: string;
  sequence: number;
  text: string;
}

export const SPEECH_LINE_LENGTH = 24;
export const SPEECH_LINE_DELAY_MS = 5000;

export function splitSpeechLines(text: string): string[] {
  return text.split('\n').flatMap(paragraph => {
    const characters = Array.from(paragraph);
    if (!characters.length) return [''];
    const lines: string[] = [];
    for (let start = 0; start < characters.length; start += SPEECH_LINE_LENGTH) {
      lines.push(characters.slice(start, start + SPEECH_LINE_LENGTH).join(''));
    }
    return lines;
  });
}

interface QueuedSpeech {
  entry: PlaySpeechEntry;
  lines: string[];
  shown: number;
}

export interface PlaySpeechState {
  scope: string;
  initialized: boolean;
  seenThrough: number;
  queue: QueuedSpeech[];
  paused: boolean;
}

export function createPlaySpeechState(scope: string): PlaySpeechState {
  return { scope, initialized: false, seenThrough: 0, queue: [], paused: false };
}

export function sameSpeech(a: PlaySpeechEntry, b: PlaySpeechEntry): boolean {
  return a.id === b.id && a.sequence === b.sequence && a.text === b.text;
}

function startHead(queue: QueuedSpeech[]): QueuedSpeech[] {
  if (queue[0]?.shown === 0) queue = [{ ...queue[0], shown: 1 }, ...queue.slice(1)];
  // A completed head stays for one interval only if another entry is waiting.
  // This keeps a batch of short replies from all appearing at once.
  if (queue.length === 1 && queue[0].shown === queue[0].lines.length) return [];
  return queue;
}

/** undefined is loading/untrusted; [] is a trusted, empty historical baseline. */
export function syncPlaySpeech(
  state: PlaySpeechState, scope: string, entries: readonly PlaySpeechEntry[] | undefined,
  instant = false,
): PlaySpeechState {
  if (state.scope !== scope) state = createPlaySpeechState(scope);
  if (entries === undefined) return state;
  const seenThrough = entries.reduce((maximum, entry) => Math.max(maximum, entry.sequence), state.seenThrough);
  if (!state.initialized) return { ...state, initialized: true, seenThrough };
  let queue = state.queue.filter(item => entries.some(entry => sameSpeech(entry, item.entry)));
  const fresh = entries.filter(entry => entry.sequence > state.seenThrough)
    .slice().sort((a, b) => a.sequence - b.sequence);
  queue = instant ? [] : startHead([...queue, ...fresh.map(entry => ({
    entry: { ...entry }, lines: splitSpeechLines(entry.text), shown: 0,
  }))]);
  const paused = queue.length > 0 && state.paused;
  if (seenThrough === state.seenThrough && paused === state.paused
      && queue.length === state.queue.length && queue.every((item, index) => item === state.queue[index])) return state;
  return { ...state, seenThrough, queue, paused };
}

export type PlaySpeechAction =
  | { type: 'sync'; scope: string; entries: readonly PlaySpeechEntry[] | undefined; instant: boolean }
  | { type: 'tick'; scope: string; id: string; shown: number }
  | { type: 'pause' | 'resume' | 'skip'; scope: string };

export function playSpeechReducer(state: PlaySpeechState, action: PlaySpeechAction): PlaySpeechState {
  if (action.type === 'sync') return syncPlaySpeech(state, action.scope, action.entries, action.instant);
  if (action.scope !== state.scope) return state;
  if (action.type === 'skip') return { ...state, queue: [], paused: false };
  if (action.type === 'pause' || action.type === 'resume') {
    return state.queue.length ? { ...state, paused: action.type === 'pause' } : state;
  }
  const head = state.queue[0];
  if (action.type !== 'tick' || !head || state.paused
      || action.id !== head.entry.id || action.shown !== head.shown) return state;
  const queue = startHead(head.shown < head.lines.length
    ? [{ ...head, shown: head.shown + 1 }, ...state.queue.slice(1)]
    : state.queue.slice(1));
  return { ...state, queue, paused: queue.length > 0 && state.paused };
}

/** A memory may appear as soon as every line of its causal utterance is visible. */
export function pendingSpeechSequences(state: PlaySpeechState): number[] {
  return state.queue.filter(item => item.shown < item.lines.length).map(item => item.entry.sequence);
}
