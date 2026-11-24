// The answer above the search results: one run per question, kept in memory while the visitor
// moves through the site, so that a filter, the next page or going back does not ask again.
// Nothing is stored in the browser.
//
// A search made on this site's own pages asks by itself (`answerFor`). A search page opened from
// outside (a shared link, a link on another site), or changed there only by a filter, asks
// nothing and offers a button (`offer`). So no other site can make its visitors spend the
// chatbot's limits, or show them an answer it steered.
import { ask, challenge, solve, Refused } from '$lib/ask';

export type Step = { tool: string; args: Record<string, unknown> };

export class Answer {
  /** `off`: the chatbot is off or not configured, and nothing is shown. `checking`: finding out
   * whether it is on, nothing shown yet. `none`: not asked; the visitor may ask. */
  status = $state<'preparing' | 'searching' | 'done' | 'off' | 'checking' | 'none'>('preparing');
  steps = $state<Step[]>([]);
  text = $state('');
  /** An outcome or refusal code when there is no answer (copy/ask.ts translates it). */
  problem = $state('');
  /** The first full-text search the answer made, for the list below. */
  search = $state<Step['args'] | null>(null);
  private controller = new AbortController();
  /** Given up before it finished: asked again when the visitor comes back to it. */
  stopped = false;

  constructor(
    readonly question: string,
    readonly lang: string
  ) {}

  async run() {
    const signal = this.controller.signal;
    try {
      const c = await challenge(signal);
      const solution = await solve(c.challenge, c.bits, signal);
      this.status = 'searching';
      await ask(
        this.question,
        this.lang,
        { challenge: c.challenge, solution },
        (event) => {
          if (event.kind === 'step') {
            this.steps = [...this.steps, { tool: event.tool, args: event.args }];
            if (!this.search && event.tool === 'search_text') this.search = event.args;
          } else if (event.kind === 'answer') this.text = event.text;
          else if (event.outcome !== 'answered') this.problem = event.outcome;
        },
        signal
      );
      if (!this.text && !this.problem) this.problem = 'failed';
      this.status = 'done';
    } catch (e) {
      if (signal.aborted) return;
      if (e instanceof Refused && e.reason === 'off') {
        this.status = 'off';
        return;
      }
      this.problem = e instanceof Refused ? e.reason : 'broken';
      this.status = 'done';
    }
  }

  /** Whether the chatbot is on, before offering to ask: a challenge costs nothing. */
  async check() {
    try {
      await challenge(this.controller.signal);
      this.status = 'none';
    } catch (e) {
      this.status = e instanceof Refused && e.reason === 'off' ? 'off' : 'none';
    }
  }

  stop() {
    this.stopped = true;
    this.controller.abort();
  }
}

// the last few questions of this visit, newest last
const kept: Answer[] = [];
const KEEP = 10;

function find(question: string, lang: string): Answer | undefined {
  return kept.find((a) => a.question === question && a.lang === lang && !a.stopped);
}

/** The answer to `question` in `lang`: the one already asked in this visit, or a new run. */
export function answerFor(question: string, lang: string): Answer {
  const found = find(question, lang);
  if (found && found.status !== 'none' && found.status !== 'checking') return found;
  // a question still being answered when another is asked is given up
  for (const a of kept) if (a.status === 'preparing' || a.status === 'searching') a.stop();
  const answer = new Answer(question, lang);
  kept.push(answer);
  if (kept.length > KEEP) kept.shift();
  void answer.run();
  return answer;
}

/** For a search page opened from outside: the answer of this visit, or an offer to ask. */
export function offer(question: string, lang: string): Answer {
  const found = find(question, lang);
  if (found) return found;
  const answer = new Answer(question, lang);
  answer.status = 'checking';
  void answer.check();
  return answer;
}
