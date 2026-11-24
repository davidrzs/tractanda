# Svelte frontend

SvelteKit/Svelte 5 single-page app with TypeScript and a native SVG map. It is built to static
files (`@sveltejs/adapter-static`, fallback `index.html`) that the Rust server in `../server`
serves together with the JSON API on the same origin. Node is needed only to build.

## Run locally

Start the Rust server against a database (`../server/API.md`):

```sh
cd server && DATABASE_PATH=../data/opencommune.db cargo run
```

In another terminal:

```sh
cd frontend
npm ci
npm run dev
```

Open http://127.0.0.1:5173/de (or `/fr`, `/it`). Vite proxies `/api` and `/original` to
`API_ORIGIN` (default http://127.0.0.1:8000). Pages load their data in the browser with universal
`+page.ts` / `+layout.ts` loaders: the commune list (`/api/communes`) once for the map and the
search, one commune (`/api/communes/{bfs}`) and a page of its files (`/api/documents`) on its page,
a full-text search (`/api/pages`) on the search page, the classification runs (`/api/runs`) on the
about page.

To serve the built site: `npm run build`, then run the server with `STATIC_DIR=frontend/build`.

## What works

- Map-first homepage with the number of files and communes, commune-name search and canton index
  filter, and links to the canton pages. Communes whose register status is `blocked_robots` are
  hatched.
- Canton pages (`/{lang}/kantone/{code}`, the code in lower case): the canton's communes on a map
  and in a list by name, each with its number of documents.
- Addressable commune archives (`/{lang}/gemeinden/{bfs}`): a holdings table (documents per year
  and section family; a cell filters the list), then the commune's files. Each file shows its
  printed title, body, pages, languages that differ from the page's, and its sections: a contents
  line and a page strip in the six family colours, and chips that open the original at the
  section's first page. Selectors with counts (section type, body, year) and a title text field;
  filtering by a section type or family shows the matching sections as lines of their own. Pages
  of 20; the filters live in the URL query. Under the list, the exact `/api/documents` request.
- The search page (`/{lang}/suche`): one box. With words in it, the text of the pages is searched
  (`/api/pages`). Each file shows its best matching pages: the page, its section type, a snippet
  with the matches marked (`<mark>`, built from `highlights`; the API's text is never read as HTML)
  and a link to the original at that page. Filters for section type, body, year, canton and
  commune; a note when the search reached its cap. Without words, or when the server has no
  full-text index (503), the commune page's list across all communes, by title. The site never
  shows the full text of a page.
- Commune pages have a box that opens the search page for that commune.
- The about page explains each classification run from `/api/runs`; files link to their run.
- The API page (`/{lang}/api`): what the API is, the MCP address with a configuration snippet,
  and an interactive reference from `/api/openapi.json` ([Scalar](https://github.com/scalar/scalar),
  MIT, self-hosted). It is loaded only on that page (about 4 MB, 1.1 MB gzipped) and sends nothing
  to other origins: no Scalar fonts, proxy, telemetry, AI agent or developer tools, and "try it"
  goes to this site.
- German, French and Italian interface dictionaries, locale-aware numbers/dates, preserved
  page/query when switching language, correct HTML language on every navigation.
- Responsive desktop/mobile layouts, keyboard links/search, focus states, reduced motion.

Map geometry comes from swisstopo through the swiss-maps package (`scripts/build_map.py`); no
map library. Shared UI components and translations live in `src/lib`.
[SvelteKit's routing](https://svelte.dev/docs/kit/routing) supplies navigation and page loading.

## Checks

```sh
npm run check
npm run build
npx playwright install chromium --only-shell
npx playwright test --config playwright.access.config.ts  # static build against an offline API fixture
npx playwright test  # smoke test: needs the Rust server on a real database and the Vite dev server
npm run format:check
```

CI runs `check`, `format:check`, `build` and the offline tests (`access.spec.ts`,
`documents.spec.ts`, `api.spec.ts`, `search.spec.ts` against `tests/access-fixture.py`, which serves a saved copy of
the server's `openapi.json` from `tests/openapi.json`). The smoke test needs real data (it opens
Wetzikon, BFS 121, and expects files there), so it runs only by hand.
Screenshots go to the ignored `test-results/` directory.

## Limits

UI translations do not translate source material or enable cross-language semantic search.
Romansh and English are not yet provided. The full-text search covers the pages inside sections
of eight types (minutes, decisions, vote results, budgets, accounts, proposals, agendas and
members' requests); its text is machine-extracted and unchecked. The commune page's text field
matches the printed titles. Titles, bodies, dates and sections come from a language model and are not
checked by a person; private names in titles appear as "[name]". `personal_data` is never shown.

## Deployment

The server binary serves `frontend/build` (`STATIC_DIR`): files as they are, `index.html` for
every other path, hashed `_app/immutable` files with a one-year cache. Every page carries
`noindex, nofollow`. Usage statistics are counted by the server: page loads of
`index.html` by route template, API requests by route; client-side navigation is not a page
load.

Problem reports open a user-controlled mail draft with the page, record ID and source.
The recipient is assembled on click; obfuscation only deters basic HTML scraping.
