<script lang="ts">
  import { copy, href, fmt, type Lang } from '$lib/i18n';
  import { DATASET_URL, MCP_COMMAND, SOURCE_URL, docs, start } from '$lib/copy/start';
  import { askCopy } from '$lib/copy/ask';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import Map from '$lib/Map.svelte';
  import CommuneSearch from '$lib/CommuneSearch.svelte';
  import { Button } from '$lib/components/ui/button';
  import { Badge } from '$lib/components/ui/badge';
  import { type Commune, hasDocuments } from '$lib/types';
  import Search from '@lucide/svelte/icons/search';
  import Copy from '@lucide/svelte/icons/copy';
  import ArrowUpRight from '@lucide/svelte/icons/arrow-up-right';
  import FileText from '@lucide/svelte/icons/file-text';
  import CodeXml from '@lucide/svelte/icons/code-xml';
  import hfIcon from '$lib/icons/huggingface.svg';
  import ghIcon from '$lib/icons/github.svg';
  import { onMount } from 'svelte';
  let { data } = $props();
  let lang = $derived((page.params.lang || 'de') as Lang);
  let t = $derived(copy[lang]);
  let s = $derived(start[lang]);
  const number = (n: number) => fmt(n, lang);
  let communes = $derived(data.communes as Commune[]);
  let collected = $derived(communes.filter(hasDocuments));
  // the count in the lead, rounded down to the thousand: "über 2'000"
  let rounded = $derived(
    collected.length >= 1000
      ? s.over.replace('{n}', number(Math.floor(collected.length / 1000) * 1000))
      : number(collected.length)
  );
  let lead = $derived(s.lead.replace('{communes}', rounded));

  // the search of every document's text (the search page reads ?q=)
  let q = $state('');
  const searchHref = (words: string) =>
    href('/suche', lang) + (words ? `?${new URLSearchParams({ q: words })}` : '');
  // a search made here: the answer above the results starts by itself (search page)
  function search(e: SubmitEvent) {
    e.preventDefault();
    goto(searchHref(q.trim()), { state: { ask: true } });
  }
  function searchExample(e: MouseEvent, words: string) {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    goto(searchHref(words), { state: { ask: true } });
  }

  // the list of communes, most documents first
  const FIRST = 8;
  let canton = $state('');
  let visibleCount = $state(FIRST);
  let shown = $derived(
    communes
      .filter((c) => !canton || c.canton === canton)
      .sort((a, b) => b.documents - a.documents || a.name.localeCompare(b.name, lang))
  );
  let cantons = $derived([...new Set(communes.map((c) => c.canton))].sort());

  let copied = $state(false);
  function copyCommand() {
    navigator.clipboard
      ?.writeText(MCP_COMMAND)
      .then(() => {
        copied = true;
        setTimeout(() => (copied = false), 1500);
      })
      .catch(() => {});
  }

  // the film plays muted in a loop, and stays still for people who ask for less motion
  let film = $state<HTMLVideoElement>();
  let playing = $state(false);
  function toggleFilm() {
    if (!film) return;
    if (film.paused) film.play().catch(() => {});
    else film.pause();
  }
  onMount(() => {
    if (!matchMedia('(prefers-reduced-motion: reduce)').matches) film?.play().catch(() => {});
  });

  const card =
    'flex flex-col rounded-xl border border-border bg-white p-5 outline-none focus-visible:ring-3 focus-visible:ring-ring/50';
  const cardLink = `${card} hover:border-zinc-300 hover:bg-zinc-50`;
</script>

<svelte:head
  ><title>Tractanda — {s.pageTitle}</title><meta name="description" content={lead} /></svelte:head
>

<!-- What the archive is, the search and the film: text 5 parts, film 7, both centred vertically -->
<section class="mx-auto max-w-7xl px-4 py-14 sm:px-6 lg:py-24">
  <div class="grid items-center gap-10 lg:grid-cols-[5fr_7fr] lg:gap-16">
    <div class="min-w-0">
      <h1
        class="text-4xl leading-[1.08] font-semibold tracking-tight text-balance lg:text-[2.875rem]"
      >
        {s.title}
      </h1>
      <p class="mt-6 text-lg leading-relaxed text-pretty text-zinc-600">{lead}</p>
      <form class="mt-10" role="search" action={href('/suche', lang)} onsubmit={search}>
        <label for="start-q" class="text-sm font-medium text-foreground">{s.searchLabel}</label>
        <div class="mt-2 flex flex-col gap-2 sm:flex-row">
          <div class="relative flex-1">
            <Search
              class="pointer-events-none absolute top-1/2 left-3.5 size-5 -translate-y-1/2 text-zinc-400"
              aria-hidden="true"
            />
            <input
              id="start-q"
              name="q"
              type="search"
              autocomplete="off"
              bind:value={q}
              placeholder={s.searchPlaceholder}
              class="h-12 w-full rounded-lg border border-input bg-white pr-3 pl-11 text-base shadow-xs outline-none placeholder:text-zinc-400 focus-visible:border-zinc-900 focus-visible:ring-4 focus-visible:ring-ring/15"
            />
          </div>
          <Button type="submit" class="h-12 px-6 shadow-xs hover:bg-brand-dark"
            >{t.searchButton}</Button
          >
        </div>
        <p class="examples mt-4 text-sm text-muted-foreground">
          {s.examples}
          {#each s.exampleQueries as example, i}{i === 0
              ? ' '
              : i === s.exampleQueries.length - 1
                ? ` ${s.or} `
                : ', '}<a
              href={searchHref(example)}
              onclick={(e) => searchExample(e, example)}
              class="rounded-sm font-medium text-zinc-800 underline decoration-zinc-300 underline-offset-4 outline-none hover:decoration-zinc-900 focus-visible:ring-2 focus-visible:ring-ring"
              >{example}</a
            >{/each}{` ${s.orAsk} `}<a
            href={searchHref(s.askExample)}
            onclick={(e) => searchExample(e, s.askExample)}
            class="rounded-sm font-medium text-zinc-800 underline decoration-zinc-300 underline-offset-4 outline-none hover:decoration-zinc-900 focus-visible:ring-2 focus-visible:ring-ring"
            >{s.askExample}</a
          >
        </p>
        <p class="ask-notice mt-2 text-xs leading-5 text-muted-foreground">
          {askCopy[lang].notice}
          <a
            href={href('/datenschutz', lang)}
            class="rounded-sm text-zinc-700 underline decoration-zinc-300 underline-offset-4 hover:decoration-zinc-900"
            >{askCopy[lang].privacy}</a
          >
        </p>
      </form>
      <p class="mt-10 text-sm leading-6 text-muted-foreground">{s.machineNote}</p>
    </div>
    <figure class="film min-w-0">
      <div class="relative overflow-hidden rounded-xl border border-border bg-zinc-50">
        <!-- the film has no sound; the caption says what it shows -->
        <video
          bind:this={film}
          class="block aspect-video w-full object-cover"
          src="/film/tractanda-de.mp4"
          poster="/film/tractanda-de-poster.jpg"
          muted
          loop
          playsinline
          preload="metadata"
          aria-label={s.filmCaption}
          onplay={() => (playing = true)}
          onpause={() => (playing = false)}
        ></video>
        <button
          type="button"
          onclick={toggleFilm}
          class="absolute right-3 bottom-3 rounded-md bg-white/90 px-2.5 py-1 text-xs font-medium text-zinc-700 shadow-xs ring-1 ring-border outline-none hover:bg-white focus-visible:ring-2 focus-visible:ring-ring"
          >{playing ? s.pause : s.play}</button
        >
      </div>
      <figcaption class="mt-3 text-sm text-muted-foreground">{s.filmCaption}</figcaption>
    </figure>
  </div>
</section>

<!-- The communes: the map beside the finder and the list -->
<section id="gemeinden" class="border-t border-border">
  <div class="mx-auto max-w-7xl px-4 py-14 sm:px-6 lg:py-24">
    <div class="max-w-2xl">
      <h2 class="text-2xl font-semibold tracking-tight">{t.discover}</h2>
      <p class="coverage-count mt-2 text-muted-foreground">
        <strong class="font-medium text-foreground">{number(collected.length)}</strong>
        {t.withDocuments}. {t.coverageNote}
        {s.pickNote}
      </p>
    </div>
    <div class="mt-10 grid items-start gap-10 lg:grid-cols-[7fr_5fr] lg:gap-16">
      <div class="min-w-0"><Map {communes} /></div>
      <!-- on a phone the finder comes first: the map is small there -->
      <div class="order-first min-w-0 lg:order-none">
        <CommuneSearch {communes} />
        <div class="mt-10 flex items-center justify-between gap-3">
          <h3 class="text-sm font-medium">{s.mostDocuments}</h3>
          <label
            ><span class="sr-only">{t.cantonLabel}</span><select
              bind:value={canton}
              onchange={() => (visibleCount = FIRST)}
              class="h-8 rounded-md border border-input bg-white px-2 text-sm shadow-xs outline-none focus-visible:ring-4 focus-visible:ring-ring/15"
              ><option value="">{t.allCantons}</option>{#each cantons as c}<option>{c}</option
                >{/each}</select
            ></label
          >
        </div>
        <div class="communes-list -mx-2 mt-2 divide-y divide-border border-y border-border">
          {#each shown.slice(0, visibleCount) as c (c.bfs_id)}<a
              class="commune-row flex items-center justify-between gap-3 rounded-md px-2 py-2.5 text-sm text-foreground outline-none hover:bg-zinc-50 focus-visible:ring-3 focus-visible:ring-ring/50"
              href={href(`/gemeinden/${c.bfs_id}`, lang)}
              ><span class="min-w-0 flex-1 truncate"
                >{c.name} <small class="text-[13px] text-muted-foreground">{c.canton}</small></span
              ><span class="shrink-0 text-[13px] text-muted-foreground"
                >{c.documents ? docs(c.documents, lang) : t.missing}</span
              ><ArrowUpRight class="size-3.5 shrink-0 text-zinc-400" aria-hidden="true" /></a
            >{/each}
        </div>
        <div class="mt-4 flex flex-wrap items-center justify-between gap-x-6 gap-y-3 text-sm">
          <p class="text-muted-foreground">
            <!-- the heading counts the communes with documents; a second total here would differ -->
            {#if canton}{s.communeCount.replace('{n}', number(shown.length))} ·
            {/if}{s.byCount}
          </p>
          <div class="flex flex-wrap items-center gap-x-6 gap-y-3">
            {#if visibleCount < shown.length}
              <Button
                variant="outline"
                class="load-more h-9 px-3"
                onclick={() => (visibleCount += 50)}>{t.moreCommunes}</Button
              >
            {/if}
            <a
              class="text-link font-medium text-foreground underline decoration-zinc-300 underline-offset-4 hover:decoration-zinc-900"
              href={href('/suche', lang)}>{t.topicLink}</a
            >
          </div>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- Code, data, paper and API, on a grey band -->
<section id="offen" class="border-t border-border bg-zinc-50">
  <div class="mx-auto max-w-7xl px-4 py-14 sm:px-6 lg:py-24">
    <div class="max-w-2xl">
      <h2 class="text-2xl font-semibold tracking-tight">{s.openTitle}</h2>
      <p class="mt-2 text-zinc-600">{s.openIntro}</p>
    </div>
    <div class="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <a href={SOURCE_URL} class={cardLink}>
        <img src={ghIcon} alt="" class="size-5" />
        <span class="mt-4 inline-flex items-center gap-1 font-medium"
          >{s.codeTitle}<ArrowUpRight class="size-3.5 text-zinc-400" aria-hidden="true" /></span
        >
        <span class="mt-1 text-sm text-zinc-600">{s.codeText}</span>
      </a>
      <a href={DATASET_URL} class={cardLink}>
        <img src={hfIcon} alt="" class="size-5" />
        <span class="mt-4 inline-flex items-center gap-1 font-medium"
          >{s.dataTitle}<ArrowUpRight class="size-3.5 text-zinc-400" aria-hidden="true" /></span
        >
        <span class="mt-1 text-sm text-zinc-600">{s.dataText}</span>
      </a>
      <a href="/tractanda-paper.pdf" class={cardLink}>
        <FileText class="size-5 text-zinc-700" strokeWidth={1.8} aria-hidden="true" />
        <span class="mt-4 inline-flex items-center gap-2 font-medium"
          >{s.paperTitle}
          <Badge variant="secondary" class="font-normal text-zinc-600">{s.draft}</Badge></span
        >
        <span class="mt-1 text-sm text-zinc-600">{s.paperText}</span>
      </a>
      <a href={href('/api', lang)} class={cardLink}>
        <CodeXml class="size-5 text-zinc-700" strokeWidth={1.8} aria-hidden="true" />
        <span class="mt-4 font-medium">{s.apiTitle}</span>
        <span class="mt-1 text-sm text-zinc-600">{s.apiText}</span>
      </a>
    </div>
    <p class="mt-8 text-sm text-muted-foreground">{s.mcpLabel}</p>
    <div
      class="mt-2 flex min-w-0 items-center justify-between gap-3 rounded-lg bg-zinc-950 py-2 pr-2 pl-4 font-mono text-sm text-zinc-100"
    >
      <code class="min-w-0 overflow-x-auto py-1 whitespace-nowrap">{MCP_COMMAND}</code>
      <button
        type="button"
        onclick={copyCommand}
        aria-label={s.copyLabel}
        class="inline-flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 font-sans text-xs text-zinc-400 outline-none hover:bg-zinc-800 hover:text-white focus-visible:ring-2 focus-visible:ring-zinc-400"
        ><Copy class="size-3.5" aria-hidden="true" />{copied ? s.copied : s.copy}</button
      >
      <span class="sr-only" aria-live="polite">{copied ? s.copied : ''}</span>
    </div>
  </div>
</section>
