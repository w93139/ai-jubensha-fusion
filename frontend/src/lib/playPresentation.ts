/** Player-only presentation. Never use for stored sources, rules or model input. */
export function playerMaterialText(text: string): string {
  return text.replace(/\r\n?/g, '\n')
    // Supplemental IDs identify editorial provenance, not an original card number.
    // Only strip a title ID when its next line explicitly declares that provenance.
    .replace(/^\s*(?:#{1,6}\s*)?\d+-R\s*[|｜]\s*([^\n]+)\n(?=\s*编辑修订版\s*v?\d)/gmi, '## $1\n')
    .replace(/^\s*编辑修订版\s*v?\d+(?:\.\d+)*(?:\s*[|｜]\s*(?:新增线索|补充线索|非原\d+号(?:线索)?卡?))*\s*$/gmi, '')
    .replace(/^(\s*)编辑修订版\s*v?\d+(?:\.\d+)*[。．]\s*/gmi, '$1')
    .trim();
}

/** Only the requested ending advertisement, including its OCR spelling variant. */
export function playerEndingText(text: string): string {
  return text.replace(/\r\n?/g, '\n')
    // Authored branch predicates and transition instructions are not story prose.
    .split('\n').filter(line => !/^\s*(?:结局[一二三四五六七八九十\d]+\s*)?[〔【\[]编辑(?:修订条件|衔接[·：:][^〕】\]]+)[〕】\]]/.test(line)).join('\n')
    .replace(/享受正版游戏[，,、\s]*体验开心过程\s*zhile[yv]uanbg\.cn\s*$/i, '').replace(/\n{3,}/g, '\n\n').trim();
}

/** Omit the known editorial instructions appended to authored host explanations. */
export function playerHostText(text: string): string {
  return text.replace(/^本题的主持答案是[：:]\s*/, '')
    .replace(/本提示只回答本日主案，不替你选择正式指认或信任。/g, '')
    .replace(/这只回答“帮助谁、为什么”，不替你决定信任票。/g, '')
    .replace(/不要求凭现有材料硬报精确分钟；49-R单卡不能独证身份、操作者或时刻。/g, '仅凭桌边残留，还不能确认是谁布置及准确时间。')
    .replace(/，也不要自行补上原文没有的精确昏迷时长/g, '').trim();
}

export function phaseThinkingTime(kind?: string): string {
  return kind === 'READING' ? '15–25' : kind === 'INVESTIGATION' ? '20–35' : '10–20';
}
