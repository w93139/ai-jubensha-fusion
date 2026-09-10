/** Local-only player notes. This module has no API or model transport. */
export const NOTEBOOK_NOTE_LIMIT = 20_000;
export const NOTEBOOK_CLIP_LIMIT = 100;
export const NOTEBOOK_CLIP_TEXT_LIMIT = 20_000;

export type NotebookClip = { id: string; title: string; text: string };
export type NotebookDocument = { version: 1; note: string; clips: NotebookClip[] };
export type NotebookStorage = Pick<Storage, 'getItem' | 'setItem'>;
type StorageProvider = () => NotebookStorage;
type Failure = { ok: false; error: string };

export function notebookKey(ownerId: string | null, playId: string, characterId: string): string | null {
  if (![ownerId, playId, characterId].every(value => typeof value === 'string' && value.trim().length > 0 && value.length <= 1_000)) return null;
  // JSON tuple encoding avoids collisions between identifiers containing delimiters.
  return `fusion:play-notebook:1:${encodeURIComponent(JSON.stringify([ownerId, playId, characterId]))}`;
}

export function emptyNotebook(): NotebookDocument {
  return { version: 1, note: '', clips: [] };
}

function exactKeys(value: unknown, keys: string[]): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
}

export function validNotebookClip(value: unknown): value is NotebookClip {
  return exactKeys(value, ['id', 'title', 'text'])
    && typeof value.id === 'string' && value.id.trim().length > 0 && value.id.length <= 1_000
    && typeof value.title === 'string' && value.title.trim().length > 0 && value.title.length <= 500
    && typeof value.text === 'string' && value.text.trim().length > 0 && value.text.length <= NOTEBOOK_CLIP_TEXT_LIMIT;
}

export function validNotebookDocument(value: unknown): value is NotebookDocument {
  return exactKeys(value, ['version', 'note', 'clips']) && value.version === 1
    && typeof value.note === 'string' && value.note.length <= NOTEBOOK_NOTE_LIMIT
    && Array.isArray(value.clips) && value.clips.length <= NOTEBOOK_CLIP_LIMIT
    && value.clips.every(validNotebookClip)
    && new Set(value.clips.map(clip => clip.id)).size === value.clips.length;
}

function browserStorage(): NotebookStorage {
  return window.localStorage; // Access itself can throw in restricted browsers.
}

export function loadNotebook(key: string | null, storage: StorageProvider = browserStorage):
  { ok: true; document: NotebookDocument; exists: boolean } | Failure {
  if (!key) return { ok: false, error: '登录并进入当前角色后才能使用本机笔记。' };
  try {
    const raw = storage().getItem(key);
    if (raw === null) return { ok: true, document: emptyNotebook(), exists: false };
    // Bound parsing work, including JSON escaping at the largest supported size.
    if (raw.length > 12_500_000) return { ok: false, error: '本机笔记数据过大，未覆盖原数据。' };
    const document: unknown = JSON.parse(raw);
    if (!validNotebookDocument(document)) return { ok: false, error: '本机笔记格式无法读取，未覆盖原数据。' };
    return { ok: true, document, exists: true };
  } catch {
    return { ok: false, error: '无法读取本机笔记，请检查浏览器存储权限后重试。原数据未被覆盖。' };
  }
}

export function saveNotebook(key: string | null, document: NotebookDocument, storage: StorageProvider = browserStorage):
  { ok: true } | Failure {
  if (!key) return { ok: false, error: '登录并进入当前角色后才能保存本机笔记。' };
  if (!validNotebookDocument(document)) return { ok: false, error: '笔记或收藏超过限制，尚未保存。' };
  try {
    storage().setItem(key, JSON.stringify(document));
    return { ok: true };
  } catch {
    return { ok: false, error: '本机保存失败，当前修改尚未保存。请检查存储空间或权限，重试或先导出。' };
  }
}

export function addNotebookClip(document: NotebookDocument, clip: NotebookClip):
  { ok: true; document: NotebookDocument; duplicate: boolean } | Failure {
  if (!validNotebookDocument(document) || !validNotebookClip(clip)) return { ok: false, error: '这条收藏格式不受支持，未加入笔记本。' };
  if (document.clips.some(item => item.id === clip.id)) return { ok: true, document, duplicate: true };
  if (document.clips.length >= NOTEBOOK_CLIP_LIMIT) return { ok: false, error: '收藏已达 100 条，请先移除一条再收藏。' };
  return { ok: true, document: { ...document, clips: [...document.clips, { id: clip.id, title: clip.title, text: clip.text }] }, duplicate: false };
}

export function notebookExportText(document: NotebookDocument, playId: string, characterId: string): string {
  return [
    '我的剧本杀笔记', `本局：${playId}`, `角色：${characterId}`,
    '本机保存；不会发给 AI；不同浏览器不同步。', '', '【笔记】', document.note || '（暂无笔记）',
    '', '【收藏】', ...document.clips.flatMap((clip, index) => [`${index + 1}. ${clip.title}`, clip.text, '']),
  ].join('\n');
}
