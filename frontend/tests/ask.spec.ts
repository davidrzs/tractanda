import { expect, test, type Page } from '@playwright/test';

// The answer above the search results, against a mocked server. A search made on the site starts
// the answer by itself (proof of work, steps, answer without HTML and with links to the site's
// pages only, the list switching to the answer's own search); a search page opened from outside
// asks nothing and offers a button; refusals; the chatbot off.
const textPage = () => ({
  total: 0,
  capped: false,
  offset: 0,
  limit: 20,
  facets: { years: [], sections: [], cantons: [], communes: [] },
  items: []
});
const documentPage = () => ({
  total: 0,
  offset: 0,
  limit: 20,
  facets: { years: [], sections: [], bodies: [] },
  items: []
});

type Reply = { status: number; body: string };
async function mock(
  page: Page,
  ask: (body: Record<string, unknown>, origin: string) => Reply,
  challengeStatus = 200
) {
  const searched: string[] = [];
  const asked: Record<string, unknown>[] = [];
  await page.route('**/api/communes', (route) => route.fulfill({ json: [] }));
  await page.route('**/api/documents?*', (route) => route.fulfill({ json: documentPage() }));
  await page.route('**/api/pages?*', (route) => {
    searched.push(new URL(route.request().url()).searchParams.get('q') ?? '');
    return route.fulfill({ json: textPage() });
  });
  await page.route('**/api/ask/challenge', (route) =>
    challengeStatus === 200
      ? route.fulfill({ json: { challenge: 'abc.1.def', bits: 4 } })
      : route.fulfill({ status: challengeStatus, json: { detail: 'off', reason: 'off' } })
  );
  await page.route('**/api/ask', async (route) => {
    const body = route.request().postDataJSON();
    asked.push(body);
    const { status, body: text } = ask(body, new URL(route.request().url()).origin);
    await route.fulfill({
      status,
      headers: { 'content-type': status === 200 ? 'text/event-stream' : 'application/json' },
      body: text
    });
  });
  return { searched, asked };
}

const sse = (events: [string, unknown][]) =>
  events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join(':\n\n');
const question = 'Welche Gemeinden im Kanton Bern haben über Tempo 30 diskutiert?';

const answered = (origin: string) => ({
  status: 200,
  body: sse([
    ['step', { tool: 'search_text', args: { q: '"Tempo 30" OR "zone 30"', canton: 'BE' } }],
    ['step', { tool: 'read_pages', args: { sha256: 'aaaa', from: 14, to: 16 } }],
    [
      'answer',
      {
        text:
          'In **Port** wurde es 2021 beschlossen ([Protokoll, S. 15](/original/aaaa?page=15)).\n' +
          `- Mehr [anderswo](https://elsewhere.example/x) und [hier](${origin}//evil.example/x)\n` +
          '- <b>kein HTML</b>'
      }
    ],
    ['end', { outcome: 'answered' }]
  ])
});

test('a search made on the site is answered by itself above the list', async ({ page }) => {
  const { searched, asked } = await mock(page, (_, origin) => answered(origin));
  await page.goto('/de/suche');
  await page.locator('input[name="q"]').fill(question);
  await page.locator('input[name="q"]').press('Enter');
  const card = page.locator('.ask-answer');
  await expect(card.locator('.steps li')).toHaveText([
    'Seiten durchsucht «"Tempo 30" OR "zone 30"»',
    'Seiten gelesen 14–16'
  ]);
  await expect(card.locator('strong')).toHaveText('Port');
  await expect(card.getByRole('link', { name: 'Protokoll, S. 15' })).toHaveAttribute(
    'href',
    '/original/aaaa?page=15'
  );
  await expect(card.getByRole('link', { name: 'Protokoll, S. 15' })).toHaveAttribute(
    'target',
    '_blank'
  );
  // links elsewhere, also hidden behind this site's address, show as text; markup stays text
  await expect(card.getByRole('link', { name: 'anderswo' })).toHaveCount(0);
  await expect(card.getByRole('link', { name: 'hier' })).toHaveCount(0);
  await expect(card.locator('b')).toHaveCount(0);
  await expect(card).toContainText('<b>kein HTML</b>');
  await expect(card).toContainText('90 Tage protokolliert');
  // to ask further in one's own assistant
  await expect(card.getByRole('link', { name: 'Tractanda verbinden' })).toHaveAttribute(
    'href',
    '/de/api#mcp'
  );
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  await card.getByRole('button', { name: 'Frage kopieren' }).click();
  await expect(card.getByRole('button', { name: 'Kopiert' })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(question);
  // the question's own words found nothing: the list takes the answer's search
  await expect(page).toHaveURL(/terms=%22Tempo\+30%22\+OR\+%22zone\+30%22/);
  await expect(page).toHaveURL(/canton=BE/);
  await expect(page.locator('.answer-terms')).toContainText(
    'Die Suche, die die Antwort gemacht hat'
  );
  expect(searched).toContain('"Tempo 30" OR "zone 30"');
  // asked once, with a solved challenge and the site's language
  expect(asked).toHaveLength(1);
  expect(asked[0].question).toBe(question);
  expect(asked[0].lang).toBe('de');
  const digest = new Uint8Array(
    await crypto.subtle.digest(
      'SHA-256',
      new TextEncoder().encode(`abc.1.def:${asked[0].solution}`)
    )
  );
  expect(digest[0] >> 4).toBe(0);
  // the exact words: the list keeps them, and the answer is not asked again
  await page.locator('.answer-terms a').click();
  await expect(page).toHaveURL(/exact=1/);
  await expect(page).not.toHaveURL(/terms=/);
  await expect(card.locator('strong')).toHaveText('Port');
  expect(asked).toHaveLength(1);
});

test('a search page opened from outside, or a filter there, asks nothing until clicked', async ({
  page
}) => {
  const { asked } = await mock(page, (_, origin) => answered(origin));
  await page.goto(`/de/suche?${new URLSearchParams({ q: question })}`);
  const offer = page.locator('.ask-offer');
  await expect(offer).toContainText('Ein Sprachmodell kann aus den Dokumenten eine Antwort');
  // the same words sent again from the field (as a filter change does) ask nothing either
  await page.locator('input[name="q"]').press('Enter');
  await expect(offer).toBeVisible();
  expect(asked).toEqual([]);
  await offer.getByRole('button', { name: 'Diese Frage beantworten' }).click();
  await expect(page.locator('.ask-answer strong')).toHaveText('Port');
  expect(asked).toHaveLength(1);
  expect(asked[0].solution).toBeTruthy();
});

test('a refusal leaves the list and says why; the chatbot off shows nothing', async ({ page }) => {
  await mock(page, () => ({
    status: 429,
    body: JSON.stringify({ detail: 'too many', reason: 'visitor' })
  }));
  await page.goto('/fr/suche?q=Zone+30');
  await page.getByRole('button', { name: 'Répondre à cette question' }).click();
  await expect(page.locator('.answer-problem')).toHaveText(
    'Vous avez reçu le nombre maximal de réponses pour aujourd’hui ; la recherche continue de fonctionner.'
  );
  await expect(page.locator('.result-count')).toBeVisible();

  const other = await page.context().newPage();
  await mock(
    other,
    () => ({ status: 503, body: JSON.stringify({ detail: 'off', reason: 'off' }) }),
    503
  );
  await other.goto('/de/suche?q=Hallenbad');
  await expect(other.locator('.result-count')).toBeVisible();
  await expect(other.locator('.ask-answer')).toHaveCount(0);
});
