export type GamePhase =
  | 'CHARACTER_SELECTION' | 'SCRIPT_READING' | 'BACKGROUND' | 'INTRODUCTION'
  | 'EVIDENCE_ROUND_1' | 'INVESTIGATION' | 'EVIDENCE_ROUND_2' | 'DISCUSSION'
  | 'VOTING' | 'RUNOFF_VOTING' | 'REVELATION' | 'ENDED';

export interface PublicScript {
  id: number; title: string; description?: string; player_count: number;
  duration_minutes: number; difficulty: string; category: string; cover_image_url?: string;
}

export interface Character {
  id: number; name: string; age?: number; profession?: string; gender?: string;
  background?: string; secret?: string; objective?: string; is_murderer?: boolean;
}

export interface Evidence { id: number; name: string; description: string; location: string; visibility?: string }

export interface FusionState {
  session: { session_id: string; current_phase: GamePhase; last_event_id: number };
  script: PublicScript;
  phase: GamePhase;
  characters: Character[];
  participants: Array<{ character_id: number; character_name: string; type: 'HUMAN' | 'AI'; ready: boolean }>;
  locations: Array<{ id: number; name: string; description?: string }>;
  public_evidence: Evidence[];
  private_evidence: Evidence[];
  background?: { title?: string; setting?: string; incident?: string; rules?: string };
  my_role?: Character;
  revelation?: { murderer?: Character; verdict?: number; human_vote?: number; truth?: Record<string, unknown> };
  last_event_id: number;
}

export interface FusionEvent { event_id: number; session_id: string; type: string; timestamp: string; payload: Record<string, unknown> }

