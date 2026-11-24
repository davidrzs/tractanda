<script lang="ts">
  import DocumentList from '$lib/DocumentList.svelte';
  import AskAnswer from '$lib/AskAnswer.svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { copy, href, type Lang } from '$lib/i18n';
  import { searchCopy } from '$lib/copy/search';
  import { askCopy } from '$lib/copy/ask';
  import { answerFor, offer, type Answer } from '$lib/answer.svelte';
  let { data } = $props();
  let lang = $derived((page.params.lang || 'de') as Lang);
  let t = $derived(copy[lang]);
  let tips = $derived(searchCopy[lang].tips);

  // The answer above the list: one per question and language, asked once in this visit
  // (answer.svelte.ts), so filters and the next page do not ask again. It starts by itself after
  // a search on this site's own pages; a page opened from outside shows a button
  // (answer.svelte.ts).
  let answer = $state<Answer | null>(null);
  let question = $derived(data.filters.q.trim());
  let askable = $derived(question.length >= 3 && question.length <= 500);
  $effect(() => {
    answer = !askable ? null : page.state.ask ? answerFor(question, lang) : offer(question, lang);
  });
  function ask() {
    if (askable) answer = answerFor(question, lang);
  }
  // When the visitor's words find nothing (a whole question usually does not), the list shows the
  // first full-text search the answer made, with a link back to the exact words.
  $effect(() => {
    const search = answer?.search;
    const { filters, results, notice } = data;
    if (!search || typeof search.q !== 'string' || filters.terms || filters.exact) return;
    if (results.total > 0 && notice !== 'invalid') return;
    const query = new URLSearchParams(page.url.searchParams);
    query.set('terms', search.q);
    for (const key of ['canton', 'commune', 'year'] as const) {
      const value = search[key];
      if ((typeof value === 'string' || typeof value === 'number') && !query.get(key))
        query.set(key, String(value));
    }
    query.delete('offset');
    goto(`?${query}`, { replaceState: true, noScroll: true, keepFocus: true, state: page.state });
  });
</script>

<svelte:head
  ><title>{data.filters.q ? `${data.filters.q} — ` : ''}{t.search} — Tractanda</title></svelte:head
>
<section class="search-page mx-auto max-w-7xl px-4 pt-8 sm:px-6">
  <h1 class="sr-only">{t.search}</h1>
  <DocumentList
    results={data.results}
    filters={data.filters}
    query={data.query}
    endpoint={data.endpoint}
    {lang}
    communes={data.communes}
    placeholder={t.textSearch}
    button={t.searchButton}
    empty={data.notice === 'invalid' ? t.textInvalid : undefined}
  >
    {#snippet help()}
      {#if data.notice && !data.filters.terms}<p
          class="search-notice rounded-md bg-muted px-3 py-2 text-sm font-medium text-foreground"
          role="status"
        >
          {data.notice === 'invalid' ? t.textInvalidWhy : t.textUnavailable}
        </p>{/if}
      <!-- one line until opened: the results start right under the field -->
      <details class="search-tips">
        <summary class="w-fit cursor-pointer select-none hover:text-foreground">{tips}</summary>
        <div class="mt-1 space-y-1">
          <p>{t.textHelp}</p>
          <p>{t.textScope}</p>
          <p>
            {askCopy[lang].notice}
            <a
              class="underline decoration-zinc-300 underline-offset-4 hover:text-foreground"
              href={href('/datenschutz', lang)}>{askCopy[lang].privacy}</a
            >
          </p>
        </div>
      </details>
    {/snippet}
    {#snippet above()}
      {#if answer}<AskAnswer {answer} {lang} onask={ask} />{/if}
    {/snippet}
  </DocumentList>
</section>
