/** Player-selected clues only. Stored references never grant access to a material or image. */
export type ClueVisual = { id: string; label: string };
export type CollectedClue = {
  collection: 'knowledge' | 'evidence'; materialId: string; title: string; text: string; visualIds: string[];
};
export type AvailableClue = CollectedClue & { visuals: ClueVisual[] };
export type ClueCollection = { version: 1; entries: CollectedClue[] };
type StorageProvider = () => Pick<Storage, 'getItem' | 'setItem'>;
type Failure = { ok: false; error: string };
const storage = () => window.localStorage;
const stable = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}$/.test(value);

export const clueId = (item: Pick<CollectedClue, 'collection' | 'materialId'>) => `${item.collection}:${item.materialId}`;
export const emptyClueCollection = (): ClueCollection => ({ version: 1, entries: [] });
export function clueCollectionKey(owner: string | null, play: string, character: string): string | null {
  if (![owner, play, character].every(value => typeof value === 'string' && value.trim() && value.length <= 1000)) return null;
  return `fusion:play-clues:1:${encodeURIComponent(JSON.stringify([owner, play, character]))}`;
}
function validEntry(value: unknown): value is CollectedClue {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const item = value as CollectedClue;
  return Object.keys(item).sort().join(',') === 'collection,materialId,text,title,visualIds'
    && ['knowledge', 'evidence'].includes(item.collection) && stable(item.materialId)
    && typeof item.title === 'string' && item.title.trim().length > 0 && item.title.length <= 500
    && typeof item.text === 'string' && item.text.trim().length > 0 && item.text.length <= 80_000
    && Array.isArray(item.visualIds) && item.visualIds.length <= 1000 && item.visualIds.every(stable)
    && new Set(item.visualIds).size === item.visualIds.length;
}
export function validClueCollection(value: unknown): value is ClueCollection {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const document = value as ClueCollection;
  return Object.keys(document).sort().join(',') === 'entries,version' && document.version === 1
    && Array.isArray(document.entries) && document.entries.length <= 100 && document.entries.every(validEntry)
    && new Set(document.entries.map(clueId)).size === document.entries.length;
}
export function loadClueCollection(key: string | null, source: StorageProvider = storage): { ok: true; document: ClueCollection } | Failure {
  if (!key) return { ok: false, error: '请登录并确认当前角色后使用线索收藏。' };
  try {
    const raw = source().getItem(key);
    if (raw === null) return { ok: true, document: emptyClueCollection() };
    if (raw.length > 50_000_000) return { ok: false, error: '收藏数据过大，原数据未被覆盖。' };
    const document: unknown = JSON.parse(raw);
    return validClueCollection(document) ? { ok: true, document } : { ok: false, error: '收藏格式无法读取，原数据未被覆盖。' };
  } catch { return { ok: false, error: '无法读取本机收藏，请检查浏览器存储权限。原数据未被覆盖。' }; }
}
export function saveClueCollection(key: string | null, document: ClueCollection, source: StorageProvider = storage): { ok: true } | Failure {
  if (!key || !validClueCollection(document)) return { ok: false, error: '收藏格式或数量不符合要求，尚未保存。' };
  try { source().setItem(key, JSON.stringify(document)); return { ok: true }; }
  catch { return { ok: false, error: '本机保存失败，当前改动尚未保存。请检查存储空间或权限后重试。' }; }
}
export function addCollectedClue(document: ClueCollection, incoming: AvailableClue): { ok: true; document: ClueCollection; duplicate: boolean } | Failure {
  const entry = { collection: incoming.collection, materialId: incoming.materialId, title: incoming.title, text: incoming.text, visualIds: [...incoming.visualIds] };
  if (!validClueCollection(document) || !validEntry(entry)) return { ok: false, error: '这条线索无法收藏。' };
  const previous = document.entries.find(item => clueId(item) === clueId(entry));
  if (previous) {
    if (resolveCollectedClue(previous, [incoming])) return { ok: true, document, duplicate: true };
    // A new explicit selection can replace an inaccessible old snapshot with
    // the currently authorized, source-corrected material; no silent migration.
    return { ok: true, document: { version: 1, entries: document.entries.map(item => clueId(item) === clueId(entry) ? entry : item) }, duplicate: false };
  }
  if (document.entries.length >= 100) return { ok: false, error: '最多收藏 100 条，请先取消一条收藏。' };
  return { ok: true, document: { version: 1, entries: [...document.entries, entry] }, duplicate: false };
}
export function resolveCollectedClue(saved: CollectedClue, available: AvailableClue[]): AvailableClue | undefined {
  // Fail closed on an absent/changed projection. Render current authorized text
  // and labels, never content or image URLs recovered from browser storage.
  return available.find(item => clueId(item) === clueId(saved) && item.text === saved.text
    && item.visualIds.length === saved.visualIds.length && item.visualIds.every(id => saved.visualIds.includes(id)));
}
