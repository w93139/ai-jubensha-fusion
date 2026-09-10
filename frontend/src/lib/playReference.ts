export interface PlayReferenceMaterial {
  id: string;
  text: string;
}

export interface PlayReferenceSection {
  id: string;
  materialId: string;
  title: string;
  /** An unchanged slice of the authorized material, including its heading. */
  text: string;
}

const taskTitle = /^(?:你的(?:目的|目标|任务)|(?:本|当前)阶段(?:任务|目标)|个人(?:任务|目标))$/;
const memoryTriggerTitle = /^你的回忆(?:（共\d+条）|\(共\d+条\))$/;
const rulesTitle = /^(?:《[^》\n]+》\s*)?(?:玩家(?:共用)?规则|游戏规则)$/;
const plainSectionTitle = /^(?:你的(?:表现|回忆(?:[（(]共\d+条[）)])?|经历|秘密|故事)|你已经知道的其他人|(?:人物|角色|故事|个人)背景|(?:人物|角色)关系|背景(?:介绍|资料)?|(?:行动|调查|游戏|阅读)规则|其他(?:资料|信息)|线索|回忆|秘密|注意事项|附[：:].+|第[一二三四五六七八九十\d]+(?:阶段|幕))$/;

interface Heading {
  title: string;
  level: number;
}

function headingAt(lines: string[], index: number): Heading | undefined {
  const line = lines[index].trim();
  const markdown = line.match(/^(#{1,6})\s+(.+?)(?:\s+#+)?$/);
  let title = markdown?.[2] ?? line;
  if (!markdown && line && /^(?:={3,}|-{3,})$/.test(lines[index + 1]?.trim() ?? '')) {
    return { title: line.replace(/[：:]$/, ''), level: lines[index + 1].trim()[0] === '=' ? 1 : 2 };
  }
  const bold = title.match(/^(?:\*\*(.+)\*\*|__(.+)__)$/);
  if (bold) title = bold[1] ?? bold[2];
  title = title.trim().replace(/[：:]$/, '');
  if (markdown || bold || taskTitle.test(title) || rulesTitle.test(title) || plainSectionTitle.test(title)) {
    return { title, level: markdown ? markdown[1].length : 0 };
  }
  return undefined;
}

function headings(text: string): { start: number; heading: Heading }[] {
  const lines = text.split('\n');
  const result: { start: number; heading: Heading }[] = [];
  let offset = 0;
  let fence: string | undefined;
  for (let index = 0; index < lines.length; index++) {
    const marker = lines[index].trim().match(/^(`{3,}|~{3,})/);
    if (marker) {
      if (!fence) fence = marker[1][0];
      else if (marker[1][0] === fence) fence = undefined;
    } else if (!fence) {
      const heading = headingAt(lines, index);
      if (heading) result.push({ start: offset, heading });
    }
    offset += lines[index].length + 1;
  }
  return result;
}

function extractSections(materials: readonly PlayReferenceMaterial[], titlePattern: RegExp): PlayReferenceSection[] {
  return materials.flatMap(material => {
    const positions = headings(material.text);
    return positions.flatMap(({ start, heading }, index) => {
      if (!titlePattern.test(heading.title)) return [];
      // A new section ends this slice even when it is nested. We do not infer
      // that an unrelated heading belongs here or rewrite OCR text.
      const end = positions[index + 1]?.start ?? material.text.length;
      return [{ id: JSON.stringify([material.id, start]), materialId: material.id,
        title: heading.title, text: material.text.slice(start, end).trimEnd() }];
    });
  });
}

/** Only the current actor's private, authorized materials belong here. */
export function extractTaskSections(materials: readonly PlayReferenceMaterial[]): PlayReferenceSection[] {
  return extractSections(materials, taskTitle);
}

/** This is a reading aid, never a grant or a source of unlocked memory cards. */
export function extractMemoryTriggerSections(materials: readonly PlayReferenceMaterial[]): PlayReferenceSection[] {
  return extractSections(materials, memoryTriggerTitle);
}

/** Rules are separate public source materials, never a source of actor tasks. */
export function extractRuleSections(materials: readonly PlayReferenceMaterial[]): PlayReferenceSection[] {
  return materials.flatMap(material => {
    const positions = headings(material.text);
    const first = positions[0];
    // Only an explicit document title qualifies; a quoted mention in prose
    // or a later rules heading does not classify an unrelated document.
    if (!first || material.text.slice(0, first.start).trim() || !rulesTitle.test(first.heading.title)) return [];
    const nextDocument = first.heading.level > 0
      ? positions.slice(1).find(item => item.heading.level > 0 && item.heading.level <= first.heading.level)
      : undefined;
    return [{ id: JSON.stringify([material.id, first.start]), materialId: material.id,
      title: first.heading.title, text: material.text.slice(first.start, nextDocument?.start).trimEnd() }];
  });
}
