// The document list's state, kept in the page's URL query so that a filtered view can be linked.
export const PAGE_SIZE = 20;
// The most matching pages a full-text search ranks (server/API.md, `GET /api/pages`).
export const TEXT_CAP = 20000;
// the text field, the selectors, the family and the first part's family (set from the commune
// page's chart) and the commune (set from a commune page's text search), by their API parameter
export const FILTERS = [
  'q',
  'section',
  'family',
  'first_family',
  'body',
  'year',
  'canton',
  'commune'
] as const;
// `terms`: the words the list searches when they are not the visitor's own: the full-text search the
// answer above the results made (search page), set when the visitor's words found nothing.
// `exact`: the visitor asked for their own words; the list then keeps them.
export type Filters = Record<(typeof FILTERS)[number], string> & {
  offset: number;
  terms: string;
  exact: boolean;
};

export function readFilters(url: URL): Filters {
  const get = (key: string) => url.searchParams.get(key)?.trim() || '';
  const year = get('year');
  const canton = get('canton').toUpperCase();
  const commune = get('commune');
  return {
    q: get('q'),
    section: get('section'),
    family: get('family'),
    first_family: get('first_family'),
    body: get('body'),
    year: /^\d{4}$/.test(year) ? year : '',
    canton: /^[A-Z]{2}$/.test(canton) ? canton : '',
    commune: /^\d+$/.test(commune) ? commune : '',
    offset: Math.max(0, Math.floor(Number(url.searchParams.get('offset')) || 0)),
    terms: get('terms'),
    exact: get('exact') === '1'
  };
}

// The /api/documents query for these filters: empty ones left out, one page of PAGE_SIZE. It has
// no canton filter. `commune`: the commune page's commune, else the commune filter.
export function documentQuery(filters: Filters, commune?: string) {
  const query = new URLSearchParams();
  if (commune || filters.commune) query.set('commune', commune || filters.commune);
  for (const key of ['section', 'family', 'first_family', 'body', 'year', 'q'] as const)
    if (filters[key]) query.set(key, filters[key]);
  if (filters.terms) query.set('q', filters.terms);
  query.set('offset', String(filters.offset));
  query.set('limit', String(PAGE_SIZE));
  return query;
}

// The /api/pages query (full-text search): the words first, then the filters that are set. Pages
// have no first part: `first_family` applies to the document list only.
export function textQuery(filters: Filters) {
  const query = new URLSearchParams();
  for (const key of FILTERS)
    if (filters[key] && key !== 'first_family') query.set(key, filters[key]);
  if (filters.terms) query.set('q', filters.terms);
  query.set('offset', String(filters.offset));
  query.set('limit', String(PAGE_SIZE));
  return query;
}

export const filtered = (filters: Filters) => FILTERS.some((key) => filters[key]);

// Text as runs of plain and marked text; `ranges` are [start, end) offsets in code points
// (Array.from). Svelte writes each run as text: nothing in it is read as HTML.
export function marked(text: string, ranges: [number, number][]) {
  const chars = Array.from(text);
  const parts: { text: string; marked: boolean }[] = [];
  let at = 0;
  for (const [start, end] of [...ranges].sort((a, b) => a[0] - b[0])) {
    const from = Math.max(start, at);
    const to = Math.min(end, chars.length);
    if (to <= from) continue;
    if (from > at) parts.push({ text: chars.slice(at, from).join(''), marked: false });
    parts.push({ text: chars.slice(from, to).join(''), marked: true });
    at = to;
  }
  if (at < chars.length) parts.push({ text: chars.slice(at).join(''), marked: false });
  return parts;
}

// One character without case or accents; one code point stays one, so offsets carry over.
function fold(char: string) {
  const folded = char.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();
  return Array.from(folded).length === 1 ? folded : char;
}
const WORD = '[\\p{L}\\p{N}]';

// Where a search's words stand in a text (a title), as code point ranges for `marked`; case and
// accents do not matter. `words`: read as the full-text search reads them (whole words, "a
// phrase", word* a prefix, punctuation between words); else, as the title search, the whole text
// anywhere. Neighbouring ranges with only spaces between them become one.
export function findWords(text: string, q: string, words: boolean): [number, number][] {
  const folded = Array.from(text).map(fold).join('');
  const foldedQ = Array.from(q.trim()).map(fold).join('');
  if (!foldedQ) return [];
  let pattern: RegExp;
  if (words) {
    const terms: string[] = [];
    for (const m of foldedQ.matchAll(/"([^"]*)"?(\*?)|([^\s"]+)/g)) {
      const term = m[1] ?? m[3];
      const tokens = term.match(/[\p{L}\p{N}]+/gu);
      if (!tokens) continue;
      const prefix = m[2] === '*' || term.endsWith('*');
      terms.push(`(?<!${WORD})${tokens.join(`[^\\p{L}\\p{N}]+`)}${prefix ? '' : `(?!${WORD})`}`);
    }
    if (!terms.length) return [];
    pattern = new RegExp(terms.join('|'), 'gu');
  } else pattern = new RegExp(foldedQ.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gu');
  const ranges: [number, number][] = [];
  for (const m of folded.matchAll(pattern)) {
    if (!m[0]) continue;
    const start = Array.from(folded.slice(0, m.index)).length;
    const end = start + Array.from(m[0]).length;
    const last = ranges.at(-1);
    if (last && /^\s*$/u.test(Array.from(folded).slice(last[1], start).join(''))) last[1] = end;
    else ranges.push([start, end]);
  }
  return ranges;
}

// The original through this site at a commune's address, opened at a page when one is given.
export function originalLink(url: string | null, commune?: number, page?: number) {
  if (!url) return '';
  const query = new URLSearchParams();
  if (commune !== undefined) query.set('commune', String(commune));
  if (page !== undefined) query.set('page', String(page));
  const search = String(query);
  return search ? `${url}${url.includes('?') ? '&' : '?'}${search}` : url;
}
