<script lang="ts">
  import type { Snippet } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import ChevronDown from '@lucide/svelte/icons/chevron-down';
  import Search from '@lucide/svelte/icons/search';
  import DocumentRow from './DocumentRow.svelte';
  import { searchCopy } from './copy/search';
  import { FILTERS, TEXT_CAP, filtered, type Filters } from './documents';
  import { copy, fmt, href, label, type Lang } from './i18n';
  import {
    BODY_KINDS,
    SECTION_TYPES,
    type Commune,
    type DocumentPage,
    type TextPage
  } from './types';
  // `commune`: the commune page's commune; without it the list spans every commune. `query`: the
  // API query the page loaded the list with, from `endpoint`. Results of /api/pages (a full-text
  // search) show each file's matching pages and offer the canton and commune filters.
  // `placeholder`, `button` and `help`: the text field's placeholder, its button and a help line
  // under it; `above`: what stands between the field and the list (the answer on the search page). `communes`: every commune, to name the commune filter. Across every commune (the
  // search page) the filters stand in a column left of the list; on a commune page above it.
  let {
    results,
    filters,
    query,
    lang,
    commune,
    empty,
    endpoint = '/api/documents',
    placeholder,
    button,
    help,
    above,
    communes = []
  }: {
    results: DocumentPage | TextPage;
    filters: Filters;
    query: string;
    lang: Lang;
    commune?: number;
    empty?: string;
    endpoint?: string;
    placeholder?: string;
    button?: string;
    help?: Snippet;
    above?: Snippet;
    communes?: Commune[];
  } = $props();
  let t = $derived(copy[lang]);
  let more = $derived(searchCopy[lang]);
  let form: HTMLFormElement;
  let text = $derived('capped' in results ? results : null);
  let wide = $derived(commune === undefined);
  // the filter column on a phone: closed until asked for
  let open = $state(false);

  // One selector per facet. Values in the vocabulary's order, else in the API's order (years
  // newest first, cantons by code, communes with the most files first); a value without a filter
  // of its own (null: no year, no body, ...) is listed with its count but not selectable.
  type Key = 'section' | 'body' | 'year' | 'canton' | 'commune';
  type Option = { value: string | number | null; count: number | null };
  type Selector = {
    key: Key;
    name: string;
    all: string;
    facet: Option[];
    order: readonly string[] | null;
  };
  let selectors = $derived.by((): Selector[] => {
    const section: Selector = {
      key: 'section',
      name: t.sectionLabel,
      all: t.allSections,
      facet: results.facets.sections,
      order: SECTION_TYPES
    };
    const year: Selector = {
      key: 'year',
      name: t.yearLabel,
      all: t.allYears,
      facet: results.facets.years,
      order: null
    };
    const body = { key: 'body' as Key, name: t.bodyLabel, all: t.allBodies, order: BODY_KINDS };
    if (!text) return [section, { ...body, facet: (results as DocumentPage).facets.bodies }, year];
    // a full-text search counts no bodies; it leaves out the documents of private bodies
    const bodies = BODY_KINDS.filter((b) => b !== 'private').map((value) => ({
      value,
      count: null
    }));
    return [
      section,
      { ...body, facet: bodies },
      year,
      {
        key: 'canton',
        name: t.cantonLabel,
        all: t.allCantons,
        facet: text.facets.cantons,
        order: null
      },
      {
        key: 'commune',
        name: t.commune,
        all: t.allCommunes,
        facet: text.facets.communes.map((c) => ({ value: c.bfs_id, count: c.count })),
        order: null
      }
    ];
  });
  function options(key: Key, facet: Option[], order: readonly string[] | null) {
    const rank = (f: Option) =>
      f.value === null
        ? Infinity
        : order
          ? order.indexOf(String(f.value)) + 1 || order.length + 1
          : 0;
    const listed = [...facet].sort((a, b) => rank(a) - rank(b));
    // the chosen value stays visible when the other filters leave nothing of it
    if (filters[key] && !listed.some((f) => String(f.value) === filters[key]))
      listed.unshift({ value: filters[key], count: 0 });
    return listed;
  }
  const communeName = (bfs: string) =>
    text?.facets.communes.find((c) => String(c.bfs_id) === bfs)?.name ??
    communes.find((c) => String(c.bfs_id) === bfs)?.name ??
    bfs;
  const name = (key: Key, value: string | number | null) =>
    value === null
      ? t.noValue
      : key === 'year' || key === 'canton'
        ? String(value)
        : key === 'commune'
          ? communeName(String(value))
          : label(lang, key, value);

  // the filters go into the URL; the page's loader reads them from there
  function submit(event: SubmitEvent) {
    event.preventDefault();
    const data = new FormData(form);
    const query = new URLSearchParams();
    for (const key of FILTERS) {
      const value = String(data.get(key) ?? '').trim();
      if (value) query.set(key, value);
    }
    // the answer's search stays with the same words, and goes with new ones
    if (query.get('q') === filters.q && filters.terms) query.set('terms', filters.terms);
    if (query.get('q') === filters.q && filters.exact) query.set('exact', '1');
    const search = String(query);
    // words typed here: the answer above the results may start by itself (search page); a filter
    // alone keeps whatever the page had, so a shared link does not ask through a filter
    goto(`${page.url.pathname}${search ? `?${search}` : ''}`, {
      keepFocus: true,
      noScroll: true,
      state: query.get('q') !== filters.q ? { ask: true } : page.state
    });
  }
  // the list for the visitor's own words, which the answer's search then leaves alone
  let exactLink = $derived.by(() => {
    const query = new URLSearchParams(page.url.searchParams);
    query.delete('terms');
    query.delete('offset');
    query.set('exact', '1');
    return `?${query}`;
  });
  // this page with one parameter changed or removed
  function link(key: string, value: string) {
    const query = new URLSearchParams(page.url.searchParams);
    if (key !== 'offset') query.delete('offset');
    if (value) query.set(key, value);
    else query.delete(key);
    const search = String(query);
    return search ? `?${search}` : page.url.pathname;
  }
  let set = $derived(selectors.filter((s) => filters[s.key]).length);
  // "Page 2 of 5"
  let pageOf = $derived(
    more.pageOf
      .replace('{n}', fmt(Math.floor(filters.offset / results.limit) + 1, lang))
      .replace('{total}', fmt(Math.max(1, Math.ceil(results.total / results.limit)), lang))
  );
  const field =
    'w-full rounded-md border border-input bg-white px-2 text-sm shadow-xs outline-none focus-visible:border-zinc-900 focus-visible:ring-3 focus-visible:ring-primary/15';
  const pageButton =
    'inline-flex h-9 items-center rounded-md border border-border bg-white px-3 font-medium shadow-xs hover:bg-muted';
  const chip =
    'inline-flex items-center gap-1.5 rounded-md bg-muted px-2 py-1 text-xs text-zinc-700';
  const remove = 'rounded px-1 text-sm leading-none text-muted-foreground hover:text-foreground';
</script>

<div class={wide ? 'lg:grid lg:grid-cols-12 lg:gap-x-10' : ''}>
  <form
    class={[
      'document-filters',
      wide && 'lg:col-[1/-1] lg:row-[1/4] lg:grid lg:grid-cols-subgrid lg:grid-rows-subgrid'
    ]}
    method="GET"
    bind:this={form}
    onsubmit={submit}
  >
    <div class={wide ? 'lg:col-[1/-1] lg:row-[1]' : ''}>
      <div class={['flex flex-col gap-2 sm:flex-row', !wide && 'max-w-2xl']}>
        <div class="relative min-w-0 flex-1">
          <Search
            class={[
              'pointer-events-none absolute top-1/2 -translate-y-1/2 text-zinc-400',
              wide ? 'left-3.5 size-5' : 'left-3 size-4'
            ]}
            aria-hidden="true"
          />
          <input
            name="q"
            type="search"
            aria-label={placeholder ?? t.docSearch}
            placeholder={placeholder ?? t.docSearch}
            aria-describedby={help ? 'search-help' : undefined}
            maxlength="500"
            value={filters.q}
            class={[
              'w-full min-w-0 rounded-lg border border-input bg-white pr-3 shadow-xs outline-none placeholder:text-muted-foreground focus-visible:border-zinc-900 focus-visible:ring-4 focus-visible:ring-primary/15',
              wide ? 'h-11 pl-11 text-base' : 'h-9 pl-9 text-sm'
            ]}
          />
        </div>
        <button
          class={[
            'shrink-0 rounded-lg text-sm font-medium whitespace-nowrap shadow-xs outline-none focus-visible:ring-4',
            wide
              ? 'h-11 bg-primary px-6 text-primary-foreground hover:bg-brand-dark focus-visible:ring-primary/25'
              : 'h-9 border border-border bg-white px-4 text-foreground hover:bg-muted focus-visible:ring-primary/15'
          ]}>{button ?? t.filter}</button
        >
      </div>
      {#if help}<div
          class="search-help mt-2 max-w-3xl space-y-1 text-xs leading-5 text-muted-foreground"
          id="search-help"
        >
          {@render help()}
        </div>{/if}
      {#if above}{@render above()}{/if}
    </div>

    <div class={wide ? 'mt-4 lg:col-[1/4] lg:row-[3] lg:mt-0 lg:self-start lg:pt-6' : 'mt-2'}>
      {#if wide}<button
          type="button"
          class="inline-flex h-9 items-center gap-1.5 rounded-md border border-border bg-white px-3 text-sm font-medium shadow-xs hover:bg-muted lg:hidden"
          aria-expanded={open}
          aria-controls="document-selectors"
          onclick={() => (open = !open)}
          >{more.filters}{#if set}<span class="text-muted-foreground">({fmt(set, lang)})</span
            >{/if}<ChevronDown
            class={['size-4 text-muted-foreground transition-transform', open && 'rotate-180']}
            aria-hidden="true"
          /></button
        >{/if}
      <div
        id="document-selectors"
        class={wide
          ? ['mt-3 space-y-4 lg:mt-0 lg:block', !open && 'hidden']
          : 'grid max-w-2xl grid-cols-1 gap-2 sm:grid-cols-3'}
        role="group"
        aria-label={more.filters}
      >
        {#each selectors as s (s.key)}<div class={wide ? 'space-y-1.5' : ''}>
            <label for="filter-{s.key}" class={wide ? 'block text-sm font-semibold' : 'sr-only'}
              >{s.name}</label
            ><select
              id="filter-{s.key}"
              name={s.key}
              class="{field} h-9"
              value={filters[s.key]}
              onchange={() => form.requestSubmit()}
              ><option value="">{s.all}</option>{#each options(s.key, s.facet, s.order) as f}<option
                  value={f.value === null ? '' : String(f.value)}
                  disabled={f.value === null}
                  >{name(s.key, f.value)}{f.count === null
                    ? ''
                    : ` (${fmt(f.count, lang)})`}</option
                >{/each}</select
            >
          </div>{/each}
      </div>
    </div>

    <div
      class={[
        'flex flex-wrap items-end justify-between gap-x-6 gap-y-2 border-b border-border pb-4',
        wide ? 'mt-6 lg:col-[1/-1] lg:row-[2]' : 'mt-4'
      ]}
    >
      <div class="min-w-0">
        <p
          class={[
            'result-count font-semibold tracking-tight [overflow-wrap:anywhere]',
            wide ? 'text-xl' : 'text-base'
          ]}
        >
          <span>{fmt(results.total, lang)} {results.total === 1 ? t.resultOne : t.result}</span
          >{#if filters.q}{` ${more.about.replace('{q}', filters.terms || filters.q)}`}{/if}
        </p>
        {#if filters.terms}<p class="answer-terms mt-1 text-sm text-muted-foreground">
            {more.answerTerms} ·
            <a
              class="rounded-sm font-medium text-foreground underline decoration-zinc-300 underline-offset-4 hover:decoration-zinc-900"
              href={exactLink}>{more.exactWords}</a
            >
          </p>{/if}
        <p class="mt-1 text-sm text-muted-foreground">
          {#if filters.q}{`${text ? more.inText : more.inTitles} · `}{/if}{text
            ? more.bestFirst
            : more.newestFirst}
        </p>
        {#if filters.family || filters.first_family || (!text && wide && filters.commune)}<div
            class="mt-2 flex flex-wrap gap-2"
          >
            {#each [['family', t.familyLabel], ['first_family', more.firstFamily]] as const as [key, name]}{#if filters[key]}<p
                  class="active-filter active-family {chip}"
                >
                  <input type="hidden" name={key} value={filters[key]} /><i
                    class="size-2 rounded-full"
                    style:background-color="var(--family-{filters[key]})"
                    aria-hidden="true"
                  ></i>{name}
                  {label(lang, 'family', filters[key])}
                  <a
                    class={remove}
                    href={link(key, '')}
                    aria-label={t.removeFilter}
                    title={t.removeFilter}>×</a
                  >
                </p>{/if}{/each}
            {#if !text && wide && filters.commune}<p class="active-filter active-commune {chip}">
                <input
                  type="hidden"
                  name="commune"
                  value={filters.commune}
                />{t.communeFilter.replace('{name}', communeName(filters.commune))}
                <a
                  class={remove}
                  href={link('commune', '')}
                  aria-label={t.removeFilter}
                  title={t.removeFilter}>×</a
                >
              </p>{/if}
          </div>{/if}
      </div>
      {#if filtered(filters)}<a
          class="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
          href={page.url.pathname}>{t.reset}</a
        >{/if}
    </div>
  </form>

  <div class={wide ? 'lg:col-[4/-1] lg:row-[3]' : ''}>
    {#if text?.capped}<p
        class="capped mt-4 rounded-md bg-muted px-3 py-2 text-sm leading-6 text-zinc-700"
      >
        {t.capped.replaceAll('{n}', fmt(TEXT_CAP, lang))}
      </p>{/if}
    {#each results.items as doc (doc.sha256)}<DocumentRow
        {doc}
        {lang}
        {commune}
        section={text ? '' : filters.section}
        family={text ? '' : filters.family || filters.first_family}
        hits={'pages' in doc ? doc.pages : undefined}
        matching={'matching_pages' in doc ? doc.matching_pages : undefined}
        q={filters.q}
        fullText={!!text}
      />{:else}<div class="empty-state py-10">
        <h2 class="text-base font-medium">{empty ?? t.noResults}</h2>
        {#if filters.year}<p class="mt-2 text-sm text-muted-foreground">{t.unknownDate}</p>{/if}
        {#if filtered(filters)}<a
            class="mt-3 inline-block text-sm text-primary underline-offset-4 hover:underline"
            href={page.url.pathname}>{t.reset}</a
          >{/if}
      </div>{/each}
    {#if filters.offset > 0 || filters.offset + results.limit < results.total}<nav
        class="pagination mt-6 flex flex-wrap items-center gap-2 text-sm"
        aria-label={more.pages}
      >
        {#if filters.offset > 0}<a
            class={pageButton}
            href={link(
              'offset',
              filters.offset > results.limit ? String(filters.offset - results.limit) : ''
            )}>← {t.prev}</a
          >{/if}
        <span class="px-1 text-muted-foreground">{pageOf}</span>
        {#if filters.offset + results.limit < results.total}<a
            class={pageButton}
            href={link('offset', String(filters.offset + results.limit))}>{t.next} →</a
          >{/if}
      </nav>{/if}
    <p class="list-note mt-6 rounded-lg bg-zinc-50 px-4 py-3 text-sm leading-6 text-zinc-600">
      {t.classificationNote}
      <a
        class="font-medium text-foreground underline underline-offset-4"
        href={`${href('/ueber-uns', lang)}#klassifikation`}>{t.classificationLink}</a
      >
    </p>
    <p
      class="as-data mt-6 flex flex-wrap items-baseline gap-x-3 gap-y-1 border-t border-border pt-4 text-xs text-muted-foreground"
    >
      <span>{t.asData}</span>
      <code
        class="rounded bg-muted px-1.5 py-0.5 font-mono text-foreground [overflow-wrap:anywhere]"
        >GET {endpoint}?{query}</code
      >
      <a
        class="text-foreground underline underline-offset-4 hover:text-primary"
        href={`${endpoint}?${query}`}
        target="_blank"
        rel="noreferrer">JSON ↗</a
      >
      <a
        class="text-foreground underline underline-offset-4 hover:text-primary"
        href={href('/api', lang)}>{t.apiLink}</a
      >
    </p>
  </div>
</div>
