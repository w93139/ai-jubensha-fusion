import type { PackagePlay } from '@/types/packagePlay';

/** Select the latest actual human statement, never an AI reply or an older eligible substitute. */
export function latestHumanStatement(view: PackagePlay) {
  return view.discussion?.entries.filter(entry => entry.speaker === view.selected_character_id
    && entry.phase_id === view.current_phase.id).reduce<(NonNullable<PackagePlay['discussion']>['entries'][number]) | undefined>(
    (latest, entry) => !latest || entry.sequence > latest.sequence ? entry : latest, undefined);
}

const normalized = (text: string) => text.normalize('NFKC').toLowerCase().replace(/[\s\p{P}\p{S}]/gu, '');
export function questionClarification(text: string): string | undefined {
  const value = normalized(text);
  if (/^(?:请问|請問|请告诉我|請告訴我)?(?:我是谁|我是誰|我是什么人|我是什麼人|whoami)$/.test(value)) {
    return '你想核对自己所扮演的角色身份，还是请对方自我介绍？请把问题写明确后发送。';
  }
  if (/^(?:请问|請問|请告诉我|請告訴我)?(?:[他她它这這那]是[谁誰]|whoishe|whoisshe)$/.test(value)) {
    return '你指的是哪位人物？请写出名字或具体事件，再继续询问。';
  }
  return undefined;
}

/** Player-facing guidance, not a substitute for server admission. */
export function questionRefusal(text: string): string | undefined {
  const compact = text.normalize('NFKC').toLowerCase().replace(/[ \t\r]/g, '');
  if (/隐藏目标|隱藏目標|系统提示|系統提示|系统指令|系統指令|(?:忽略|无视|绕过).{0,8}(?:规则|指令|权限)|(?:完整|全部|原始).{0,6}(?:回忆卡|私本|角色本)|(?:ignore|bypass).{0,12}(?:rules|instructions|permissions)/i.test(compact)
    || /(?:^|[。！？!?；;\n])(?:(?:请|帮我|请你|能不能|你能|麻烦)){0,3}(?:(?:写|生成|调试|运行)(?:一段|一篇|一些)?(?:python代码|javascript代码|sql代码|广告文案|色情)|(?:查|查询|查看|搜索)(?:今天|明天|当前|现在)的?(?:天气)|(?:推荐|分析|预测)(?:一下|一些|几只)?(?:股票|基金)|(?:教我|告诉我如何|指导我)(?:如何|怎么)?(?:(?:制作|制造)(?:炸弹|爆炸物)|(?:入侵|盗取)(?:他人|别人)(?:账号|手机)))/.test(compact)) {
    return '这里只讨论本剧本的人物、线索和经过。请换一个与当前调查有关的问题。';
  }
  return undefined;
}

// Suggestions only. Never dispatch a model request or infer facts from lexical overlap.
// Each result must still be displayed and explicitly confirmed as its canonical question.
const stop = /^(?:什么|什麼|是谁|是誰|怎么|怎麼|为什么|為什|你们|我们|这个|那个|可以|知道|记得|想问|一下|请问|事情|问题|告诉|是否|能够|目前|现在|之前|之后|发生|哪些|时候)$/;
const grams = (text: string) => {
  const result = new Set<string>();
  for (const run of text.normalize('NFKC').toLowerCase().match(/[\p{L}\p{N}]+/gu) || []) {
    if (/^[a-z0-9]+$/.test(run)) { if (run.length >= 3) result.add(run); continue; }
    for (let i = 0; i < run.length - 1; i++) { const token = run.slice(i, i + 2); if (!stop.test(token)) result.add(token); }
  }
  return result;
};
export function suggestQuestions(view: PackagePlay, text: string, channel: 'PUBLIC' | 'PRIVATE', peer?: string) {
  if (!view.single_player?.available || view.settled || questionClarification(text) || questionRefusal(text) || !text.trim() || Array.from(text).length > 1000) return [];
  const query = grams(text);
  const candidates = view.single_player.topics.flatMap(topic => topic.responders
    .filter(role => role.channels.includes(channel) && (channel !== 'PRIVATE' || role.character_id === peer))
    .flatMap(role => role.intents.filter(intent => intent.available).map(intent => {
      const tokens = grams(`${topic.title} ${intent.label} ${intent.question}`);
      const overlap = [...query].filter(token => tokens.has(token)).length;
      const exact = normalized(text) === normalized(intent.question) || normalized(text) === normalized(topic.title);
      return { topic_id: topic.id, character_id: role.character_id, intent_id: intent.id,
        title: topic.title, question: intent.question, score: exact ? 1000 : overlap };
    })));
  return candidates.filter(candidate => candidate.score >= 2).sort((a, b) => b.score - a.score).slice(0, 6);
}
