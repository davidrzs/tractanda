<script lang="ts">
  import Map from '$lib/Map.svelte';
  import { canton as texts } from '$lib/copy/canton';
  import { commune } from '$lib/copy/commune';
  import { docs, start } from '$lib/copy/start';
  import { copy, fmt, href, type Lang } from '$lib/i18n';
  import { type Commune, hasDocuments } from '$lib/types';
  import { page } from '$app/state';
  import ArrowUpRight from '@lucide/svelte/icons/arrow-up-right';
  let { data } = $props();
  let lang = $derived((page.params.lang || 'de') as Lang);
  let t = $derived(copy[lang]);
  let k = $derived(texts[lang]);
  let name = $derived(commune[lang].cantons[data.code]);
  // the canton's communes from the layout's list, by name
  let communes = $derived((data.communes as Commune[]).filter((c) => c.canton === data.code));
  let collected = $derived(communes.filter(hasDocuments).length);
</script>

<svelte:head><title>{k.title.replace('{name}', name)} — Tractanda</title></svelte:head>
<div class="mx-auto max-w-7xl px-4 sm:px-6">
  <nav class="mt-6 text-sm text-muted-foreground" aria-label={commune[lang].path}>
    <ol class="flex flex-wrap items-center gap-x-1.5">
      <li>
        <a
          class="rounded-sm outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
          href={`${href('/', lang)}#gemeinden`}>{t.communes}</a
        >
      </li>
      <li>
        <span aria-hidden="true">/</span>
        <span class="text-foreground" aria-current="page">{name}</span>
      </li>
    </ol>
  </nav>

  <div class="mt-4 grid gap-8 lg:grid-cols-12 lg:gap-10">
    <div class="min-w-0 lg:col-span-8">
      <h1 class="text-3xl font-semibold tracking-tight sm:text-4xl">{name}</h1>
      <p class="canton-count mt-2 text-muted-foreground">
        <strong class="font-medium text-foreground">{fmt(collected, lang)}</strong>
        {t.withDocuments}
      </p>
    </div>
    <div class="lg:col-span-4">
      <div class="mini-map max-w-md lg:max-w-none">
        <Map {communes} compact />
      </div>
    </div>
  </div>

  <section class="mt-12 border-t border-border pt-8" aria-labelledby="communes-title">
    <h2 id="communes-title" class="text-xl font-semibold tracking-tight">{t.communes}</h2>
    <p class="mt-1 text-sm text-muted-foreground">{k.byName}</p>
    <ul class="communes-list mt-4 gap-x-10 sm:columns-2 lg:columns-3">
      {#each communes as c (c.bfs_id)}<li class="break-inside-avoid border-b border-border">
          <a
            class="commune-row flex items-center justify-between gap-3 rounded-md px-2 py-2.5 text-sm text-foreground outline-none hover:bg-zinc-50 focus-visible:ring-3 focus-visible:ring-ring/50"
            href={href(`/gemeinden/${c.bfs_id}`, lang)}
            ><span class="min-w-0 flex-1 truncate">{c.name}</span><span
              class="shrink-0 text-[13px] text-muted-foreground"
              >{c.documents ? docs(c.documents, lang) : start[lang].noDocuments}</span
            ><ArrowUpRight class="size-3.5 shrink-0 text-zinc-400" aria-hidden="true" /></a
          >
        </li>{/each}
    </ul>
  </section>
</div>
