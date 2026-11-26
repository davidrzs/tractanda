<script lang="ts">
  import { page } from '$app/state';
  import { about } from '$lib/about';
  import { licensesPage } from '$lib/copy/pages';
  import { documentCount, href, type Lang } from '$lib/i18n';
  import { privacy } from '$lib/privacy';
  import Releases from '$lib/Releases.svelte';
  import ReportProblem from '$lib/ReportProblem.svelte';
  import {
    codeClass,
    columnClass,
    h1Class,
    h2Class,
    introClass,
    linkClass,
    pClass,
    pageClass,
    sectionClass
  } from '$lib/text-page';
  import { cn } from '$lib/utils';
  import ArrowRight from '@lucide/svelte/icons/arrow-right';
  import ArrowUpRight from '@lucide/svelte/icons/arrow-up-right';
  let lang = $derived((page.params.lang || 'de') as Lang);
  let t = $derived(about[lang]);
  let { data } = $props();
  const name = 'David Zollikofer';
</script>

<svelte:head>
  <title>{t.title} — Tractanda</title>
  <meta name="description" content={t.intro} />
</svelte:head>

<article class={cn('about-page', pageClass)}>
  <div class={columnClass}>
    <header>
      <h1 class={h1Class}>{t.title}</h1>
      <p class={introClass}>{t.intro}</p>
    </header>

    <section class={sectionClass}>
      <h2 class={h2Class}>{t.purposeTitle}</h2>
      <p class={pClass}>{t.purpose}</p>
      <p class={pClass}>{t.aim}</p>
    </section>

    <section class={sectionClass}>
      <h2 class={h2Class}>{t.sourcesTitle}</h2>
      <p class={pClass}>{t.sources}</p>
    </section>

    <section class={sectionClass}>
      <h2 class={h2Class}>{t.methodTitle}</h2>
      <p class={pClass}>{t.method}</p>
      <p class={pClass}>{t.limits}</p>
    </section>

    <section id="klassifikation" class={cn(sectionClass, 'scroll-mt-6')}>
      <h2 class={h2Class}>{t.classificationTitle}</h2>
      <p class={pClass}>{t.classification}</p>
      {#if data.runs?.length}
        <ul class="mt-6 space-y-3">
          {#each data.runs as run (run.id)}
            <li class="run scroll-mt-6 rounded-lg border border-border p-4" id={`run-${run.id}`}>
              <h3 class="font-mono text-sm font-medium break-all">{run.id}</h3>
              <p class="run-meta mt-1 text-sm text-muted-foreground">
                {[run.model && `${t.model}: ${run.model}`, documentCount(run.documents, lang)]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
              {#if run.note}<p class="mt-2 text-sm leading-6 text-zinc-700">{run.note}</p>{/if}
            </li>
          {/each}
        </ul>
      {/if}
    </section>

    <section class={sectionClass}>
      <h2 class={h2Class}>{t.dataTitle}</h2>
      <p class={pClass}>{t.data}</p>
      <ul class="mt-4 divide-y divide-border border-y border-border text-sm">
        <li class="py-3">
          <a class={linkClass} href={href('/api', lang)}>{t.api}</a>
        </li>
        <li class="py-3 text-zinc-700">
          {t.mcp}:
          <code class={codeClass}>{page.url.origin}/mcp</code>
        </li>
        <li class="py-3">
          <a class={linkClass} href="https://huggingface.co/datasets/davidrzs/tractanda"
            >{t.dataset}<ArrowUpRight
              class="ml-0.5 inline size-3.5 align-[-2px]"
              aria-hidden="true"
            /></a
          >
        </li>
        <li class="py-3">
          <a class={linkClass} href="/tractanda-paper.pdf">{t.paper}</a>
        </li>
        <li class="py-3">
          <a class={linkClass} href="/llms.txt">{t.llms}</a>
        </li>
        <li class="py-3">
          <a class={linkClass} href={href('/lizenzen', lang)}>{licensesPage[lang].title}</a>
        </li>
      </ul>
    </section>

    {#if data.releases?.releases.length}
      <section id="versionen" class={cn(sectionClass, 'scroll-mt-6')}>
        <h2 class={h2Class}>{t.versionsTitle}</h2>
        <p class={pClass}>{t.versions}</p>
        <Releases data={data.releases} {lang} />
      </section>
    {/if}

    <section class={sectionClass}>
      <h2 class={h2Class}>{t.privacyTitle}</h2>
      <p class={pClass}>{t.privacy}</p>
      <p class="mt-4">
        <a
          class={cn('text-link inline-flex items-center gap-1', linkClass)}
          href={href('/datenschutz', lang)}
          >{privacy[lang].title}<ArrowRight class="size-4" aria-hidden="true" /></a
        >
      </p>
    </section>

    <section class={sectionClass}>
      <h2 class={h2Class}>{t.teamTitle}</h2>
      <p class={pClass}>
        {t.team.split(name)[0]}<a class={linkClass} href="https://www.davidrzs.com/">{name}</a
        >{t.team.split(name)[1]}
      </p>
      <p class={pClass}>{t.contact}</p>
      <p class="mt-4 text-sm">
        <ReportProblem
          {lang}
          mode="contact"
          class="font-medium text-foreground underline decoration-zinc-300 hover:decoration-zinc-900"
        />
      </p>
    </section>

    <p class={cn(sectionClass, 'text-sm')}>
      <a
        class={cn('text-link inline-flex items-center gap-1', linkClass)}
        href="https://github.com/davidrzs/tractanda"
        >{t.sourceLink}<ArrowUpRight class="size-4" aria-hidden="true" /></a
      >
    </p>
  </div>
</article>
