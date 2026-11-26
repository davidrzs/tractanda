// one row of /api/communes: the map, the commune search and the start page's list
export type Commune = {
  bfs_id: number;
  name: string;
  canton: string;
  language: string | null;
  website: string | null;
  lat: number | null;
  lon: number | null;
  documents: number;
  // the coverage register's headline status (docs/REGISTER.md) and its note
  register_status: string | null;
  register_note: string | null;
};
// one entry per website, newest check first; source_url is the website, never a document
export type Access = {
  status: 'restricted' | 'rate_limited' | 'robots_unavailable' | 'technical_error';
  checked_at: string;
  restricted_at: string | null;
  stale: boolean;
  robots_url: string;
  source_url: string;
};
// a row of /api/register
export type RegisterEntry = {
  bfs_id: number;
  name: string;
  canton: string;
  status: string;
  website: string | null;
  vendor: string | null;
  reachable: boolean | null;
  checked: string | null;
  checked_by: string | null;
  notes: string | null;
  publications: {
    body: string;
    kind: string;
    status: string;
    listing_url: string | null;
    from: string | null;
    to: string | null;
    cadence: string | null;
    obstacle: string | null;
    how: string | null;
  }[];
};
// the commune page (/api/communes/{bfs_id})
export type CommuneDetail = {
  bfs_id: number;
  name: string;
  canton: string;
  language: string | null;
  website: string | null;
  vendor: string | null;
  lat: number | null;
  lon: number | null;
  documents: number;
  register: RegisterEntry | null;
  access: Access[];
  holdings: Holding[];
};
// a commune's holdings per year and section family; newest year first. `documents`: documents
// with a section of the family (a document can count for several families). `pages`: the pages of
// the family's sections, each page counted once; `first_part_documents`: documents by their first
// section. The last two add up across the families of a year; servers before them leave them out.
export type Holding = {
  year: number;
  family: string;
  documents: number;
  pages?: number;
  first_part_documents?: number;
};
export const normalize = (s: string) =>
  s
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase();
export const hasDocuments = (c: Commune) => c.documents > 0;
// the register's status for a commune whose robots.txt refuses the crawler: hatched on the map
export const restricted = (c: Commune) => c.register_status === 'blocked_robots';

// The classifier's vocabularies (src/opencommune/serving.sql), in the order the selectors list them.
export const SECTION_TYPES = [
  'minutes',
  'decisions',
  'vote_result',
  'budget',
  'accounts',
  'invitation_agenda',
  'proposal',
  'member_request',
  'regulation',
  'report',
  'notice',
  'plan_or_map',
  'news',
  'other',
  'unclear'
] as const;
export const BODY_KINDS = [
  'parliament',
  'citizens_assembly',
  'executive',
  'commission',
  'commune',
  'other_public_body',
  'private',
  'unknown'
] as const;
// the six colour families the section types fall into, in display order
export const FAMILIES = [
  'deliberation',
  'money',
  'proposals',
  'rules',
  'information',
  'other'
] as const;
export const LANGUAGES = ['de', 'fr', 'it', 'rm', 'en', 'other'] as const;
export type SectionType = (typeof SECTION_TYPES)[number];
export type BodyKind = (typeof BODY_KINDS)[number];
export type Family = (typeof FAMILIES)[number];
export type Language = (typeof LANGUAGES)[number];

export type Section = {
  type: string;
  family: string;
  first_page: number;
  last_page: number;
  partly_unseen: boolean;
};
// one file of /api/documents; every listed file is classified. `personal_data` is part of the
// API but never shown.
export type Document = {
  sha256: string;
  // the title as printed, read by the classifier; private names appear as "[name]"
  title: string | null;
  // the body as printed
  body_name: string | null;
  first_seen: string;
  listing_date: string | null;
  date: string | null;
  year: number | null;
  years: number[];
  page_count: number | null;
  pages_seen: number | null;
  run_id: string | null;
  body_kind: string | null;
  languages: string[];
  personal_data: boolean | null;
  sections: Section[];
  communes: { bfs_id: number; name: string; canton: string }[];
  original_url: string | null;
};
export type Facet = { value: string | number | null; count: number };
export type DocumentPage = {
  total: number;
  offset: number;
  limit: number;
  // each facet is counted with every filter except its own
  facets: {
    years: Facet[];
    sections: Facet[];
    bodies: Facet[];
  };
  items: Document[];
};
// A page that matches a full-text search (/api/pages). `snippet` is plain text; `highlights` are
// the matched words in it as [start, end) offsets in characters (code points, not UTF-16 units).
export type PageHit = {
  page: number;
  section_types: string[];
  snippet: string;
  highlights: [number, number][];
  original_url: string;
};
// a document of a full-text search: how many of its pages match, and its three best pages
export type TextDocument = Document & { matching_pages: number; pages: PageHit[] };
export type TextPage = {
  total: number;
  // true when the search reached its cap of matching pages: the counts cover only the best ones
  capped: boolean;
  offset: number;
  limit: number;
  // each facet counts the documents of the result, with every filter applied
  facets: {
    years: Facet[];
    sections: Facet[];
    cantons: Facet[];
    communes: { bfs_id: number; name: string; canton: string; count: number }[];
  };
  items: TextDocument[];
};
// one row of /api/runs
export type Run = {
  id: string;
  stage: string;
  model: string | null;
  settings: unknown;
  note: string;
  documents: number;
};
// one released version of the data, from /api/releases
export type Release = {
  version: string;
  date: string;
  documents: number;
  pages: number;
  communes: number;
  searchable_pages: number;
  ocr_pages: number;
  classification_run: string;
  git_commit: string | null;
  note: string | null;
};
// /api/releases: `current` is the version the site serves, null when it serves unreleased files
export type Releases = {
  current: string | null;
  releases: Release[];
  downloads: { day: string; files: number }[];
};
