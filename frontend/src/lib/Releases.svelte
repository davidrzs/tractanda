<script lang="ts">
  import { about } from './about';
  import { fmt, when, type Lang } from './i18n';
  import type { Release, Releases } from './types';
  // The released versions of the data (/api/releases): the counts of the version the site serves,
  // the documents of each version against every file downloaded so far, and each version's
  // changes. The list is the table of the chart.
  let { data, lang }: { data: Releases; lang: Lang } = $props();
  let t = $derived(about[lang]);
  const COUNTS = ['documents', 'pages', 'communes', 'searchable_pages', 'ocr_pages'] as const;
  let current = $derived(data.releases.find((r) => r.version === data.current) ?? null);

  const DAY = 86_400_000;
  const time = (iso: string) => Date.parse(iso);
  let downloaded = $derived.by(() => {
    let total = 0;
    return data.downloads.map((d) => ({ day: d.day, at: time(d.day), files: (total += d.files) }));
  });
  let times = $derived([...downloaded.map((d) => d.at), ...data.releases.map((r) => time(r.date))]);
  let from = $derived(Math.min(...times));
  let to = $derived(Math.max(...times, from + DAY));
  // the y axis: the smallest step of 1, 2, 2.5 or 5 times a power of ten that needs five or fewer
  let axis = $derived.by(() => {
    const max = Math.max(
      1,
      ...downloaded.map((d) => d.files),
      ...data.releases.map((r) => r.documents)
    );
    const power = 10 ** Math.floor(Math.log10(max / 5));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => Math.ceil(max / s) <= 5)!;
    const top = Math.ceil(max / step) * step;
    return { top, ticks: Array.from({ length: top / step + 1 }, (_, i) => i * step) };
  });
  let months = $derived.by(() => {
    const out: number[] = [];
    const d = new Date(from);
    for (let m = Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1); m < to;) {
      out.push(m);
      const next = new Date(m);
      m = Date.UTC(next.getUTCFullYear(), next.getUTCMonth() + 1);
    }
    return out;
  });

  let width = $state(0);
  const height = 220;
  const [L, R, T, B] = [64, 12, 10, 24];
  const x = (at: number) => L + ((at - from) / (to - from)) * (width - L - R);
  const y = (n: number) => T + (1 - n / axis.top) * (height - T - B);
  let area = $derived(
    downloaded.length
      ? `M${x(downloaded[0].at)},${y(0)}` +
          downloaded.map((d) => `L${x(d.at)},${y(d.files)}`).join('') +
          `L${x(downloaded.at(-1)!.at)},${y(0)}Z`
      : ''
  );
  let line = $derived(
    downloaded.map((d, i) => `${i ? 'L' : 'M'}${x(d.at)},${y(d.files)}`).join('')
  );
  // the documents of a version hold until the next one
  let steps = $derived(
    data.releases.length
      ? data.releases
          .map((r, i) => `${i ? `H${x(time(r.date))}V` : `M${x(time(r.date))},`}${y(r.documents)}`)
          .join('') + `H${x(to)}`
      : ''
  );

  const month = (at: number) =>
    new Intl.DateTimeFormat(`${lang}-CH`, { month: 'short', timeZone: 'UTC' }).format(at);
  const inArchive = (at: number) => data.releases.findLast((r) => time(r.date) <= at) ?? null;

  // the crosshair: the day nearest the pointer
  let shown = $state<number | null>(null);
  function point(event: PointerEvent) {
    const box = (event.currentTarget as SVGElement).getBoundingClientRect();
    const at = from + ((event.clientX - box.left - L) / (width - L - R)) * (to - from);
    if (!downloaded.length) return;
    shown = downloaded.reduce(
      (best, d, i) => (Math.abs(d.at - at) < Math.abs(downloaded[best].at - at) ? i : best),
      0
    );
  }
  let hovered = $derived(shown === null ? null : downloaded[shown]);

  function changes(r: Release, before: Release | undefined) {
    if (!before) return t.first;
    const parts = COUNTS.filter((k) => r[k] !== before[k]).map((k) => {
      const n = r[k] - before[k];
      return `${n > 0 ? '+' : '−'}${fmt(Math.abs(n), lang)} ${t.counts[k]}`;
    });
    return parts.length ? parts.join(' · ') : t.unchanged;
  }
</script>

{#if current}
  <p class="mt-6 text-sm text-muted-foreground">
    {t.current}: <span class="font-mono font-medium text-foreground">{current.version}</span>
    · {when(current.date, lang)}
  </p>
  <dl class="current mt-3 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
    {#each COUNTS as k}<div>
        <dt class="text-xs text-muted-foreground first-letter:uppercase">{t.counts[k]}</dt>
        <dd class="text-lg font-semibold tabular-nums">{fmt(current[k], lang)}</dd>
      </div>{/each}
  </dl>
{/if}

{#if downloaded.length || data.releases.length}
  <ul class="mt-6 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
    <li class="inline-flex items-center gap-1.5">
      <span class="size-2.5 rounded-sm bg-(--family-proposals)" aria-hidden="true"
      ></span>{t.downloaded}
    </li>
    <li class="inline-flex items-center gap-1.5">
      <span class="h-0.5 w-3 rounded-full bg-primary" aria-hidden="true"></span>{t.listed}
    </li>
  </ul>
  <div class="relative mt-2" bind:clientWidth={width}>
    {#if width > 0}<svg
        {width}
        {height}
        viewBox="0 0 {width} {height}"
        class="block select-none"
        font-size="11"
        role="img"
        aria-label={t.chartLabel
          .replace('{from}', when(new Date(from).toISOString(), lang))
          .replace('{to}', when(new Date(to).toISOString(), lang))}
        onpointermove={point}
        onpointerleave={() => (shown = null)}
      >
        {#each axis.ticks as v}<line
            x1={L}
            x2={width - R}
            y1={y(v)}
            y2={y(v)}
            class="stroke-border"
            stroke-width="1"
          /><text x={L - 8} y={y(v) + 4} text-anchor="end" class="fill-muted-foreground"
            >{fmt(v, lang)}</text
          >{/each}
        {#each months as m}<text
            x={x(m)}
            y={height - 6}
            text-anchor="middle"
            class="fill-muted-foreground">{month(m)}</text
          >{/each}
        <path d={area} class="fill-(--family-proposals)" fill-opacity="0.15" />
        <path d={line} class="stroke-(--family-proposals)" fill="none" stroke-width="2" />
        <path d={steps} class="stroke-primary" fill="none" stroke-width="2" />
        {#each data.releases as r (r.version)}<circle
            cx={x(time(r.date))}
            cy={y(r.documents)}
            r="4"
            class="fill-primary stroke-background"
            stroke-width="2"
          />{/each}
        {#if hovered}<line
            x1={x(hovered.at)}
            x2={x(hovered.at)}
            y1={T}
            y2={height - B}
            class="stroke-muted-foreground"
            stroke-width="1"
          />{/if}
      </svg>{/if}
    {#if hovered}
      {@const listed = inArchive(hovered.at)}
      <div
        class="pointer-events-none absolute top-0 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs shadow-sm"
        style:left="{Math.min(x(hovered.at) + 8, width - 180)}px"
      >
        <p class="font-medium">{when(hovered.day, lang)}</p>
        <p class="tabular-nums">{t.downloaded}: {fmt(hovered.files, lang)}</p>
        {#if listed}<p class="tabular-nums">
            {t.listed}: {fmt(listed.documents, lang)} ({listed.version})
          </p>{/if}
      </div>
    {/if}
  </div>
{/if}

<ul class="releases mt-6 divide-y divide-border border-y border-border text-sm">
  {#each data.releases.toReversed() as r, i (r.version)}
    <li class="py-3" id={`version-${r.version}`}>
      <p class="flex flex-wrap items-baseline justify-between gap-x-4">
        <span class="font-mono font-medium">{r.version}</span>
        <span class="text-muted-foreground">{when(r.date, lang)}</span>
      </p>
      <p class="mt-1 text-zinc-700">
        {changes(r, data.releases[data.releases.length - 2 - i])}
      </p>
      <p class="mt-0.5 text-xs text-muted-foreground tabular-nums">
        {fmt(r.documents, lang)}
        {t.counts.documents} · {fmt(r.searchable_pages, lang)}
        {t.counts.searchable_pages}
      </p>
    </li>
  {/each}
</ul>
