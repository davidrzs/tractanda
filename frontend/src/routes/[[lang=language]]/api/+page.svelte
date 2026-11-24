<script lang="ts">
  import { page } from '$app/state';
  import { apiPage } from '$lib/api-page';
  import { Button } from '$lib/components/ui/button';
  import { type Lang } from '$lib/i18n';
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
  import ArrowUpRight from '@lucide/svelte/icons/arrow-up-right';
  import Check from '@lucide/svelte/icons/check';
  import Copy from '@lucide/svelte/icons/copy';
  let lang = $derived((page.params.lang || 'de') as Lang);
  let t = $derived(apiPage[lang]);
  let mcpUrl = $derived(`${page.url.origin}/mcp`);
  let snippet = $derived(JSON.stringify({ mcpServers: { tractanda: { url: mcpUrl } } }, null, 2));
  let copied = $state(false);
  async function copySnippet() {
    try {
      await navigator.clipboard.writeText(snippet);
      copied = true;
      setTimeout(() => (copied = false), 2000);
    } catch {
      // the snippet stays selectable by hand
    }
  }

  // Scalar in the site's colours, font and radius; its own layout otherwise
  const customCss = `
    .scalar-app {
      --scalar-font: 'Inter Variable', ui-sans-serif, system-ui, sans-serif;
      --scalar-font-code: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      --scalar-color-1: #18181b;
      --scalar-color-2: #52525b;
      --scalar-color-3: #71717a;
      --scalar-color-accent: #b3121c;
      --scalar-background-1: #ffffff;
      --scalar-background-2: #fafafa;
      --scalar-background-3: #f4f4f5;
      --scalar-background-accent: #b3121c1a;
      --scalar-border-color: #e4e4e7;
      --scalar-radius: 0.375rem;
      --scalar-radius-lg: 0.5rem;
      --scalar-radius-xl: 0.75rem;
    }
    /* links that would hand this site's description to Scalar's hosted client */
    .scalar-app a[href^='https://client.scalar.com'] {
      display: none;
    }`;

  // The interactive reference: loaded only on this page, from this site only. No Scalar fonts, no
  // request proxy, no telemetry, no AI agent, no developer tools, no link to Scalar's hosted
  // client; "try it" goes to this origin.
  let container: HTMLDivElement;
  let status = $state<'loading' | 'ready' | 'failed'>('loading');
  $effect(() => {
    // Scalar has German and French; Italian falls back to English
    const locale = lang === 'it' ? 'en' : lang;
    const origin = page.url.origin;
    let instance: { destroy: () => void } | undefined;
    let stopped = false;
    status = 'loading';
    Promise.all([import('@scalar/api-reference'), import('@scalar/api-reference/style.css')])
      .then(([{ createApiReference }]) => {
        if (stopped) return;
        instance = createApiReference(container, {
          url: '/api/openapi.json',
          servers: [{ url: origin }],
          withDefaultFonts: false,
          telemetry: false,
          agent: { disabled: true },
          mcp: { disabled: true },
          showDeveloperTools: 'never',
          hideClientButton: true,
          hideDarkModeToggle: true,
          forceDarkModeState: 'light',
          documentDownloadType: 'json',
          defaultHttpClient: { targetKey: 'shell', clientKey: 'curl' },
          localization: { locale },
          theme: 'default',
          customCss,
          _integration: 'svelte'
        });
        status = 'ready';
      })
      .catch(() => {
        if (!stopped) status = 'failed';
      });
    return () => {
      stopped = true;
      instance?.destroy();
    };
  });
</script>

<svelte:head>
  <title>{t.title} — Tractanda</title>
  <meta name="description" content={t.intro} />
</svelte:head>

<article class={cn('api-page', pageClass)}>
  <div class={columnClass}>
    <header>
      <h1 class={h1Class}>{t.title}</h1>
      <p class={introClass}>{t.intro}</p>
      <p class={pClass}>{t.license}</p>
      <p class={pClass}>{t.caveat}</p>
      <p class="mt-4 text-sm">
        <a
          class={cn('text-link inline-flex items-center gap-1', linkClass)}
          href="/api/openapi.json"
          target="_blank"
          rel="noreferrer">{t.openapi}<ArrowUpRight class="size-4" aria-hidden="true" /></a
        >
      </p>
    </header>

    <section id="mcp" class={cn('mcp', sectionClass)}>
      <h2 class={h2Class}>{t.mcpTitle}</h2>
      <p class={pClass}>
        {t.mcp.split('{url}')[0]}<code class={codeClass}>{mcpUrl}</code>{t.mcp.split('{url}')[1]}
      </p>
      <p class={pClass}>{t.mcpConfig}</p>
      <div class="snippet mt-3 overflow-hidden rounded-lg border border-border bg-zinc-50">
        <div class="flex items-center justify-between border-b border-border py-1.5 pr-1.5 pl-4">
          <span class="text-xs font-medium text-muted-foreground">JSON</span>
          <Button variant="ghost" size="sm" onclick={copySnippet} aria-live="polite">
            {#if copied}<Check aria-hidden="true" />{:else}<Copy aria-hidden="true" />{/if}{copied
              ? t.copied
              : t.copy}
          </Button>
        </div>
        <pre class="overflow-x-auto px-4 py-3 font-mono text-sm leading-6"><code>{snippet}</code
          ></pre>
      </div>
      <p class={pClass}>{t.mcpConnector}</p>
    </section>

    <section class={cn('reference', sectionClass)} aria-labelledby="reference-title">
      <h2 id="reference-title" class={h2Class}>{t.referenceTitle}</h2>
      <p class={pClass}>{t.referenceNote}</p>
      {#if status === 'loading'}<p class="mt-3 text-sm text-muted-foreground">
          {t.loading}
        </p>{:else if status === 'failed'}<p class="mt-3 text-sm text-destructive">
          {t.failed}
        </p>{/if}
    </section>
  </div>
</article>
<div class="scalar mx-auto mt-6 max-w-7xl px-4 pb-12 sm:px-6" bind:this={container}></div>
