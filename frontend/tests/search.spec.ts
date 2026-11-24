import { test, expect } from '@playwright/test';

// Against the offline fixture (tests/access-fixture.py): page text for files of Wetzikon (121),
// one of them shared with Hinwil (117). The fixture's search ranks at most 22 pages.
const sha = (n: number) => n.toString(16).padStart(64, '0');

test('full-text search: matching pages, marks, filters, cap and paging', async ({ page }) => {
  const errors: string[] = [];
  const requests: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('request', (request) => requests.push(new URL(request.url()).pathname));
  await page.goto('/de/suche');
  const box = page.getByLabel('Stichwort oder Frage …');
  await expect(page.locator('.search-help')).toContainText('Alle Wörter müssen auf der Seite');
  await expect(page.locator('.search-help')).toContainText('maschinell ausgelesen');
  await expect(box).toHaveAttribute('aria-describedby', 'search-help');
  await box.fill('Steuerfuss');
  await box.press('Enter');
  await expect(page).toHaveURL(/\/de\/suche\?q=Steuerfuss$/);
  await expect(page).toHaveTitle('Steuerfuss — Dokumente suchen — Tractanda');
  await expect(page.locator('.result-count')).toHaveText(/^21\s+Ergebnisse/);
  await expect(page.locator('.capped')).toHaveText(
    /^Die Suche trifft 20.000 Seiten oder mehr\. .* der 20.000 besten Seiten\./
  );
  const rows = page.locator('.document-row');
  await expect(rows).toHaveCount(20);

  // a file with two matching pages: page, section type, link to the original there, marks
  const booklet = rows.first();
  await expect(booklet.locator('h2')).toHaveText('Einladung zur Gemeindeversammlung');
  await expect(booklet.locator('.hits-count')).toHaveText('2 passende Seiten');
  const hits = booklet.locator('.hit');
  await expect(hits.locator('.hit-meta')).toHaveText(['S. 11 ↗ · Protokoll', 'S. 13 ↗ · Budget']);
  await expect(hits.first().locator('a')).toHaveAttribute(
    'href',
    `/original/${sha(1)}?page=11&commune=121`
  );
  await expect(hits.first().locator('a')).toHaveAttribute('target', '_blank');
  await expect(hits.first().locator('mark')).toHaveText(['Steuerfuss']);
  await expect(hits.first().locator('.snippet')).toHaveAttribute('lang', 'de');
  // text that looks like HTML stays text
  await expect(hits.nth(1).locator('.snippet')).toContainText('Aufwand <b>und</b> Ertrag');
  await expect(hits.nth(1).locator('.snippet b')).toHaveCount(0);
  await expect(booklet.locator('.match')).toHaveCount(0);
  // the shared file opens at its first commune's address
  await expect(rows.nth(1).locator('.hit a')).toHaveAttribute(
    'href',
    `/original/${sha(2)}?page=5&commune=117`
  );
  await expect(page.locator('.as-data code')).toHaveText(
    'GET /api/pages?q=Steuerfuss&offset=0&limit=20'
  );

  // the facets of the search: canton and commune, the body without counts
  const canton = page.getByLabel('Kanton', { exact: true });
  await expect(canton.locator('option')).toHaveText(['Alle Kantone', 'ZH (21)']);
  const commune = page.getByLabel('Gemeinde', { exact: true });
  await expect(commune.locator('option')).toHaveText([
    'Alle Gemeinden',
    'Wetzikon (21)',
    'Hinwil (1)'
  ]);
  const bodies = await page
    .getByLabel('Gremium', { exact: true })
    .locator('option')
    .allTextContents();
  expect(bodies).toContain('Parlament');
  expect(bodies).not.toContain('Privat');
  await expect(page.getByLabel('Teil', { exact: true }).locator('option').nth(1)).toHaveText(
    'Protokoll (13)'
  );

  // paging: the second page holds the last file
  await page.getByRole('link', { name: 'Weiter →' }).click();
  await expect(page).toHaveURL(/\?q=Steuerfuss&offset=20$/);
  await expect(rows).toHaveCount(1);
  await page.getByRole('link', { name: '← Zurück' }).click();
  await expect(rows).toHaveCount(20);

  // a commune: the search narrows, the cap is not reached
  await commune.selectOption('117');
  await expect(page).toHaveURL(/\?q=Steuerfuss&commune=117$/);
  await expect(rows).toHaveCount(1);
  await expect(page.locator('.capped')).toHaveCount(0);
  await expect(page.locator('.as-data code')).toHaveText(
    'GET /api/pages?q=Steuerfuss&commune=117&offset=0&limit=20'
  );
  await canton.selectOption('ZH');
  await expect(page).toHaveURL(/\?q=Steuerfuss&canton=ZH&commune=117$/);
  await expect(rows).toHaveCount(1);

  // offsets count characters, not UTF-16 units: a character outside the BMP comes first
  await page.goto(`/de/suche?q=${encodeURIComponent('"crédit d’engagement"')}`);
  await expect(rows.first().locator('mark')).toHaveText(['crédit d’engagement']);
  // a file in two languages: the snippet's language is not named
  await expect(rows.first().locator('.snippet')).not.toHaveAttribute('lang');
  await page.screenshot({ path: 'test-results/search-desktop.png', fullPage: true });

  // the website never asks for the text of pages
  expect(requests.filter((path) => /^\/api\/documents\/[0-9a-f]+\/pages/.test(path))).toEqual([]);
  expect(errors).toEqual([]);
});

test('a refused search, and the title search when the server has no index', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  // fewer than 3 characters before *
  await page.goto('/de/suche?q=ab*');
  await expect(page.locator('.search-notice')).toContainText('mindestens drei Buchstaben');
  await expect(page.locator('.empty-state h2')).toHaveText('Diese Suche ist nicht möglich.');
  await expect(page.locator('.document-row')).toHaveCount(0);

  // 503: the titles are searched, with a note
  await page.route(/\/api\/pages\?/, (route) =>
    route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: '{"detail": "Full-text search is not available on this server."}'
    })
  );
  await page.goto('/de/suche?q=reglement&canton=ZH');
  await expect(page.locator('.search-notice')).toHaveText(
    'Die Suche im Text ist hier zurzeit nicht verfügbar. Gesucht wurde in den Titeln.'
  );
  const rows = page.locator('.document-row');
  await expect(rows).toHaveCount(1);
  await expect(rows.locator('h2')).toHaveText('Reglement über die Abfallentsorgung');
  await expect(rows.locator('.hit')).toHaveCount(0);
  await expect(page.locator('.document-filters select')).toHaveCount(3);
  await expect(page.locator('.as-data code')).toHaveText(
    'GET /api/documents?q=reglement&offset=0&limit=20'
  );
  await page.getByRole('link', { name: 'FR', exact: true }).click();
  await expect(page.locator('.search-notice')).toContainText(
    'La recherche a porté sur les titres.'
  );
  expect(errors).toEqual([]);
});

test('commune page search, title list of a commune, languages and phone', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/de/gemeinden/117');
  const box = page.getByLabel('In den Texten dieser Gemeinde suchen');
  await box.fill('Steuerfuss');
  await box.press('Enter');
  await expect(page).toHaveURL(/\/de\/suche\?q=Steuerfuss&commune=117$/);
  const rows = page.locator('.document-row');
  await expect(rows).toHaveCount(1);
  await expect(page.getByLabel('Gemeinde', { exact: true })).toHaveValue('117');
  await expect(rows.locator('.hits-count')).toHaveText('1 passende Seite');

  // without words: the commune's files by title, the commune as a removable filter
  await page.getByLabel('Stichwort oder Frage …').fill('');
  await page.getByRole('button', { name: 'Suchen', exact: true }).click();
  await expect(page).toHaveURL(/\/de\/suche\?commune=117$/);
  await expect(page.locator('.active-commune')).toContainText('Gemeinde: Hinwil');
  await expect(rows).toHaveCount(1);
  await expect(page.locator('.as-data code')).toHaveText(
    'GET /api/documents?commune=117&offset=0&limit=20'
  );
  await page.locator('.active-commune').getByRole('link', { name: 'Filter entfernen' }).click();
  await expect(page).toHaveURL(/\/de\/suche$/);
  await expect(page.locator('.result-count')).toHaveText(/^25\s+Ergebnisse/);

  // French and Italian
  await page.goto('/fr/suche?q=Investitionskredit');
  await expect(page.getByLabel('Mot-clé ou question …')).toHaveValue('Investitionskredit');
  await expect(rows.first().locator('.hits-count')).toHaveText('2 pages correspondantes');
  await expect(rows.first().locator('.hit-meta')).toHaveText([
    'p. 3 ↗ · Préavis',
    'p. 11 ↗ · Procès-verbal'
  ]);
  await expect(page.locator('.search-help')).toContainText('n’a pas été vérifié');
  await page.getByRole('link', { name: 'IT', exact: true }).click();
  await expect(page).toHaveURL(/\/it\/suche\?q=Investitionskredit$/);
  await expect(rows.first().locator('.hit-meta').first()).toHaveText('p. 3 ↗ · Messaggio');
  await expect(page.locator('.search-help')).toContainText('non è stato verificato');
  await page.goto('/it/gemeinden/121');
  await expect(page.getByLabel('Cerca nei testi di questo comune')).toBeVisible();

  // a commune without files has no search box
  await page.goto('/de/gemeinden/1');
  await expect(page.locator('.text-search')).toHaveCount(0);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/de/suche?q=Steuerfuss');
  await expect(rows.first().locator('.hit').first()).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
  ).toBeTruthy();
  await page.screenshot({ path: 'test-results/search-mobile.png', fullPage: true });
  await page.goto('/de/gemeinden/121');
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
  ).toBeTruthy();
  await page.screenshot({ path: 'test-results/search-commune-mobile.png' });
  expect(errors).toEqual([]);
});
