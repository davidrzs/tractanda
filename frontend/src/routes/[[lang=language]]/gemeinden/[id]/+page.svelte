<script lang="ts">
  import AccessNotice from '$lib/AccessNotice.svelte';
  import DocumentList from '$lib/DocumentList.svelte';
  import Holdings from '$lib/Holdings.svelte';
  import Map from '$lib/Map.svelte';
  import { Button } from '$lib/components/ui/button';
  import { Input } from '$lib/components/ui/input';
  import { commune as texts, count } from '$lib/copy/commune';
  import { filtered } from '$lib/documents';
  import { copy, href, label, when, type Lang } from '$lib/i18n';
  import { page } from '$app/state';
  import Search from '@lucide/svelte/icons/search';
  let { data } = $props();
  let lang = $derived((page.params.lang || 'de') as Lang);
  let t = $derived(copy[lang]);
  let k = $derived(texts[lang]);
  let c = $derived(data.commune);
  let register = $derived(c.register);
  let website = $derived(c.website ?? register?.website ?? null);
  const domain = (url: string) => url.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '');
  const status = (s: string) => (k.status as Record<string, string>)[s] ?? s;
  // the register's status as a quiet dot: collected, refused, or anything else
  const dot = (s: string) =>
    s === 'configured'
      ? 'bg-emerald-600'
      : s.startsWith('blocked')
        ? 'bg-amber-500'
        : 'bg-zinc-400';
  // One status line. What the crawler saw last outranks the register's entry, so the line never
  // says "collected" above a notice that collecting fails; a configured commune without documents
  // says so.
  let line = $derived.by(() => {
    const live = (c.access || []).filter((a) => !a.stale);
    const seen = live[0]?.checked_at;
    if (live.some((a) => a.status === 'restricted'))
      return { label: t.accessRestricted, dot: 'bg-amber-500', date: seen };
    if (live.some((a) => a.status === 'rate_limited' || a.status === 'robots_unavailable'))
      return { label: k.disturbed, dot: 'bg-amber-500', date: seen };
    if (!register) return null;
    if (register.status === 'configured' && !c.documents)
      return { label: k.noDocumentsYet, dot: 'bg-zinc-400', date: register.checked };
    return { label: status(register.status), dot: dot(register.status), date: register.checked };
  });
</script>

<svelte:head><title>{k.title.replace('{name}', c.name)} — Tractanda</title></svelte:head>
<div class="mx-auto max-w-7xl px-4 sm:px-6">
  <nav class="mt-6 text-sm text-muted-foreground" aria-label={k.path}>
    <ol class="flex flex-wrap items-center gap-x-1.5">
      <li>
        <a
          class="rounded-sm outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          href={`${href('/', lang)}#gemeinden`}>{t.communes}</a
        >
      </li>
      <li>
        <span aria-hidden="true">/</span>
        {#if k.cantons[c.canton]}<a
            class="rounded-sm outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
            href={href(`/kantone/${c.canton.toLowerCase()}`, lang)}>{k.cantons[c.canton]}</a
          >{:else}{c.canton}{/if}
      </li>
      <li>
        <span aria-hidden="true">/</span>
        <span class="text-foreground" aria-current="page">{c.name}</span>
      </li>
    </ol>
  </nav>

  <div class="mt-4 grid gap-8 lg:grid-cols-12 lg:gap-10">
    <div class="min-w-0 lg:col-span-8">
      <div class="flex flex-wrap items-baseline gap-x-3">
        <h1 class="text-3xl font-semibold tracking-tight sm:text-4xl">{c.name}</h1>
        <span class="text-base text-muted-foreground">{c.canton}</span>
      </div>
      <p class="mt-2 text-muted-foreground">
        {c.documents
          ? count(c.documents, 'document', lang)
          : t.missing}{#if c.language}{' · '}{label(
            lang,
            'language',
            c.language
          )}{/if}{#if website}{' · '}<a
            class="text-link rounded-sm text-foreground underline decoration-border underline-offset-4 outline-none hover:decoration-foreground focus-visible:ring-2 focus-visible:ring-ring"
            href={website}
            target="_blank"
            rel="noreferrer">{domain(website)} ↗</a
          >{/if}
      </p>
      {#if line}<div class="status-line mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <span
            class="inline-flex items-center gap-1.5 rounded-md bg-muted px-2 py-0.5 text-foreground"
            ><span class="size-1.5 rounded-full {line.dot}" aria-hidden="true"
            ></span>{line.label}</span
          >
          {#if line.date}<span class="text-muted-foreground"
              >{k.checked.replace('{date}', when(line.date, lang))}</span
            >{/if}
        </div>{/if}
      <!-- the search page's full-text search, filtered to this commune -->
      {#if c.documents}<form
          class="text-search mt-6 flex max-w-2xl flex-col gap-2 sm:flex-row"
          method="GET"
          action={href('/suche', lang)}
          role="search"
        >
          <div class="relative flex-1">
            <label for="commune-text-search" class="sr-only">{t.communeSearch}</label>
            <Search
              class="pointer-events-none absolute top-1/2 left-3.5 size-5 -translate-y-1/2 text-muted-foreground"
              aria-hidden="true"
            />
            <Input
              id="commune-text-search"
              name="q"
              type="search"
              required
              maxlength={500}
              placeholder={k.searchPlaceholder.replace('{name}', c.name)}
              class="h-11 bg-background pr-3 pl-11 text-base md:text-base"
            />
          </div>
          <input type="hidden" name="commune" value={c.bfs_id} />
          <Button type="submit" class="h-11 px-6">{t.searchButton}</Button>
        </form>{/if}
    </div>
    <div class="lg:col-span-4">
      <div class="mini-map max-w-md lg:max-w-none">
        <Map communes={[{ ...c, register_status: null, register_note: null }]} compact />
      </div>
    </div>
  </div>

  {#if c.holdings?.length}<Holdings
      holdings={c.holdings}
      years={data.years}
      filters={data.filters}
      {lang}
      list="documents-title"
    />{/if}

  <AccessNotice access={c.access || []} {lang} />

  <section class="mt-12 border-t border-border pt-8" aria-labelledby="documents-title">
    <h2
      id="documents-title"
      tabindex="-1"
      class="scroll-mt-6 text-xl font-semibold tracking-tight outline-none"
    >
      {t.documents}
    </h2>
    <p class="mt-1 max-w-3xl text-sm text-muted-foreground">{t.gaps} {t.note}</p>
    <div class="document-list mt-4">
      <DocumentList
        results={data.results}
        filters={data.filters}
        query={data.query}
        {lang}
        commune={c.bfs_id}
        empty={c.documents === 0 && !filtered(data.filters) ? t.accessEmpty : undefined}
      />
    </div>
  </section>
</div>
