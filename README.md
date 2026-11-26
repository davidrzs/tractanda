# Tractanda

An open archive of the decisions of Swiss municipalities: Gemeindeversammlung, Gemeindeparlament,
Conseil communal and Consiglio comunale minutes, and other public municipal documents such as
budgets, accounts and vote results. Documents are collected directly from municipal websites,
structured with a language model, anonymised, and served as a website, a JSON API and an MCP server
for AI agents. No API key. Public site: https://www.tractanda.ch.

The project was called OpenCommune until November 2026. The Python package and its command keep
that name (`opencommune`), and the crawler identifies as `TractandaBot`; robots.txt rules written
for its former name, `OpenCommuneBot`, still apply to it.

A register (`data/register/`) records, for every one of the about 2,100 Swiss communes, what it
publishes and whether the crawler can collect it, and why not when it cannot.

## Parts

1. **Crawler** (Python, `src/opencommune/crawl/`): discovers and downloads documents from the
   sources configured in `data/`. It honours robots.txt without exception, sends at most one request
   per second per host and per hosting network, and never uses proxies or a browser User-Agent.
2. **Processing** (Python): classification, text extraction and structured extraction with a
   language model, anonymisation, and loading into SQLite with full-text search. Classification
   is here (`src/opencommune/classify/`, `opencommune classify`); the other stages are being moved
   in. The package also builds the document catalogue (`opencommune catalog`) and the tables the
   server reads besides it (`opencommune build`).
3. **Server** (Rust, `server/`): the JSON API, MCP over streamable HTTP at `/mcp`, and the static
   website. It reads the SQLite database only. Details in `server/API.md`.
4. **Website** (SvelteKit, `frontend/`): built to static files that the server serves.

## Where the data lives

Nothing but configuration and the list of released versions (`data/releases.csv`) is in git. `DATA_DIR` (default `data/`) holds the crawl state, the raw
documents (`raw/`, or `RAW_DATA_DIR`) with a JSON sidecar each, the processing outputs and the
database. Raw documents and extraction outputs name private persons and are never published. When
`DATA_DIR` is outside the repository, set `CONFIG_DIR=data` so the configuration still comes from the
checkout.

## Run it

Requirements: [uv](https://docs.astral.sh/uv/), and for the server and website Rust (stable) and
Node 22.

```sh
cp .env.example .env
make setup                      # Python environment and an empty SQLite database
uv run opencommune bfs-sync     # the official commune list (BFS) into data/bfs/
uv run opencommune fetch        # documents of the curated sources into data/raw/
uv run opencommune catalog      # the document catalogue in the database
uv run opencommune build        # the serving database the web server reads
uv run opencommune build-fulltext  # the full-text index built from it
uv run opencommune release --note "..."  # name the two as the next version of the data
```

`uv run opencommune --help` lists every command.

The larger archive collectors (`iweb`, `linked-archive`, `wordpress`) run as background jobs
(`deploy/crawl.sh`, `deploy/crawler/`). `opencommune coverage-report` shows how deep the archive goes
per commune.

Website and server:

```sh
make frontend                   # frontend/build
make server                     # server/target/release/opencommune-server
DATABASE_PATH=data/opencommune.db STATIC_DIR=frontend/build \
  server/target/release/opencommune-server   # http://127.0.0.1:8000
```

Deploying it behind Caddy: `deploy/server/README.md`.

## Pipeline

```
data/communes.yaml, source lists
  -> crawl       -> raw/<sha>.pdf + <sha>.json sidecar
  -> processing  -> decisions and full-text index      (being moved into this repository)
  -> catalog     -> opencommune.db: pdf_catalog
  -> build       -> serving.db: documents, sections, register and access tables for the server
  -> build-fulltext -> fulltext.db: the redacted page text of the search
  -> release     -> data/releases.csv: the two files as the next version of the data
```

## Versions of the data

A version is a serving database and the full-text index built from it, deployed together. It is
named by the day of its release, `2026.11.26` (`2026.11.26.2` for a second one that day).

1. `opencommune release --note "..."` runs after `build` and `build-fulltext`. It checks that both
   files are one build of the same code and of the current ledger, then appends the version's row
   to `data/releases.csv` (counts of documents, pages, communes and searchable pages, of which from
   OCR, the classification run, the commit) and writes the row into the full-text index. Commit
   the ledger with the deploy.
2. `build` copies the ledger into the serving database, with the files first downloaded each day.
   The site shows the versions on the about page (`/api/releases`), the current one from the
   full-text index, and names it in the start page's schema.org `Dataset`.
3. Rows are only added, never changed. A copy of the data published elsewhere carries the version's
   name: a Hugging Face upload is tagged with it, a Zenodo record uses it as its version.

## Classification

A language model sees each PDF's pages as images and answers in a fixed JSON form: what kind of
document it is, which parts it has, the body behind it and the year. Requests go to OpenAI's Batch
API with `store: false`.

1. The prompt, answer form and request settings are kept as a record in
   `src/opencommune/classify/prompts/<version>/`, where the version is a hash of the three files.
   `opencommune classify register` makes a new record; records sit side by side, and every answer
   is stored with the version that produced it.
2. `opencommune classify prepare --job <name> --cantons all` renders the requests of the documents
   that have no answer for the version yet. A document with an answer is never sent again.
3. `opencommune classify drive --job <name> --max-usd <limit>` sends them, stores the answers and
   stops sending when answers are poor or the cost passes the limit.
4. `opencommune classify status` shows answers, failures and cost.

Answers live in `process.db` in `DATA_DIR`. They can name people and are never published as they
are.

## Adding a commune

Adding a commune is configuration only: an entry in `data/communes.yaml` or a source list, and its
row in `data/register/`; `opencommune lint-communes` and `opencommune register check` check both.
The crawler runs no JavaScript: where a page loads its list as public data from the same
site, the crawler reads that data; a listing that needs a browser is out of scope. A site that refuses
the crawler (robots.txt, HTTP 403, a login or a bot challenge) is recorded as refused.

## Data protection

Decisions on naturalisation, social assistance, guardianship, health and identifiable personnel
matters are dropped; names of private individuals are replaced with `[Person]`; office holders stay
named. The website does not serve copies of the documents: it links to the originals through a
redirect, never lists unreviewed titles or document URLs, and leaves out documents whose title or
URL marks them as naturalisation documents.

## Development

`make test` runs the Python tests (recorded listing pages in `tests/fixtures/`; no test touches the
network), `make lint` runs ruff and the configuration checks through pre-commit. `cargo test` in
`server/` and `npm run check` and the offline Playwright test in `frontend/` cover the rest
(`frontend/README.md`). CI runs all of them. Conventions are in `AGENTS.md`.

## Licence

- Code: MIT (`LICENSE`). Classification renders pages with
  [PyMuPDF](https://pymupdf.readthedocs.io), which is licensed under the AGPL-3.0.
- Data: the records Tractanda extracts are released under
  [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/): free to use for any purpose,
  without conditions. Strictly speaking you need not cite Tractanda, but a citation is highly
  appreciated.
- Map: boundaries from swisstopo through the
  [swiss-maps](https://github.com/interactivethings/swiss-maps) package (`scripts/build_map.py`).
