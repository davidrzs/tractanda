import { test, expect } from '@playwright/test';
test('map, commune navigation, document list, original sources and language continuity', async ({
  page
}) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/de');
  await expect(
    page.getByRole('heading', { name: 'Die Dokumente der Schweizer Gemeinden' })
  ).toBeVisible();
  await expect(page.locator('.atlas-map svg a').first()).toHaveAttribute(
    'href',
    /\/de\/gemeinden\/\d+/
  );
  await expect(page.locator('.coverage-count')).toContainText(
    'Gemeinden mit gesammelten Dokumenten'
  );
  await page.screenshot({ path: 'test-results/home-desktop.png', fullPage: true });
  await page.getByLabel('Gemeinde finden').fill('Wetzikon');
  await page.locator('.suggestions a').first().click();
  await expect(page.getByRole('heading', { name: 'Wetzikon', level: 1 })).toBeVisible();
  await expect(page.locator('.document-row .text-link').first()).toHaveAttribute(
    'href',
    /^\/original\/[0-9a-f]{64}\?commune=121$/
  );
  // the first section type with files narrows the list and goes into the URL
  const section = page.getByLabel('Teil', { exact: true });
  const value = await section.locator('option:not([disabled])').nth(1).getAttribute('value');
  await section.selectOption(value!);
  await expect(page).toHaveURL(new RegExp(`/de/gemeinden/121\\?section=${value}$`));
  await expect(page.locator('.document-row').first()).toBeVisible();
  await page.getByRole('link', { name: 'FR', exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/fr/gemeinden/121\\?section=${value}$`));
  await expect(page.locator('html')).toHaveAttribute('lang', 'fr');
  await expect(page.getByPlaceholder('Rechercher dans les titres …')).toBeVisible();
  await expect(page.getByLabel('Partie', { exact: true })).toHaveValue(value!);
  expect((await page.locator('.mini-map svg').boundingBox())!.width).toBeGreaterThan(200);
  await page.screenshot({ path: 'test-results/commune-desktop.png', fullPage: false });
  await page.getByRole('link', { name: 'IT', exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('lang', 'it');
  await expect(page.getByLabel('Parte', { exact: true })).toBeVisible();
  await page.goto('/de/suche');
  await expect(page.locator('.document-row .communes').first()).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/it');
  await expect(
    page.getByRole('heading', { name: 'I documenti dei comuni svizzeri' })
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
  ).toBeTruthy();
  await page.screenshot({ path: 'test-results/home-mobile.png', fullPage: true });
  expect(errors).toEqual([]);
});

test('a canton lists its communes and is linked from the start and commune pages', async ({
  page
}) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/de');
  const cantons = page.getByRole('navigation', { name: 'Gemeinden nach Kanton' });
  await expect(cantons.getByRole('link')).toHaveCount(26);
  await expect(cantons.getByRole('link', { name: 'Kanton Zürich' })).toHaveAttribute(
    'href',
    '/de/kantone/zh'
  );
  await page.goto('/de/gemeinden/121');
  // the commune page keeps the server's title, as search engines index it after the app ran
  await expect(page).toHaveTitle(
    'Wetzikon: Protokolle, Traktanden und Budgets der Gemeinde — Tractanda'
  );
  await page.getByRole('link', { name: 'Kanton Zürich' }).click();
  await expect(page).toHaveURL(/\/de\/kantone\/zh$/);
  await expect(page.getByRole('heading', { name: 'Kanton Zürich', level: 1 })).toBeVisible();
  await expect(page).toHaveTitle(
    'Kanton Zürich: Protokolle, Traktanden und Budgets der Gemeinden — Tractanda'
  );
  await expect(page.locator('.canton-count')).toContainText('Gemeinden mit gesammelten Dokumenten');
  await expect(page.locator('.communes-list a[href="/de/gemeinden/121"]')).toContainText(
    'Wetzikon'
  );
  expect((await page.locator('.mini-map svg').boundingBox())!.width).toBeGreaterThan(200);
  await page.screenshot({ path: 'test-results/canton-desktop.png', fullPage: true });
  await page.getByRole('link', { name: 'EN', exact: true }).click();
  await expect(page).toHaveURL(/\/en\/kantone\/zh$/);
  await expect(page.getByRole('heading', { name: 'Canton of Zurich', level: 1 })).toBeVisible();
  // an unknown canton is the error page
  await page.goto('/de/kantone/xx');
  await expect(page.getByRole('heading', { name: 'Kanton Zürich' })).toHaveCount(0);
  expect(errors).toEqual([]);
});
