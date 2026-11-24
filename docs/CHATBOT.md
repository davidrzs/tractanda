# The answer above the search results: design

Status: built on this branch, not deployed. Server: `server/src/ask/`, `server/src/routes/ask.rs`;
page: the search page (`frontend/src/routes/[[lang=language]]/suche/`) with
`frontend/src/lib/AskAnswer.svelte` and `frontend/src/lib/answer.svelte.ts`. Endpoints and
settings: `server/API.md`; running it in production: `deploy/server/README.md`.

The start page's search box takes keywords and questions alike. Every search lands on the results
page as before; above the list, a language model answers from the archive's documents, with links
to the pages it rests on. When the visitor's own words find nothing (a whole question usually
does not), the list shows the full-text search the answer made, with a link back to the exact
words.

## How it works

1. The browser solves a proof of work for a challenge from the server, then sends the question to
   `POST /api/ask` and reads server-sent events: each step (a tool call) as it happens, then the
   answer, then how it ended.
2. The server runs the tool loop with `rig` (`rig-core`, `rig-agent` 0.47). The tools are the MCP
   server's own, connected in-process with `rig-rmcp` over an in-memory pipe: one source of tool
   code and descriptions, no network between them. Their calls count in the usage statistics under
   the channel `ask`; the chat channel gets text-only results.
3. The model is a setting: Anthropic's Messages API (Claude Haiku 5.5 by default) or any
   OpenAI-compatible endpoint (the CSCS inference platform). Anthropic's prompt caching is on, so
   each round's repeated prefix costs a tenth.
4. The system prompt (`server/src/ask/prompt.md`) asks for answers from the tool results only,
   with page links, in the question's language; the site's language is added as the fallback for
   input whose language is unclear (a keyword). Each answer is stored with the version of the
   prompt, model and limits that produced it.
5. The start page's example searches are answered once and then, for 7 days and the same prompt
   version, given again without the model. Nothing else a visitor typed is kept as an answer, so no
   one can find out whether someone asked a given question.

## Abuse

The aim: money is capped, one attacker cannot use up what others need, and the endpoint is useless
as a free general-purpose model. Checked by an attack test with a fake model (no money spent);
its findings are fixed.

1. **Only after a search on the site.** The answer starts by itself only after a search made on
   the site's own pages (a history state set by the start page and the search form). A search page
   opened from outside (a shared link, a link or redirect from another site), or changed there by a
   filter, asks nothing and shows a button. No page may be framed by another site
   (`frame-ancestors 'self'`). So no other site can make its visitors spend the limits.
2. **The page's script must run.** Each question carries a proof of work (about 65,000 SHA-256
   hashes, around a second): the challenge is signed, valid five minutes, from the address it was
   given to, once. A plain script gets nothing; a determined one pays computing time per question.
3. **Checks before the model, in order:** off file (a path the server cannot check counts as
   off); `Origin` of the site; recognised crawlers refused; body within 5 s and 8 KB; question
   3–500 characters; the proof of work; a kept answer for an example search; the pause after the
   provider refused for want of money; the daily dollar cap; a free slot (2 at once). Only then does the question count against
   the limits: 15 a day per address and browser, 30 a day and 6 an hour per address (an IPv6
   address counts as its /64), 60 an hour for the site. The counts live in memory under daily
   keys.
4. **Limits inside a question:** 6 model calls, 8 tool calls, searches of at most 30 documents
   with 2 pages each, readings of at most 5 pages per call and 30 per question, each tool result
   cut at 40,000 characters and 150,000 for all of a question's results (each call stays below the
   long-context prices), 300,000 input tokens, 120 s; the run is dropped when the visitor
   leaves, mid-call too.
5. **Money:** a key of its own on prepaid credits without automatic reload; a daily dollar cap in
   the server, summed per call with the provider's tiers (long-context prices above 100,000 input
   tokens, cache reads and writes), a call without usage figures counted at the most a call can
   cost, a call cut off by the timeout or the visitor leaving counted by the size it was sent; an off file that stops it without a restart. When the provider refuses for want
   of credit or a spend limit, answers pause for 15 minutes and say so calmly, kept answers are still
   given, and the next question after the pause tries again.
6. **Nothing to steal or break:** read-only tools over public, redacted text; no secrets in the
   context; the answer is rendered as text, with links only to the site's originals and pages.
7. **Slow clients:** request bodies time out (10 s for the app, 5 s for `/api/ask`); Caddy cuts
   slow headers and bodies; the service has a high open-files limit.
8. **What no code prevents:** someone with many addresses (Tor, whole IPv6 networks) can use up the
   day's shared quota. The cap protects the money; at worst the answers stop for the day while the
   search keeps working.

## Privacy

1. What is typed into the search field goes from our server to the model's provider (named on the
   privacy page with its country), not the visitor's address. The model sees only what the tools
   return: titles and page text with private names, addresses and e-mail addresses replaced.
2. Questions, answers and steps are logged for 90 days without address or visitor code, then
   deleted; the notices under the search fields and the privacy page say so. Answers given again
   from the cache, and refused questions, are only counted.
3. The limits count in memory only, under daily-changing keys.

## Later

1. Follow-up questions: send the previous question and answer along, at most three in a row.
2. Streaming the answer text as it is written.
3. Feedback on an answer (helpful or not).
