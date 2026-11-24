<script lang="ts">
  import '@fontsource-variable/inter'; // served from this site, not from a font service
  import '../app.css';
  import { about } from '$lib/about';
  import { privacy } from '$lib/privacy';
  import ReportProblem from '$lib/ReportProblem.svelte';
  import Brand from '$lib/components/Brand.svelte';
  import { page } from '$app/state';
  import { onMount } from 'svelte';
  import { copy, href, type Lang } from '$lib/i18n';
  import { release } from '$lib/release';
  let { children } = $props();
  let lang = $derived((page.params.lang || 'de') as Lang);
  let t = $derived(copy[lang]);
  $effect(() => {
    document.documentElement.lang = lang;
  });
  // the server's fact sheet for crawlers (server/src/site.rs); the app renders the same facts. The
  // server's <title> stays: Svelte writes each page's title into it. The error page keeps the fact
  // sheet when the app has no data (+error.svelte).
  onMount(() => {
    const facts = document.getElementById('oc-facts');
    if (facts && !facts.dataset.keep) facts.remove();
  });
  const languages: Lang[] = ['de', 'fr', 'it', 'en'];
  const licences = {
    de: 'Lizenzen und Dank',
    fr: 'Licences et remerciements',
    it: 'Licenze e ringraziamenti',
    en: 'Licences and thanks'
  };
  function switchTo(next: Lang) {
    return (
      href(page.url.pathname.replace(/^\/(de|fr|it|en)(?=\/|$)/, '') || '/', next) + page.url.search
    );
  }
  let path = $derived(page.url.pathname);
  let nav = $derived([
    {
      to: href('/', lang),
      label: t.communes,
      current: path === href('/', lang) || /\/(gemeinden|kantone)\//.test(path)
    },
    { to: href('/suche', lang), label: t.search, current: path.includes('/suche') },
    { to: href('/api', lang), label: 'API', current: /^\/(de|fr|it|en)\/api$/.test(path) }
  ]);
</script>

<a
  class="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-white focus:px-3 focus:py-2 focus:shadow"
  href="#main">{t.skip}</a
>
<p
  class="wip border-b border-border bg-zinc-50 px-4 py-2 text-center text-xs text-muted-foreground"
>
  {t.wip}
  <ReportProblem {lang} class="underline underline-offset-2 hover:text-foreground" />
</p>
<header class="site-header border-b border-border">
  <!-- one row on wide screens; on a phone the navigation takes its own row under the name -->
  <div
    class="mx-auto flex max-w-7xl flex-wrap items-center gap-x-8 gap-y-1 px-4 py-3 sm:h-16 sm:flex-nowrap sm:px-6 sm:py-0"
  >
    <a class="brand text-lg text-foreground" href={href('/', lang)} aria-label="Tractanda"
      ><Brand /></a
    >
    <nav
      class="order-last -mx-2 flex w-full min-w-0 items-center gap-1 sm:order-none sm:mx-0 sm:w-auto sm:flex-1 sm:gap-4"
      aria-label={t.archive}
    >
      {#each nav as item}
        <a
          href={item.to}
          aria-current={item.current ? 'page' : undefined}
          class="rounded-md px-2 py-1 text-sm font-medium whitespace-nowrap text-muted-foreground hover:text-foreground aria-[current=page]:text-foreground"
          >{item.label}</a
        >
      {/each}
    </nav>
    <div
      class="languages ml-auto flex items-center gap-0.5 text-sm"
      role="group"
      aria-label="Sprache / Langue / Lingua"
    >
      {#each languages as l}<a
          href={switchTo(l)}
          hreflang={l}
          aria-current={lang === l ? 'true' : undefined}
          class="rounded-md px-2 py-1 text-muted-foreground hover:text-foreground aria-[current=true]:bg-muted aria-[current=true]:font-medium aria-[current=true]:text-foreground"
          >{l.toUpperCase()}</a
        >{/each}
    </div>
  </div>
</header>
<main id="main" class="min-h-[60vh]">{@render children()}</main>
<footer class="site-footer mt-16 border-t border-border">
  <div
    class="mx-auto grid max-w-7xl gap-8 px-4 py-10 text-sm text-muted-foreground sm:grid-cols-2 sm:px-6"
  >
    <div class="space-y-3">
      <a class="footer-brand text-foreground" href={href('/', lang)}><Brand mark="size-4" /></a>
      <p>
        Die Schweiz. Gemeinde für Gemeinde.<br />La Suisse. Commune par commune.<br />La Svizzera.
        Comune per comune.
      </p>
      <p class="maintainer">
        {t.maintainedBy}
        <a
          class="underline underline-offset-4 hover:text-foreground"
          href="https://www.davidrzs.com/">David Zollikofer</a
        >
      </p>
      <p>{t.disclaimer}</p>
    </div>
    <ul class="space-y-1.5 sm:text-right">
      <li>
        <a class="hover:text-foreground" href={href('/ueber-uns', lang)}>{about[lang].title}</a>
      </li>
      <li>
        <a class="hover:text-foreground" href={href('/datenschutz', lang)}>{privacy[lang].title}</a>
      </li>
      <li><a class="hover:text-foreground" href={href('/lizenzen', lang)}>{licences[lang]}</a></li>
      <li><ReportProblem {lang} /></li>
      <li>
        <a class="hover:text-foreground" href="https://github.com/davidrzs/tractanda"
          >{t.openSource} ↗</a
        >
      </li>
      <li>
        <a class="hover:text-foreground" href="https://huggingface.co/datasets/davidrzs/tractanda"
          >{t.dataset} ↗</a
        >
      </li>
      <li><a class="hover:text-foreground" href="/tractanda-paper.pdf">{t.paper}</a></li>
      {#if release.commit}<li>
          <a
            class="hover:text-foreground"
            href="https://github.com/davidrzs/tractanda/commit/{release.commit}"
            >{lang === 'it' ? 'Versione' : 'Version'}
            <span class="font-mono">{release.commit}{release.dirty ? '-dirty' : ''}</span></a
          >
        </li>{/if}
    </ul>
  </div>
</footer>
