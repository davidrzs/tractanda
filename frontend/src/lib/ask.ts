// The chatbot in the browser: solve the server's proof of work, send the question, read the
// answer's server-sent events (server/src/routes/ask.rs). Nothing is stored in the browser.

export type AskEvent =
  | { kind: 'step'; tool: string; args: Record<string, unknown> }
  | { kind: 'answer'; text: string }
  | { kind: 'end'; outcome: string };

/** Why a question was not taken: a refusal code of the server, or `broken`. */
export class Refused extends Error {
  constructor(public reason: string) {
    super(reason);
  }
}

type Challenge = { challenge: string; bits: number };

/** A challenge to solve. */
export async function challenge(signal?: AbortSignal): Promise<Challenge> {
  const response = await fetch('/api/ask/challenge', { signal, cache: 'no-store' });
  if (!response.ok) throw new Refused(await reasonOf(response));
  return (await response.json()) as Challenge;
}

async function reasonOf(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { reason?: string };
    return body.reason ?? 'broken';
  } catch {
    return 'broken';
  }
}

function leadingZeroBits(bytes: Uint8Array): number {
  let bits = 0;
  for (const byte of bytes) {
    if (byte === 0) {
      bits += 8;
      continue;
    }
    return bits + Math.clz32(byte) - 24;
  }
  return bits;
}

/** A decimal solution whose SHA-256 of `<challenge>:<solution>` starts with `bits` zero bits. */
export async function solve(text: string, bits: number, signal?: AbortSignal): Promise<string> {
  const encoder = new TextEncoder();
  const batch = 256;
  for (let start = 0; ; start += batch) {
    signal?.throwIfAborted();
    const tries = Array.from({ length: batch }, (_, i) => start + i);
    const digests = await Promise.all(
      tries.map((n) => crypto.subtle.digest('SHA-256', encoder.encode(`${text}:${n}`)))
    );
    const found = digests.findIndex((d) => leadingZeroBits(new Uint8Array(d)) >= bits);
    if (found >= 0) return String(tries[found]);
  }
}

/** Ask `question` with the proof of work `solved`: each event goes to `onEvent` as it arrives.
 * Throws `Refused`. */
export async function ask(
  question: string,
  lang: string,
  solved: { challenge: string; solution: string },
  onEvent: (event: AskEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  let response: Response;
  try {
    response = await fetch('/api/ask', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ question, lang, ...solved }),
      signal
    });
  } catch (e) {
    if (signal?.aborted) throw e;
    throw new Refused('broken');
  }
  if (!response.ok || !response.body) throw new Refused(await reasonOf(response));
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let end: number;
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const event = parse(block);
      if (event) onEvent(event);
    }
  }
}

function parse(block: string): AskEvent | null {
  let name = '';
  let data = '';
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) name = line.slice(6).trim();
    else if (line.startsWith('data:')) data += line.slice(5).trim();
  }
  if (!name || !data) return null; // a keep-alive comment
  try {
    const value = JSON.parse(data);
    if (name === 'step') return { kind: 'step', tool: value.tool, args: value.args ?? {} };
    if (name === 'answer') return { kind: 'answer', text: value.text };
    if (name === 'end') return { kind: 'end', outcome: value.outcome };
  } catch {
    return null;
  }
  return null;
}

// The answer as Markdown is shown without HTML: paragraphs and list items, inside them bold text
// and links. Links are kept only to this site's originals (/original/…) and pages (/de/…); any
// other link, and anything that could leave the site (//host, a backslash), shows as its text.
export type Inline = { text: string; bold?: boolean; href?: string };

export function inline(text: string, origin: string): Inline[] {
  const parts: Inline[] = [];
  const pattern = /\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)\s]+)\)/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > last) parts.push({ text: text.slice(last, match.index) });
    if (match[1] !== undefined) parts.push({ text: match[1], bold: true });
    else parts.push({ text: match[2], href: ownLink(match[3], origin) });
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push({ text: text.slice(last) });
  return parts;
}

function ownLink(url: string, origin: string): string | undefined {
  if (url.includes('\\')) return undefined;
  try {
    const parsed = new URL(url, origin);
    if (parsed.origin !== origin) return undefined;
    if (!/^\/(original|de|fr|it|en)(\/|$)/.test(parsed.pathname)) return undefined;
    return parsed.pathname + parsed.search;
  } catch {
    return undefined;
  }
}

/** Paragraphs and lists of an answer. */
export function blocks(text: string, origin: string): { list: boolean; lines: Inline[][] }[] {
  const out: { list: boolean; lines: Inline[][] }[] = [];
  for (const line of text.split('\n').map((l) => l.trim())) {
    if (!line) {
      out.push({ list: false, lines: [] });
      continue;
    }
    const item = line.match(/^(?:[-*•]|\d+[.)])\s+(.*)$/);
    const list = item !== null;
    const content = inline(item ? item[1] : line, origin);
    const previous = out.at(-1);
    if (previous && previous.list === list && (list || previous.lines.length > 0))
      previous.lines.push(content);
    else out.push({ list, lines: [content] });
  }
  return out.filter((b) => b.lines.length > 0);
}
