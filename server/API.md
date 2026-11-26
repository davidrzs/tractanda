# API

The JSON API, MCP and the static front end, served from the serving database the Python build
writes (`src/opencommune/serving.sql`). Full-text search reads a second file, the index of page
text (`src/opencommune/fulltext.py`), when there is one. No key; every response carries
`X-Robots-Tag: noindex, nofollow` until `SEARCH_INDEXING` is on.

## Running

```sh
cd frontend && npm ci && npm run build          # Node is needed only to build
cd server && cargo build --release
DATABASE_PATH=data/opencommune.db STATIC_DIR=frontend/build server/target/release/opencommune-server
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_PATH` | `data/opencommune.db` | serving database, opened read-only; the server does not start without its tables |
| `FULLTEXT_PATH` | `fulltext.db` next to `DATABASE_PATH` | full-text index, attached read-only when the file exists and holds the index's tables; without it the full-text endpoints and tools answer that they are not available |
| `STATIC_DIR` | `frontend/build` | built front end; `index.html` answers every other path |
| `BIND` | `127.0.0.1:8000` | listen address |
| `USAGE_PATH` | `data/logs/usage.sqlite3` | usage statistics (writable, separate file) |
| `USAGE_ENABLED` | `true` | `false`: count nothing |
| `USAGE_VISITORS` | `true` | daily visitor estimates |
| `USAGE_TOKEN` | none | token of the operator's report `GET /api/usage` (at least 32 characters); without it the report answers 404 |
| `MCP_ALLOWED_HOSTS` | `localhost,127.0.0.1,::1` | `Host` values `/mcp` accepts; add the public domains |
| `PUBLIC_URL` | `http://` + `BIND` | the site's address in canonical links, the sitemap and `llms.txt` |
| `FORMER_HOSTS` | none | comma-separated former names of the site: a request to one is answered with a 308 to the same path under `PUBLIC_URL` (a page path without a language to the German page) and counted under that name |
| `SEARCH_INDEXING` | `false` | `true`: search engines may index the pages; `false`: every response says `noindex` |
| `SEARCH_INDEX_COMMUNES` | `false` | `true`: the commune and canton pages may be indexed too and join the sitemap; `false`: they say `noindex` |
| `RUST_LOG` | `info,rmcp=warn` | log filter; `debug` logs one line per request (route template only) |
| `ASK_API_KEY` | none | the chatbot's model key; without it `/api/ask` answers 503 (`off`) |
| `ASK_PROVIDER` | `anthropic` | `anthropic` (Messages API) or `openai` (any OpenAI-compatible endpoint, such as the CSCS inference platform) |
| `ASK_MODEL` | `claude-haiku-5-5` for `anthropic` | the model; required for `openai` |
| `ASK_BASE_URL` | the provider's own | the API root, for an OpenAI-compatible endpoint elsewhere |
| `ASK_EFFORT` | `low` | reasoning: `off`, `low`, `medium` or `high` |
| `ASK_PRICE_INPUT`, `ASK_PRICE_OUTPUT` | `0.10`, `0.50` | dollars per million input and output tokens, for the daily cap |
| `ASK_PRICE_INPUT_LONG`, `ASK_PRICE_OUTPUT_LONG` | `0.50`, `2.50` | the same for a call above 100,000 input tokens |
| `ASK_DAILY_USD` | `1` | dollars a day (UTC) the chatbot may spend; then it refuses questions until midnight UTC |
| `ASK_MODEL_CALLS_PER_MINUTE` | none | model calls a minute at most, for a provider with a request limit (the CSCS platform allows 15) |
| `ASK_OFF_FILE` | none | while this file exists the chatbot is off, without a restart; a path the server cannot check counts as off |
| `ASK_PATH` | `data/logs/ask.sqlite3` | the chatbot's questions (kept 90 days) and daily counts (writable, separate file) |

`DATASET_PATH` is no longer read.

## Endpoints

| Endpoint | Used by |
| --- | --- |
| `GET /api/communes` | map and commune list, MCP `list_communes` |
| `GET /api/communes/{bfs_id}` | commune page |
| `GET /api/documents` | document lists and search, MCP `search_documents` |
| `GET /api/documents/{sha256}` | one document, MCP `get_document` |
| `GET /api/pages` | full-text search, MCP `search_text` |
| `GET /api/documents/{sha256}/pages` | the text of pages, MCP `read_pages` |
| `GET /api/runs` | the runs behind the classification |
| `GET /api/releases` | the about page's versions of the data |
| `GET /api/openapi.json` | the OpenAPI 3.1 description of this API, for tools and the website's API page |
| `GET /api/register` | API users |
| `GET /original/{sha256}` | links in document lists |
| `POST /mcp` | AI agents |
| `GET /robots.txt`, `/sitemap.xml`, `/llms.txt` and the pages below | search engines, AI assistants |

Errors are JSON, `{"detail": "..."}`; invalid parameters answer 400, unknown `/api/...` paths 404,
and the full-text endpoints 503 when the server has no full-text index. Dates are `YYYY-MM-DD`.

### `GET /api/communes`

Every commune of the `communes` table, by canton and name: `bfs_id, name, canton, language,
website, lat, lon, documents, register_status, register_note`. `documents` is the commune's number
of rows in `document_communes`; a document several communes link counts for each.
`register_status` and `register_note` are the commune's `register` row's `status` and `note`, null
without a row. Communes only in the register are not listed.

### `GET /api/communes/{bfs_id}`

`bfs_id, name, canton, language, website, vendor, lat, lon, documents, register, access,
holdings`.
`register` is the commune's entry as in `/api/register`, or null. `access` is
`[{status, checked_at, robots_url, restricted_at, source_url, stale}]`, one entry per website,
newest check first; `source_url` is the website, never a document; an entry is `stale` when its
`restricted_at` (or `checked_at`) is 7 days old or more, computed at request time. The crawler keeps
a problem until the same address answers again; an address that changes from run to run (one with
the date in its query) never does, so a stale entry may be long resolved. It says nothing about
when the commune was last crawled. 404 when the
commune is not in the `communes` table. `holdings` is
`[{year, family, documents, pages, first_part_documents}]`, per year and section family, newest
year first, then family: `documents`, how many of the commune's documents of that year have a
section of that family (a document with sections of two families counts for both); `pages`, the
pages of those sections, a page two sections share counted for the first; `first_part_documents`,
the documents whose first section is of the family. `pages` and `first_part_documents` add up
across the families of a year. Documents without a year are not counted.

### `GET /api/documents`

Parameters, all optional; filters combine with AND, and a blank text filter is no filter:

1. `commune`: a BFS id; the documents the commune links (`document_communes`).
2. `section`: documents with at least one section of this type.
3. `family`: documents with at least one section of this family.
4. `first_family`: documents whose first section is of this family; across the families these add
   up to all documents (the commune page's chart counts documents this way).
5. `body`: `documents.body_kind`.
6. `year`: `documents.year` (the year of `date`, else the one year in `years`, else of
   `listing_date`).
7. `q`: text the title contains. From 3 characters on, the title index `documents_fts` answers:
   case and accents do not matter (`zurich` finds `Zürich`, and `Gemeinderat` also finds
   `Gemeinderäte`). Shorter text is matched with `LIKE`, case-insensitive for ASCII (SQLite
   `lower`), as trigrams need 3 characters. Either way `%`, `_` and `"` are plain characters.
8. `limit`: 0 to 100, default 20; `0` gives the total and the facets only.
9. `offset`: 0 or more, default 0.

```json
{"total": 0, "offset": 0, "limit": 20,
 "facets": {"years": [Count], "sections": [Count], "bodies": [Count]},
 "items": [Document]}
```

Order: `year` descending with no year last, then `date` (else `listing_date`) descending, then
`sha256`.

`Count` is `{value, count}`: how many of the matching documents have the value. Each facet is
counted with every filter applied except its own (`years` without `year`, `sections` without
`section`, `bodies` without `body`; `family`, `first_family` and `q` apply to every facet). Values are in ascending order, years newest first; a
NULL value is `"value": null` and comes last. `sections` counts documents, not sections: a document
with two sections of a type counts once, and a document without sections is in no entry.

`Document`: `sha256, title, body_name, first_seen, listing_date, date, year, years[int], page_count,
pages_seen, run_id, body_kind, languages[str], personal_data,
sections[{type, family, first_page, last_page, partly_unseen}], communes[{bfs_id, name, canton}],
original_url`. Every document is classified; files without an answer are not in the database.

1. `years` and `languages` are the comma-separated columns as lists, `[]` when NULL; a part of
   `years` that is not an integer is left out. No `date`, `listing_date` or year is later than the
   day the database was built: the build leaves a later one out as a misreading.
2. `title` and `body_name` are as printed, as the classifier read them, with private names written
   as `[name]`; null when the pages show none. `personal_data` is `true` or `false`; the website
   never shows it.
3. `sections` are in the classifier's order (`position`); `communes` are by name.
4. `original_url` is always `<PUBLIC_URL>/original/<sha256>`, a full link on this site (so that a
   client that does not know the host can hand it on), never a commune's URL. It answers 404 when no
   commune gives the document a linkable URL. `?commune=<bfs_id>` picks that commune's link and
   `?page=<n>` adds `#page=<n>`, which PDF viewers open at that page.
5. `title`, `body_name`, `body_kind`, `date`, `years`, `languages`, `personal_data` and `sections` come from an
   automatic classification that no person has checked yet; `run_id` names the run.

### `GET /api/documents/{sha256}`

The `Document` with `run`: `{id, stage, model, note}` of `run_id`, or null. 404 when unknown.

### `GET /api/pages`

Full-text search in the text of the pages. It needs the full-text index; without it the answer is
503. The index holds the pages that lie inside a section of the types `minutes`, `decisions`,
`vote_result`, `budget`, `accounts`, `proposal`, `invitation_agenda` and `member_request` and have
at least 20 characters of text. It leaves out bulletins and the documents of private bodies. Pages
without a text layer come in as OCR reaches them. The text is redacted: names, street addresses and
e-mail addresses are replaced by the marks `［name］`, `［address］` and `［email］` (`［secret］` for
passwords and keys), also where they are mentioned again later in the document, and dates of birth
and mobile numbers by `［date］` and `［phone］`, in snippets and in `/api/documents/{sha256}/pages`
alike.

Parameters; the filters are optional and combine with AND, and a blank text filter is no filter:

1. `q` (required): the words to find. Each page is searched on its own: words on two pages, or a
   phrase across a page break, do not match.
   1. Every word must occur on the page, in any order. `AND` between two words says the same.
   2. `"a phrase"` in double quotes must occur as written. An unclosed quote runs to the end.
   3. `word*` matches every word that begins with `word`; at least 3 letters or digits come
      before the `*`. A `*` after a phrase makes its last word a prefix. There is no stemming:
      `Kunstrasen` misses `Kunstrasens` and `Kunstrasenplatz`, which `Kunstrasen*` finds.
   4. `OR` between words, phrases or groups: either may occur. The words around it bind more
      tightly: `a b OR c` is `(a b) OR c`. `(…)` groups: `(Kunstrasen* OR Naturrasen*) Kredit`.
      An unclosed parenthesis runs to the end. The documents are not translated: to search every
      language, join each language's words with `OR`.
   5. `NEAR(a b, N)`: the words or phrases at most `N` words apart (0 to 1000, default 10), on
      the same page. Inside: words, phrases and prefixes only, at least two.
   6. `OR`, `AND` and `NEAR` are operators in capitals only; `"OR"` in quotes, or `or`, is a word.
   7. Case and accents do not matter: `ecole` finds `École`.
   8. Punctuation inside a word separates words: `d'engagement` is the phrase `d engagement`, and
      it also finds `d’engagement`. `^` and `:` are punctuation. A word without a letter or a
      digit is left out.
   9. 400, with a reason, for what would otherwise be searched other than as typed: a `*` other
      than at the end of a word or phrase (`*rasen`: words that end in a word cannot be
      searched), a word that starts with `-` (there is no exclusion), `NOT`, an `OR` or `AND`
      without a word on each side, a `)` without its `(`, an empty group, and `NEAR` with fewer
      than two words. 400 too when `q` has no word, more than 10 words and phrases, more than 500
      characters, or a prefix of fewer than 3 characters.

   The server reads `q` into a tree of words, phrases and operators and writes the FTS5 expression
   itself, each word and phrase as a quoted FTS5 string; nothing a user types reaches FTS5 as
   syntax.
2. `commune`, `year`, `body`: the document, as in `/api/documents`.
3. `canton`: a canton's code, such as `ZH`, in any case: documents linked by one of its communes.
4. `from`, `to`: `YYYY-MM-DD`, inclusive: the document's `date`, else its `listing_date`. When one
   is set, documents with neither are left out.
5. `section`, `family`: comma-separated: only pages inside a section of one of these types or
   families match. In `/api/documents` these filters take one value and select documents; here
   they select pages.
6. `exclude_section`, `exclude_family`: comma-separated: pages inside a section of one of these
   types or families do not match, as `exclude_section=accounts,budget`. A page inside two
   sections is left out when either is.
7. `language`: comma-separated language codes (`de`, `fr`, `it`, `rm`), in any case: documents
   whose `languages` hold one of them.
8. `document`: a document's `sha256`: only its pages match, to find where in a long document
   something is.
9. `sort`: `relevance` (default), `newest` or `oldest`: the documents by their `date`, else their
   `listing_date`, undated documents last; equal dates by relevance. 400 for another value.
10. `limit`: 0 to 50, default 10. `offset`: 0 or more, default 0.
11. `pages_per_document`: 1 to 20, default 3: how many of each document's matching pages the
   answer shows.
12. `commune_facets`: 0 to 3000, default 20: how many communes the `communes` facet lists; 3000
   lists them all.

```json
{"total": 0, "capped": false, "offset": 0, "limit": 10,
 "facets": {"years": [Count], "sections": [Count], "cantons": [Count],
            "communes": [{"bfs_id": 0, "name": "", "canton": "", "count": 0}]},
 "items": [TextDocument]}
```

1. `items` are the documents with a matching page, best first: by the relevance of their best page
   (FTS5 `bm25`), then by `sha256`; with `sort`, by date, then likewise. A document's `pages` are
   best first either way.
2. `TextDocument` is the `Document` of `/api/documents` with `matching_pages` (how many of its pages
   match) and `pages`, its best matching pages (`pages_per_document`), best first:
   `{page, section_types, snippet, highlights, original_url}`.
3. `section_types`: the types of the sections that hold the page, in the classifier's order.
4. `snippet`: plain text, never HTML, with runs of white space and control characters as one
   space. A page of up to 360 characters is shown whole. Of a longer page the server shows the
   passage of 360 characters with the most different matched words (FTS5 `highlight()` marks the
   matches), or two passages of 180 characters when together they show more, as for words far
   apart on the page. A passage grows by up to 10 characters at each end so as not to cut a word.
   `…` marks left-out text, ` … ` the gap between two passages.
5. `highlights`: `[[start, end], ...]`, the matched words in `snippet`, as offsets in characters
   (Unicode code points; in JavaScript, index `Array.from(snippet)`), `end` exclusive.
6. `original_url`: `<PUBLIC_URL>/original/<sha256>?page=<n>`, which opens the file at that page.
7. The facets count the documents of the result, with every filter applied. This differs from
   `/api/documents`, where each facet leaves out its own filter: that needs a search per facet,
   and a search of a common word costs most of a second. `years`: newest first, no year last.
   `sections`: by type; a document counts for each type that holds one of its matching pages.
   `cantons`: by code; a document linked by communes of two cantons counts for both.
   `communes`: the 20 communes (or `commune_facets`) that link the most documents of the
   result, most first, then by name.
8. The cap: a search takes at most the first 20,000 matching pages that pass the filters: the
   best, or with `sort` the newest or oldest. When that many match, `capped` is `true`. Then `total`, the facets and `matching_pages` count only the
   documents of those pages, and `offset` pages through only them. Common words reach the cap:
   `Gemeinde` is on about 430,000 of the 1.2 million pages, `Budget` on about 730,000. Without the
   cap such a search takes 3 to 5 seconds. Sorting by date looks up the date of every matching
   page: for such a word it adds about half again to the time.
9. The text is machine-extracted (the PDF's text layer, or OCR) and no person has checked it.

### `GET /api/documents/{sha256}/pages?from=<n>&to=<n>`

The text of up to 10 pages of a document. `from` defaults to 1 and `to` to `from` + 9; pages past
the document's `page_count` are left out. 503 without the full-text index, 404 for an unknown
document, 400 when `from` is below 1, `to` is before `from` or the range has more than 10 pages.

```json
{"sha256": "...", "page_count": 12,
 "pages": [{"page": 1, "section_types": ["minutes"], "source": "layer", "text": "...",
            "reason": null, "original_url": "<PUBLIC_URL>/original/<sha256>?page=1"}]}
```

Every page of the range is listed. A page in the index has `source` (`layer`: the PDF's text
layer; `ocr`: text recognition on the page image) and `text`, as the index holds it: line breaks
kept, words split at a line end joined. A page outside the index has `source` and `text` null and
a `reason`:

1. "Not searched: ... bulletins and the documents of private bodies": the document's `kind` is
   `bulletin` or its `body_kind` is `private`.
2. "Not searched: ... no section of a type the search covers": the page lies in no section of the
   types the index records in its `build` table.
3. "No text yet: ...": otherwise. The page has no usable text layer, and OCR has not reached it.

### `GET /api/openapi.json`

The OpenAPI 3.1 description of the endpoints above. Its schemas are generated from the same Rust
types the handlers read and return (`src/routes/openapi.rs`); a test fails when a route of the
router is missing from it or its fields differ from what the API sends.

### `GET /api/runs`

Every run, by id: `id, stage, model, settings, note, documents`. `settings` is the stored JSON
object (null if it does not parse); `note` says in plain sentences what the settings do to the data.

### `GET /api/releases`

`current, releases, downloads`. `releases` is every released version of the data, oldest first:
`version, date, documents, pages, communes, searchable_pages, ocr_pages, classification_run,
git_commit, note` (the rows of the ledger `data/releases.csv` the serving database was built with,
then the one the full-text index was released as). `current` is the version of the files the
server reads, null when they were not released or there is no full-text index. `downloads` is
`[{day, files}]`: the files first downloaded each day, also those not classified yet.

### `GET /api/register`

Every register row, by canton and BFS id: `bfs_id, name, canton, status, website, vendor,
reachable, checked, checked_by, notes, publications[{body, kind, status, listing_url, from, to,
cadence, obstacle, how}]`.

### `GET /api/usage?days=<n>` (operator only)

The usage statistics of the last `days` days (default 30, today included), for the operator: it
needs `Authorization: Bearer <USAGE_TOKEN>` (401 without it, 404 when no token is set), is not
part of the OpenAPI description and is not counted itself. It sends what `src/usage.rs` keeps and
nothing else, as rows by day:

1. `totals`: `day, channel, route, bot, status, requests, searches, empty_searches, duration_ms`.
   Channels: `web` (pages), `api`, `mcp` (by tool), `original`, `redirect` (requests to a
   former name, by name).
2. `visitors`: `day, pages, browsers`: the estimated number of clients that loaded a page, and of
   browsers that also ran the front end (they ask for `/api/communes`). Programs that fetch HTML
   only count in `pages`. Today's numbers are counts so far; null for a day without any.
3. `referrers`: `day, host, requests`: the domain of the linking site for pages opened from a
   link elsewhere, from browsers only.
4. `bots`: `day, name, requests`: requests from recognised automated clients, by crawler name
   (`other` when unknown, `none` without a user agent).
5. `ask`, when the chatbot is configured: `day, outcome, questions, usd`: questions per outcome
   (`answered`, `too_broad`, `used_up`, `budget`, `busy`, `timeout`, `cancelled`, `empty`,
   `no_credit`, `failed`, `cached` for those answered again from what was kept, and `refused:<reason>` for those
   turned away before the model, as `POST /api/ask` names the reasons) and the dollars they cost.
   Cached and refused questions are counted in memory and written once a minute. The chatbot's tool calls are counted in
   `totals` under the channel `ask`.

### `GET /api/usage/questions?days=<n>` (operator only)

The chatbot's questions of the last `days` days (default 7, at most 90), newest first, with the
token of the usage report: `asked_at, lang, question, answer, steps[{tool, args}], outcome,
prompt_version, model, model_calls, input_tokens, output_tokens, usd, duration_ms`. Nothing that
identifies the asker is kept. Rows older than 90 days are deleted.

### `GET /api/ask/challenge` and `POST /api/ask` (the site's chatbot)

Not part of the public API: the site's search page uses them for the answer above the results, and
they are made hard to use from anywhere else (`docs/CHATBOT.md`, `src/ask/`). No page of the site
may be shown in a frame on another site (`frame-ancestors 'self'`).

1. `GET /api/ask/challenge` gives `{challenge, bits}`. The page finds a decimal `solution` such
   that the SHA-256 of `<challenge>:<solution>` starts with `bits` zero bits (16: about 65,000
   tries). A challenge is valid for five minutes, from the address it was given to, once.
   Recognised crawlers get 403.
2. `POST /api/ask` with `Origin` of the site and a JSON body `{question, lang, challenge,
   solution}` (`lang`: de, fr, it or en; the question 3 to 500 characters; the body within five
   seconds) answers with server-sent events: `step` `{tool, args}` for each tool call (with the
   arguments as they ran: the chatbot's searches ask for at most 30 documents of 2 pages, its
   readings for at most 5 pages), `answer` `{text}` (Markdown links, bold and lists) when there is
   one, and `end` `{outcome}`.
3. The start page's example searches (`EXAMPLES` in `src/ask/mod.rs`) are answered once and then,
   for 7 days and the same prompt version, given again from what was kept: after the proof of work,
   without the model and against no limit. Nothing a visitor typed otherwise is kept as an answer,
   so no one can find out whether someone asked a given question.
4. Refusals are JSON `{detail, reason}`: 403 `origin`, `client` (a recognised crawler) or
   `challenge`; 400 `body`, `short` or `long`; 408 `slow`; 413 `body`; 429 `used_up` (the daily
   dollars), `visitor` (15 a day per address and browser), `address` (30 a day), `address_hour`
   (6 an hour per address), `hour` (60 an hour for the site) or `no_credit` (answers pause for 15
   minutes after the model's provider refused for want of credit or a spend limit; kept answers are
   still given); 503 `busy` (two questions are being answered) or `off`. Only a question that runs counts against the limits. An IPv6 address
   counts as its /64.

### `GET /original/{sha256}?commune=<bfs_id>`

302 to the document on a commune's site, with `X-Robots-Tag: noindex, nofollow`: the `source_url`
the commune gives it in `document_communes`; without `commune`, or when that commune gives no
linkable URL, the first by BFS id. Only absolute http(s) URLs are used; 404 when there is none.

### `POST /mcp`

Streamable HTTP, stateless (no session kept), JSON responses. Tools:

1. `list_communes(canton, q)`: without either, only the number of communes and of documents per
   canton (`{"note", "cantons": [{canton, communes, documents}]}`), so that an agent does not read
   2,000 and more communes to find one. With `canton` (a code, any case) or `q` (part of the name:
   letters and digits compared without case and accents, ä, ö and ü also as ae, oe and ue), or
   both: `{"communes": [{bfs_id, name, canton, language, documents, register_status}]}`, by
   canton and name. The text the model reads is a TOON table: `communes[N]{fields}:`, then one
   commune per line, values separated by commas. Programs get every commune from
   `/api/communes`.
2. `get_commune(bfs_id)`: as `/api/communes/{bfs_id}`; an unknown number is a tool error.
3. `search_documents(commune, section, family, first_family, body, year, q, offset, limit)`: the
   parameters of `/api/documents`, an offset or limit out of range a tool error with a reason. The
   result in a shorter form for a model: documents without `first_seen`, `pages_seen`, `run_id` and
   `personal_data`, and `sections` as labels: `minutes 2-11`, `vote_result 12`, `budget 1-200
   (partly unseen)`.
4. `get_document(sha256)`: as `/api/documents/{sha256}`; an unknown sha256 is a tool error, as in
   the other tools.
5. `search_text(q, commune, canton, year, from, to, language, section, family, exclude_section,
   exclude_family, body, document, sort, offset, limit, pages_per_document, commune_facets)`: the
   parameters of `/api/pages`; a refused query is a tool error with the reason. The result in the
   shorter form: documents as in `search_documents`, pages without `highlights` and without a link
   of their own (the document's `original_url` with `?page=<n>`).
6. `read_pages(sha256, from, to)`: as `/api/documents/{sha256}/pages`. A bad range or an unknown
   sha256 is a tool error.

Without the full-text index, `search_text` and `read_pages` answer with a tool error that says so.
The server's instructions tell agents to search with `search_text`, read the pages around a hit with
`read_pages`, and cite the document and the page; they say that the page text is machine-extracted
(PDF text layer or OCR) and unchecked. The descriptions of the tools other than `list_communes` say
that no person has checked the classification, and for the full-text tools the text, yet. Every
tool has a title and is marked read-only and idempotent (`readOnlyHint`, `idempotentHint`). Results
are `structuredContent` plus the same JSON as text, except `list_communes`, whose text is the
table.

## Pages for search engines and AI assistants

The front end renders in the browser. For the pages below the server puts the facts into the HTML
it sends: title, description, canonical link, links to the other languages, schema.org JSON-LD
where it fits, and a fact sheet in `<main id="oc-facts">` that the app replaces on start. Every
visitor receives the same HTML.

1. `/{lang}` (`de`, `fr`, `it`, `en`): the number of documents and of the communes that link them
   (with the note that no person has checked the classification), the cantons and the communes
   with documents (JSON-LD `Dataset`, listed in the Hugging Face catalogue, with the current
   version and its date as `version` and `dateModified`). `/` redirects to the
   browser's language, else `/de`.
2. `/{lang}/gemeinden/{bfs_id}`: the commune's number of documents, website, documents per year
   and the first page of `/api/documents?commune=<bfs_id>` (the twenty newest documents, title and
   date, linked through `<PUBLIC_URL>/original/<sha256>?commune=<bfs_id>`), and a link to its
   canton (JSON-LD `AdministrativeArea`). 404 when the commune is not in the `communes` table;
   `noindex` until `SEARCH_INDEX_COMMUNES` is on.
3. `/{lang}/kantone/{code}`, the code in lower case (`zh`): the canton's communes with documents,
   each with its number of documents and linked to its page (JSON-LD `AdministrativeArea`). 404
   for another code or one in capitals; `noindex` until `SEARCH_INDEX_COMMUNES` is on, and for a
   canton without documents.
4. `/{lang}/ueber-uns`, `/{lang}/datenschutz`, `/{lang}/api`, `/{lang}/lizenzen`; `/{lang}/suche`
   (never indexed). Any other path under a language: the front end's error page, status 404. A
   page path without a language, as old links name them (`/gemeinden/261`, `/ueber-uns`), is
   answered with a 308 to the German page, query included.
5. Every page carries Open Graph tags for link previews (title, description, address, locale, and
   the card `/og/tractanda-<lang>.png`, 1200 x 630).
6. `/robots.txt`: every crawler, training crawlers included, may read the pages, but not `/api/`,
   `/mcp`, `/original/` or the search. Lists the sitemap when `SEARCH_INDEXING` is on.
7. `/sitemap.xml`: the start, about, privacy, API and licences pages and, with
   `SEARCH_INDEX_COMMUNES`, every commune and canton with documents, in the four languages.
8. `/llms.txt`: a Markdown guide for AI agents (llmstxt.org) with the current totals, the MCP
   address and the API.

## Tables and indexes

The server reads the tables of `src/opencommune/serving.sql`: `communes`, `runs`, `documents`,
`documents_fts`, `document_communes`, `sections`, `register`, `register_publications`,
`access_observations`, `releases` and `downloads` (and `build`, which it does not read).
`opencommune build` writes them from scratch; the integration tests create
their database from the same file. The build leaves the database out of WAL mode, so the read-only
server needs no `-shm` file. The server does not start without these tables.

The full-text index is a second file, `fulltext.db`, which `opencommune build-fulltext` writes
(`src/opencommune/fulltext.py`): `pages (id, sha256, page, source, run)`, `page_fts` (FTS5,
`unicode61 remove_diacritics 2`, the text stored redacted, rowid = `pages.id`) and `build`, whose
`release` row, once `opencommune release` has run, is the version's row of the ledger (JSON). The
redaction marks, such as `［name］`, are written in full-width brackets, which the tokenizer keeps
inside a word, so a search for "Name" does not find them. Every
connection attaches it as the schema `fulltext`. The serving database is opened read-only, and an
attached file inherits that. The integration tests create the index from the schema in
`fulltext.py`.

| Query | Index |
| --- | --- |
| `documents` count per commune, `commune` filter | `document_communes_commune` |
| `section` filter, `sections` facet | `sections_type` |
| `year` filter, `years` facet, the order's first key | `documents_year` |
| `q` on titles, 3 characters or more | `documents_fts` (FTS5 trigram) |
| a page's sections and communes, `/original` | the primary keys of `sections` and `document_communes` |
| access observations of a commune | `access_observations_commune` |
| full text: the matching pages | `fulltext.page_fts` (FTS5), then `fulltext.pages` by id |
| full text: `section` and `family` filters | `sections_type`, `sections_family`, then the primary key of `sections` |
| full text: `commune` and `canton` filters, `cantons` and `communes` facets | `document_communes_commune`, the primary keys of `document_communes` and `communes` |
| full text: `year` filter, `years` facet | `documents_year`, the primary key of `documents` |
| page text of a document | the unique index of `fulltext.pages` (`sha256, page`), then `fulltext.page_fts` by rowid |

The `bodies` facet and a `q` of fewer than 3 characters read the whole `documents` table (or what
the other filters leave of it). On a synthetic database of the archive's size (360,000 documents,
650,000 sections) `/api/documents` without filters takes about 0.65 s, with a 2-character `q`
about 1.2 s; with `commune` or `year` it takes under 0.1 s. Deep pages are slow too: an offset of
300,000 takes about 1 s. With the title index, `q=Protokoll` (20,855 documents) takes about 0.16 s
on the real data.

### How a full-text search runs

One statement gives everything but the snippets, so that the index is searched once:

1. `hit`: the pages that match (`page_fts MATCH`), joined to `pages`, with the filters: documents
   as an `IN` list on sha256, `section` and `family` as `EXISTS` on `sections`. They are ordered
   by `+rank` and cut at 20,000. The `+` keeps FTS5 from ranking every match itself; SQLite then
   ranks only the pages that pass the filters. `document` also limits the match to the rowid range
   of that document's pages, which `fulltext.py` writes one after the other, so FTS5 skips the
   other documents' matches.
2. `doc`: per document the best score and the number of pages. The total, the facets, the
   requested documents and their best pages come from `hit` and `doc`. `CROSS JOIN` makes SQLite
   start from them; it would otherwise scan the serving tables.
3. A second statement marks the matches in the whole text of those pages (at most 50 times
   `pages_per_document`) with FTS5 `highlight()`, and the server picks the passages. It names the
   pages as `+rowid IN (...)`, so that FTS5 reads the matches in order. Looked up one by one, a
   prefix query searches the whole vocabulary for each page: 60 snippets of `gem*` took 6 s.

Measured on the archive's data (an index of 1,229,565 pages of 58,123 documents, 4.5 GB; 349,986
documents in the serving database), with a release build, a warm cache and the default `limit`,
whole requests take:

| Request | Pages that match | `total` | Time |
| --- | --- | --- | --- |
| `/api/pages?q="Investitionskredit"` | 946 | 701 | 0.01 s |
| `/api/pages?q=crédit d'engagement` | 3,510 | 1,627 | 0.03 s |
| `/api/pages?q=preventivo 2025` | 11,293 | 352 | 0.04 s |
| `/api/pages?q=Steuerfuss` | 18,452 | 8,245 | 0.12 s |
| `/api/pages?q=Tempo* 30` | 17,577 | 6,782 | 0.14 s |
| `/api/pages?q=Gemeinde` | 426,862 | 8,510, capped | 0.40 s |
| `/api/pages?q=Budget` | 727,900 | 7,047, capped | 0.54 s |
| `/api/pages?q=gem*` | 636,404 | 12,190, capped | 0.82 s |
| `/api/pages?q=Gemeinde&canton=ZH` | | 3,559, capped | 0.34 s |
| `/api/pages?q=Gemeinde&commune=261` | | 1 | 0.11 s |
| `/api/pages?q=Gemeinde&section=minutes` | | 412 | 0.19 s |
| `/api/documents/{sha256}/pages`, 10 pages | | | 0.001 s |

A `commune` or `canton` filter makes a search faster: fewer pages are ranked. The first searches
after a start can take 1 to 2.5 s, while the operating system reads the index into its cache.
Without the cap, `Gemeinde` takes about 2.7 s and `Budget` about 5 s.
