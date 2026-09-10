import type { PlayMaterial } from '@/types/packagePlay';

export type PerformanceGroup = {
  kind: 'performance';
  id: string;
  heading: PlayMaterial;
  introduction?: PlayMaterial;
  items: PlayMaterial[];
};
export type PerformanceReadingBlock = PerformanceGroup | { kind: 'material'; id: string; material: PlayMaterial };

const title = /^(?:#{1,6}\s+)?(?:你的表现|\*\*你的表现\*\*|__你的表现__)[：:]?$/;
const section = /^(?:#{1,6}\s|你的(?:目的|目标|任务|表现|回忆|经历|秘密|故事)|你已经知道的其他人|附[：:]|第[一二三四五六七八九十\d]+(?:阶段|幕))/;
const number = (text: string) => text.trimStart().match(/^(\d+)(?:[、．）)]\s*|\.\s+)(?=\S)/)?.[1];
const privateOnly = (item: PlayMaterial) => item.disclosure === 'KEEP_PRIVATE' && !item.can_share && !item.shared_by_character_id;

/** Presentation only: pass the current actor's authorized private knowledge.
 * Keep original objects, order and text. An unsupported structure stays as-is.
 */
export function performanceReadingBlocks(materials: readonly PlayMaterial[]): PerformanceReadingBlock[] {
  const blocks: PerformanceReadingBlock[] = [];
  for (let index = 0; index < materials.length; index++) {
    const heading = materials[index];
    let next = index + 1;
    let introduction: PlayMaterial | undefined;
    const items: PlayMaterial[] = [];
    if (privateOnly(heading) && title.test(heading.text.trim())) {
      const candidate = materials[next];
      if (candidate && privateOnly(candidate) && !number(candidate.text) && !section.test(candidate.text.trim())) {
        introduction = candidate;
        next++;
      }
      while (materials[next] && number(materials[next].text)) {
        const item = materials[next];
        if (!privateOnly(item) || Number(number(item.text)) !== items.length + 1) {
          items.length = 0;
          break;
        }
        items.push(item);
        next++;
      }
    }
    if (items.length) {
      blocks.push({ kind: 'performance', id: heading.id, heading, introduction, items });
      index = next - 1;
    } else blocks.push({ kind: 'material', id: heading.id, material: heading });
  }
  return blocks;
}
