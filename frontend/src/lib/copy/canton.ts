// Texts of the canton page (src/routes/[[lang=language]]/kantone/[code]/+page.svelte) and the
// start page's list of cantons. The canton names are the commune page's (copy/commune.ts).
import type { Lang } from '$lib/i18n';

export const canton: Record<Lang, { title: string; byCanton: string; byName: string }> = {
  de: {
    // the server's title of the page (server/src/site.rs)
    title: '{name}: Protokolle, Traktanden und Budgets der Gemeinden',
    byCanton: 'Gemeinden nach Kanton',
    byName: 'Nach Namen geordnet.'
  },
  fr: {
    title: '{name} : procès-verbaux, ordres du jour et budgets des communes',
    byCanton: 'Communes par canton',
    byName: 'Classées par nom.'
  },
  it: {
    title: '{name}: verbali, ordini del giorno e preventivi dei comuni',
    byCanton: 'Comuni per cantone',
    byName: 'Ordinati per nome.'
  },
  en: {
    title: '{name}: minutes, agendas and budgets of the municipalities',
    byCanton: 'Municipalities by canton',
    byName: 'Sorted by name.'
  }
};
