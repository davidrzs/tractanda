<script lang="ts">
  // The answer above the search results (answer.svelte.ts): the steps as they arrive, then the
  // text, shown without HTML and with links to this site only (ask.ts `blocks`).
  import { page } from '$app/state';
  import { href, type Lang } from '$lib/i18n';
  import { askCopy } from '$lib/copy/ask';
  import { blocks } from '$lib/ask';
  import type { Answer } from '$lib/answer.svelte';
  import { Badge } from '$lib/components/ui/badge';

  // `onask`: ask the model, for a search page opened from outside without a kept answer
  let { answer, lang, onask }: { answer: Answer; lang: Lang; onask: () => void } = $props();
  let t = $derived(askCopy[lang]);
  let busy = $derived(answer.status === 'preparing' || answer.status === 'searching');
  // problems a visitor needs not see: a refusal that means something is wrong with the page
  let problem = $derived(answer.problem ? (t.problems[answer.problem] ?? t.broken) : '');

  function detail(args: Record<string, unknown>): string {
    if (typeof args.q === 'string') return ` «${args.q}»`;
    if (typeof args.from === 'number')
      return args.to !== undefined ? ` ${args.from}–${args.to}` : ` ${args.from}`;
    return '';
  }
  // the originals open in a new tab, as in the document list; the site's own pages in this one
  const opens = (href: string) =>
    href.startsWith('/original/') ? { target: '_blank', rel: 'nofollow noreferrer' } : {};
  const link =
    'rounded-sm font-medium text-foreground underline decoration-zinc-300 underline-offset-4 hover:decoration-zinc-900';

  // To ask further in one's own assistant: connect Tractanda there (the API page explains how),
  // with the MCP address and the question to paste.
  let copied = $state<'' | 'address' | 'question'>('');
  async function copy(what: 'address' | 'question') {
    const text = what === 'address' ? `${page.url.origin}/mcp` : answer.question;
    try {
      await navigator.clipboard.writeText(text);
      copied = what;
      setTimeout(() => (copied = ''), 2000);
    } catch {
      // the browser refused the clipboard: nothing to show
    }
  }
</script>

{#if answer.status === 'none'}
  <section
    class="ask-answer ask-offer mt-4 flex flex-wrap items-center gap-3 rounded-xl border border-dashed border-[#ecd3d5] bg-[#fdf8f8] px-4 py-3 text-sm"
  >
    <span class="text-zinc-700">{t.askHint}</span>
    <Badge variant="secondary" class="text-brand">{t.beta}</Badge>
    <button
      type="button"
      class="ml-auto h-9 rounded-lg bg-primary px-4 font-medium text-primary-foreground shadow-xs hover:bg-brand-dark"
      onclick={onask}>{t.askButton}</button
    >
  </section>
{:else if answer.status !== 'off' && answer.status !== 'checking'}
  <section
    class="ask-answer mt-4 rounded-xl border border-[#ecd3d5] bg-[#fdf8f8] px-4 py-3.5 sm:px-5"
    aria-live="polite"
    aria-busy={busy}
  >
    <div class="flex flex-wrap items-center gap-2 text-sm font-semibold">
      {t.answer}<Badge variant="secondary" class="text-brand">{t.beta}</Badge>
      <span class="ml-auto text-xs font-normal text-muted-foreground"
        >{busy ? (answer.status === 'preparing' ? t.preparing : t.searching) : t.written}</span
      >
    </div>
    {#if answer.steps.length}
      <ol class="steps mt-2 space-y-0.5 text-xs text-muted-foreground">
        {#each answer.steps as step}<li>
            {t.steps[step.tool] ?? step.tool}{detail(step.args)}
          </li>{/each}
      </ol>
    {/if}
    {#if busy}<div class="mt-3 h-0.5 overflow-hidden rounded bg-[#f4e1e2]">
        <div class="h-full w-1/3 animate-pulse bg-brand"></div>
      </div>{/if}
    {#if answer.text}
      <div class="answer-text mt-3 space-y-2 text-[0.9375rem] leading-relaxed text-zinc-800">
        {#each blocks(answer.text, page.url.origin) as block}
          {#if block.list}
            <ul class="list-disc space-y-1 pl-6">
              {#each block.lines as line}<li>
                  {#each line as part}{#if part.href}<a
                        class={link}
                        href={part.href}
                        {...opens(part.href)}>{part.text}</a
                      >{:else if part.bold}<strong>{part.text}</strong
                      >{:else}{part.text}{/if}{/each}
                </li>{/each}
            </ul>
          {:else}
            {#each block.lines as line}<p>
                {#each line as part}{#if part.href}<a
                      class={link}
                      href={part.href}
                      {...opens(part.href)}>{part.text}</a
                    >{:else if part.bold}<strong>{part.text}</strong>{:else}{part.text}{/if}{/each}
              </p>{/each}
          {/if}
        {/each}
      </div>
    {/if}
    {#if problem}<p class="answer-problem mt-2 text-sm text-zinc-700" role="status">
        {problem}
      </p>{/if}
    {#if answer.text}
      <p
        class="ask-continue mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-[#ecd3d5] pt-3 text-xs text-muted-foreground"
      >
        <span>{t.continueIn}</span>
        <a class={link} href={`${href('/api', lang)}#mcp`}>{t.connect}</a>
        <button type="button" class={link} onclick={() => copy('address')} aria-live="polite"
          >{copied === 'address' ? t.copied : t.copyAddress}</button
        >
        <button type="button" class={link} onclick={() => copy('question')} aria-live="polite"
          >{copied === 'question' ? t.copied : t.copyQuestion}</button
        >
      </p>
    {/if}
    <p class="mt-3 text-xs text-muted-foreground">
      {t.logged} · <a class={link} href={href('/datenschutz', lang)}>{t.privacy}</a>
    </p>
  </section>
{/if}
