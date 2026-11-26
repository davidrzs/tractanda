import { test, expect } from '@playwright/test';

test('coverage counts, hatching, map captions and empty commune pages', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/de');
  // Hinwil and Wetzikon have files; the hatching is the register's blocked_robots
  await expect(page.locator('.coverage-count strong')).toHaveText('2');
  const empty = page.locator('.atlas-map a[href="/de/gemeinden/1"]');
  await expect(empty.locator('path[fill^="url"]')).toHaveCount(1);
  await expect(
    page.locator('.atlas-map a[href="/de/gemeinden/121"] path[fill^="url"]')
  ).toHaveCount(1);
  await expect(page.locator('.atlas-map a[href="/de/gemeinden/2"] path[fill^="url"]')).toHaveCount(
    0
  );
  await expect(page.locator('.atlas-map a[href="/de/gemeinden/3"] path[fill^="url"]')).toHaveCount(
    0
  );
  // the map is outside the tab order: the finder and the list reach the communes by keyboard
  await expect(empty).toHaveAttribute('tabindex', '-1');
  await empty.hover();
  await expect(page.locator('.map-caption')).toContainText('Sammlung teilweise eingeschränkt');
  await page.screenshot({ path: 'test-results/access-home-desktop.png', fullPage: true });
  await empty.click();
  await expect(page.locator('h1')).toHaveText('Aeugst am Albis');
  await expect(page.locator('.access-notice')).toContainText('robots.txt');
  await page.locator('.access-notice summary').click();
  await expect(page.locator('.access-notice a').last()).toHaveAttribute(
    'href',
    'https://commune.example/robots.txt'
  );
  await expect(page.locator('.empty-state')).toContainText('noch keine Dokumente');
  await page.getByRole('link', { name: 'FR', exact: true }).click();
  await expect(page.locator('.access-notice')).toContainText('collecte automatique');
  await page.getByRole('link', { name: 'IT', exact: true }).click();
  await expect(page.locator('.access-notice')).toContainText('raccolta automatica');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('.access-notice summary').click();
  await page.screenshot({ path: 'test-results/access-commune-mobile.png', fullPage: true });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
  ).toBeTruthy();
  await page.goto('/de/gemeinden/2');
  await page.locator('.access-notice summary').click();
  await expect(page.locator('.access-notice')).toContainText('begrenzt zurzeit die Anfragen');
  await page.goto('/de/gemeinden/3');
  await page.locator('.access-notice summary').click();
  await expect(page.locator('.access-notice')).toContainText('älter als sieben Tage');
  expect(errors).toEqual([]);
});

test('about page keeps language, explains limits and offers email contact', async ({ page }) => {
  await page.goto('/de/ueber-uns');
  await expect(page.locator('h1')).toHaveText('Über Tractanda');
  await expect(page.locator('.about-page')).toContainText('ein Projekt von David Zollikofer');
  await expect(page.locator('#klassifikation')).toContainText(
    'Wie die Dokumente eingeordnet werden'
  );
  await expect(page.locator('#run-classify-65e415a748e2')).toContainText('at most 150 pages');
  await expect(page.locator('#run-classify-65e415a748e2 .run-meta')).toHaveText(
    'Modell: model-x · 25 Dokumente'
  );
  // the versions: the current one's counts, the chart, and each version's changes, newest first
  const versions = page.locator('#versionen');
  await expect(versions).toContainText('Aktuelle Version: 2026.11.26');
  await expect(versions.locator('.current div').filter({ hasText: 'Texterkennung' })).toContainText(
    /1.234/ // the browser's de-CH separator
  );
  await expect(versions.locator('svg[role="img"]')).toBeVisible();
  await expect(versions.locator('.releases li')).toHaveText([
    /\+5 Dokumente · \+50 Seiten · \+1.234 im Volltext durchsuchbare Seiten · \+1.234 Seiten aus Texterkennung/,
    /Erste erfasste Version/
  ]);
  const contact = page.locator('.about-page [data-report-email]');
  await expect(contact).toHaveAttribute('href', '#report');
  await page.evaluate(() =>
    document.addEventListener('click', (event) => {
      if ((event.target as Element).closest('[data-report-email]')) event.preventDefault();
    })
  );
  await contact.click();
  await expect(contact).toHaveAttribute('href', /^mailto:.*subject=Tractanda/);
  await expect(page.locator('.about-page .report-address')).toBeVisible();
  for (const language of ['FR', 'IT']) {
    await page.getByRole('link', { name: language, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/${language.toLowerCase()}/ueber-uns$`));
    await expect(page.locator('.about-page')).toContainText('David Zollikofer');
  }
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)
  ).toBeTruthy();
  await page.screenshot({ path: 'test-results/about-mobile.png', fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/de/ueber-uns');
  await page.screenshot({ path: 'test-results/about-desktop.png', fullPage: true });
});

test('the commune finder ignores accents, umlaut spellings and punctuation', async ({ page }) => {
  await page.goto('/de');
  await page.getByLabel('Gemeinde finden').fill('Äugst am');
  await expect(page.locator('.commune-search [role="option"]')).toHaveText([/Aeugst am Albis/]);
});

test('the status line follows what the crawler saw last', async ({ page }) => {
  // Affoltern is configured in the register, but its last check met rate limiting
  await page.goto('/de/gemeinden/2');
  await expect(page.locator('.status-line')).toContainText('Sammeln vorübergehend gestört');
  await expect(page.locator('.status-line')).not.toContainText('Wird gesammelt');
});
