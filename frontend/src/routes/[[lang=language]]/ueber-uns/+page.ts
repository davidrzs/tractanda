import { apiOptional } from '$lib/api';
import type { Releases, Run } from '$lib/types';
export async function load({ fetch }) {
  // the classification runs and what their settings mean for the data, and the released versions;
  // the page stands without them
  const [runs, releases]: [Run[] | null, Releases | null] = await Promise.all([
    apiOptional(fetch, '/runs'),
    apiOptional(fetch, '/releases')
  ]);
  return { runs, releases };
}
