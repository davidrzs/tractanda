# Tractanda: notes for contributors and coding agents

`README.md` describes the project and how to run it. This file holds the rules for changing it.

## Layout

1. `src/opencommune/`: the Python package. `crawl/` discovers and downloads documents; the
   top-level modules hold the configuration, the register and the database tables the server reads
   (`catalog`, `build`). `classify/` classifies documents with a language model. `text/` reads the
   text layer of each page and runs OCR on the pages without one. `cli/` holds the commands, one
   module per part. The other processing stages (extraction,
   anonymisation) are being moved in.
2. `server/`: the Rust web server (JSON API, MCP, static front end). It reads the database only.
3. `frontend/`: the SvelteKit site, built to static files.
4. `data/`: the checked-in configuration (`communes.yaml`, source lists, `register/`) and the
   ledger of released versions (`releases.csv`). Crawl state and documents live in `DATA_DIR`,
   outside git.
5. `docs/CHATBOT.md`: the design of the answers above the search results.

## Crawling

1. robots.txt is absolute. No proxies, no browser User-Agent, no working around HTTP 403, login
   walls or bot challenges, and no forged headers. A site that refuses us is recorded as refused,
   and its documents are not collected another way (a backend host, seeded file addresses). The
   crawler is `TractandaBot`; rules for its former name, `OpenCommuneBot`, bind it as well.
2. One request per second per host, and per network, at most; Crawl-delay and Retry-After are
   honoured.
3. Configuration and crawl state stay apart: configuration is reviewed in git, state is written by
   the crawler only and never edited by hand.

## Privacy

1. Published records never name private individuals. Configuration does not either: no file
   address that names an applicant, a resident or another private person goes into `data/`.
2. Lists never carry a commune's document URL or an unreviewed title.
3. Test fixtures are real pages; check new ones for personal data before adding them.

## Model requests

1. A prompt record is never edited: a change is a new record with a new version. Nothing is sent
   with a record that is not committed.
2. Requests set `store: false`. Uploaded files are deleted at the provider once their results are
   stored. Answers stay private in `DATA_DIR`.
3. One prompt version is always rendered with the same PyMuPDF version (pinned in `uv.lock`), so its
   requests stay the same bytes; a test pins them.

## Versions of the data

1. A version is a serving database and the full-text index built from it, released together with
   `opencommune release` and deployed together. Data reaches the site only as a version.
2. `data/releases.csv` is the ledger: one row per version, written by `release`, committed with the
   deploy. Rows are only added; none is edited or removed.
3. A copy of the data published elsewhere (Hugging Face, Zenodo) is made from a released version and
   carries its name.

## Engineering

1. The smallest design that does the job: no framework, layer or option without a present use.
2. Python: logging through the standard `logging` module, configured once at the CLI entry point; no
   `print` in library code. Rust: `tracing`.
3. Every change keeps `make test` and `make lint` green, and `cargo test`, `cargo clippy` and
   `npm run check` for the server and the front end. Behaviour changes get a test.
4. Tests never touch the network: a guard fails any non-loopback connection.
5. Commands go through the `opencommune` CLI, which configures logging; modules have no
   `__main__` of their own. `scripts/` holds tools that are run by hand.
