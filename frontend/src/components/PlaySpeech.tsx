import type { ReactNode } from 'react';

import { sameSpeech } from '../lib/playSpeech';
import type { PlaySpeechEntry } from '../lib/playSpeech';

export interface PlaySpeechPlayback {
  render(entry: PlaySpeechEntry): ReactNode;
  pendingSequences: number[];
  skipAll(): void;
}

/**
 * Scope must bind owner, play and character. Pass only the current verified
 * audible view; undefined while unavailable, including before the first load.
 * Authorized replies are immediately complete. No playback state is retained
 * across loads or scopes, and this hook never changes game state.
 */
export function usePlaySpeech(
  scope: string, entries: readonly PlaySpeechEntry[] | undefined,
): PlaySpeechPlayback {
  return {
    pendingSequences: [],
    skipAll() { /* Compatibility: all authorized text is already visible. */ },
    render(entry) {
      if (!entries?.some(item => sameSpeech(item, entry))) return null;
      return <div key={`${scope}:${entry.id}`} className="min-w-0 space-y-2">
        <p className="whitespace-pre-wrap break-words leading-7">{entry.text}</p>
      </div>;
    },
  };
}
