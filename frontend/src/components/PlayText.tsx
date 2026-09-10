import { playerMaterialText } from '@/lib/playPresentation';
import { Fragment } from 'react';

const blockStart = /^(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|\d+、|>\s|```|\||\*\*[^*]+\*\*$|__[^_]+__$)/;
const cjk = /[\u3400-\u9fff]/;
const readingHeading = /^(?:你的(?:表现|目的|回忆(?:（共\d+条）)?)|你已经知道的其他人|附[：:]名词解释|第[一二三四五六七八九十\d]+(?:阶段|幕))$/;

/** Reading-only layout repair. Never use this output for storage, triggers or model inputs. */
export function normalizePlayText(text: string): string {
  const lines = playerMaterialText(text).split('\n');
  const out: string[] = [];
  let blanks = 0;
  for (const raw of lines) {
    const line = raw.trim().replace(/([\u3400-\u9fff，。！？；：、“”《》]) +(?=[\u3400-\u9fff，。！？；：、“”《》])/g, '$1');
    if (!line) { blanks++; continue; }
    const last = out.at(-1) || '';
    const prose = last && !blockStart.test(last) && !blockStart.test(line) && !readingHeading.test(last) && !readingHeading.test(line);
    // OCR often inserts even blank lines inside a Chinese sentence. A terminal
    // punctuation mark or structural marker always retains its paragraph boundary.
    const join = prose && cjk.test(last) && cjk.test(line)
      && !/[。！？!?：:；;][”’」』）)]?$/.test(last)
      && (blanks === 0 || /[\u3400-\u9fff，、]$/.test(last));
    const left = Array.from(last).at(-1) || '';
    const right = Array.from(line)[0] || '';
    const wordBoundary = /[\p{L}\p{N}]/u.test(left) && /[\p{L}\p{N}]/u.test(right) && !cjk.test(left) && !cjk.test(right);
    if (join) out[out.length - 1] = last + (wordBoundary ? ' ' : '') + line;
    else { if (out.length && blanks) out.push(''); out.push(line); }
    blanks = 0;
  }
  return out.join('\n');
}

function inline(text: string) {
  return text.split(/(\*\*[^*\n]+\*\*|__[^_\n]+__|`[^`\n]+`|\*[^*\n]+\*)/g).map((part, i) => {
    if ((part.startsWith('**') && part.endsWith('**')) || (part.startsWith('__') && part.endsWith('__'))) return <strong key={i} className="font-bold text-paper">{part.slice(2, -2)}</strong>;
    if (part.startsWith('`') && part.endsWith('`')) return <code key={i} className="rounded bg-white/5 px-1">{part.slice(1, -1)}</code>;
    if (part.startsWith('*') && part.endsWith('*')) return <em key={i}>{part.slice(1, -1)}</em>;
    return <Fragment key={i}>{part}</Fragment>;
  });
}

export default function PlayText({ text }: { text: string }) {
  const lines = normalizePlayText(text).split('\n');
  return <div className="min-w-0 break-words text-[15px] leading-8 [overflow-wrap:anywhere]">
    {lines.map((line, i) => {
      if (!line) return null;
      if (readingHeading.test(line)) return <h3 key={i} className="mb-3 mt-6 text-xl font-bold leading-relaxed">{line}</h3>;
      const heading = line.match(/^(#{1,6})\s+(.+?)(?:\s+#+)?$/);
      if (heading) {
        const Heading = heading[1].length <= 2 ? 'h3' : 'h4';
        return <Heading key={i} className={`mb-3 mt-6 font-bold leading-relaxed ${heading[1].length === 1 ? 'text-2xl' : heading[1].length === 2 ? 'text-xl' : 'text-lg'}`}>{inline(heading[2])}</Heading>;
      }
      if (/^[-*_]{3,}$/.test(line)) return <hr key={i} className="my-5 border-line" />;
      const list = line.match(/^(?:[-*+]\s+|\d+[.)]\s+|\d+、\s*)(.*)$/);
      if (list) return <p key={i} className="mb-2 pl-5 -indent-4"><span className="mr-2 text-brass">{line.match(/^\d+[.)、]/)?.[0] || '•'}</span>{inline(list[1])}</p>;
      if (/^>\s?/.test(line)) return <blockquote key={i} className="my-3 border-l-2 border-brass/60 pl-4 text-mist">{inline(line.replace(/^>\s?/, ''))}</blockquote>;
      return <p key={i} className="mb-4 last:mb-0">{inline(line)}</p>;
    })}
  </div>;
}
