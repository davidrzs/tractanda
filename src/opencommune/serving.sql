-- The serving database: one SQLite file that `opencommune build` writes from scratch and the Rust
-- server only reads. Model answers enter only as enumerated fields, page numbers, and the title and
-- body as printed, in which the classifier wrote private names as [name] (prompt record
-- classify-65e415a748e2). The server's integration tests create their database from this file.

-- Communes the archive covers: the configured ones and those reached through archive collectors.
CREATE TABLE IF NOT EXISTS communes (
    bfs_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    canton TEXT NOT NULL,
    language TEXT,                  -- de, fr, it, rm; NULL when not configured
    website TEXT,
    vendor TEXT,
    publicity_regime TEXT,
    lat REAL,
    lon REAL
);

-- One row per run that produced fields of `documents` or `sections`. `settings` holds every
-- setting that changes what the run saw (resolution, page sampling, model); `note` says in plain
-- sentences what those settings do to the data.
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,            -- the prompt version, for example classify-65e415a748e2
    stage TEXT NOT NULL,            -- classification
    model TEXT,
    settings TEXT NOT NULL,         -- JSON
    note TEXT NOT NULL,
    documents INTEGER NOT NULL      -- documents with an answer from this run
);

-- Every classified file, once, keyed by its content. Files without an answer from the run are not
-- listed. Naturalisation documents and print views of service pages are not listed either
-- (decisions 0014, 0018).
CREATE TABLE IF NOT EXISTS documents (
    sha256 TEXT PRIMARY KEY,
    title TEXT,                     -- the title as printed, as the classifier read it; private names written as [name]
    body_name TEXT,                 -- the body as printed, as the classifier read it; private names written as [name]
    first_seen TEXT NOT NULL,       -- ISO date of the first download
    listing_date TEXT,              -- ISO date the commune's listing gives, if any and not after the build day
    page_count INTEGER,
    run_id TEXT NOT NULL REFERENCES runs (id), -- the run that gave the fields below
    kind TEXT,                      -- meeting_booklet | bulletin | standalone
    body_kind TEXT,                 -- parliament | citizens_assembly | executive | commission | commune | other_public_body | private | unknown
    date TEXT,                      -- ISO date the classifier read, if any and not after the build day
    years TEXT,                     -- comma-separated years the document concerns, as the classifier read them, none after the build year
    year INTEGER,                   -- the year of date; else the one year in years; else of listing_date; else NULL
    languages TEXT,                 -- comma-separated: de, fr, it, rm, en, other
    personal_data INTEGER NOT NULL, -- 1 when the classifier saw personal data, else 0; never shown
    pages_seen INTEGER              -- pages the classifier saw; fewer than page_count above 150 pages
);
CREATE INDEX IF NOT EXISTS documents_year ON documents (year);

-- The title index for the search box: FTS5 with the trigram tokenizer over `title` and
-- `body_name`. A query matches any three or more characters, also inside a word, and ignores
-- diacritics. The table indexes `documents` in place (external content): it keeps no copy of the
-- text, and its rowid is the rowid of `documents`. The build fills it once all documents are
-- written: INSERT INTO documents_fts (documents_fts) VALUES ('rebuild'). Nothing changes
-- `documents` after that. Nothing may VACUUM this file either, as VACUUM can renumber those rowids.
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    title, body_name, content = 'documents', tokenize = 'trigram remove_diacritics 1'
);

-- Every commune that links a file, with the URL it uses. A file can belong to several communes
-- and a commune has many files.
CREATE TABLE IF NOT EXISTS document_communes (
    sha256 TEXT NOT NULL REFERENCES documents (sha256),
    bfs_id INTEGER NOT NULL REFERENCES communes (bfs_id),
    source_url TEXT,                -- NULL when the URL is not a public http(s) URL
    PRIMARY KEY (sha256, bfs_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS document_communes_commune ON document_communes (bfs_id, sha256);

-- What a classified file is made of: its sections in page order. The family groups the types for
-- display and for the commune overview; build.py holds the one mapping.
CREATE TABLE IF NOT EXISTS sections (
    sha256 TEXT NOT NULL REFERENCES documents (sha256),
    position INTEGER NOT NULL,      -- 1, 2, ... in the order the classifier gave them
    type TEXT NOT NULL,             -- minutes | decisions | vote_result | budget | accounts | invitation_agenda | proposal | member_request | regulation | report | notice | plan_or_map | news | other | unclear
    family TEXT NOT NULL,           -- deliberation | money | proposals | rules | information | other
    first_page INTEGER NOT NULL,
    last_page INTEGER NOT NULL,
    partly_unseen INTEGER NOT NULL, -- 1 when the section spans pages the classifier never saw
    pages INTEGER NOT NULL,         -- pages of the document the section counts: its range, less pages an earlier section counts
    PRIMARY KEY (sha256, position)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS sections_family ON sections (family, sha256);
CREATE INDEX IF NOT EXISTS sections_type ON sections (type, sha256);

-- The coverage register (data/register/<CANTON>.yaml), one row per commune.
CREATE TABLE IF NOT EXISTS register (
    bfs_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    canton TEXT NOT NULL,
    status TEXT NOT NULL,
    website TEXT,
    vendor TEXT,
    reachable INTEGER,              -- 1, 0 or NULL
    checked TEXT,                   -- ISO date
    checked_by TEXT,
    notes TEXT,
    -- The note the commune list shows (build.register_note).
    note TEXT
);
CREATE INDEX IF NOT EXISTS register_canton ON register (canton, bfs_id);

-- The register's publications, in file order.
CREATE TABLE IF NOT EXISTS register_publications (
    bfs_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    body TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    listing_url TEXT,
    "from" TEXT,
    "to" TEXT,
    cadence TEXT,
    obstacle TEXT,
    how TEXT,
    PRIMARY KEY (bfs_id, position)
) WITHOUT ROWID;

-- Current access failures, one row per commune and website: the latest check of any address on
-- the host, attributed to the one register commune whose website is there. The row links to the
-- website, never to a document.
CREATE TABLE IF NOT EXISTS access_observations (
    bfs_id INTEGER NOT NULL,
    source_url TEXT NOT NULL,       -- scheme://host/
    status TEXT NOT NULL,           -- restricted | rate_limited | robots_unavailable | technical_error
    checked_at TEXT NOT NULL,       -- ISO timestamp with offset
    robots_url TEXT NOT NULL,
    restricted_at TEXT
);
CREATE INDEX IF NOT EXISTS access_observations_commune
    ON access_observations (bfs_id, checked_at DESC);

-- The released versions of the data, oldest first: the ledger data/releases.csv as it stood when
-- this file was built. The version this file belongs to is released after it is built, so its row
-- is not here; the full-text index carries it (`opencommune release`, release.py).
CREATE TABLE IF NOT EXISTS releases (
    version TEXT PRIMARY KEY,       -- 2026.11.26; 2026.11.26.2 for a second release that day
    date TEXT NOT NULL,             -- ISO date of the release
    documents INTEGER NOT NULL,     -- documents listed
    pages INTEGER NOT NULL,         -- their pages
    communes INTEGER NOT NULL,      -- communes with at least one listed document
    searchable_pages INTEGER NOT NULL, -- pages in the full-text index
    ocr_pages INTEGER NOT NULL,     -- of those, pages whose text came from OCR
    classification_run TEXT NOT NULL,
    git_commit TEXT,                -- the code that built the version; "-dirty" when tracked files differed
    note TEXT                       -- for the maintainers, in English; the site does not show it
);

-- Files first downloaded each day, from the URL index: every file, also those not classified yet.
CREATE TABLE IF NOT EXISTS downloads (
    day TEXT PRIMARY KEY,           -- ISO date, UTC
    files INTEGER NOT NULL
) WITHOUT ROWID;

-- How this file was built: built_at (ISO timestamp, UTC) and git_commit (as in releases; empty
-- outside a git checkout).
CREATE TABLE IF NOT EXISTS build (key TEXT PRIMARY KEY, value TEXT NOT NULL);
