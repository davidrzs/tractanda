import { commune } from '$lib/copy/commune';
import { error } from '@sveltejs/kit';
export async function load({ params, parent }) {
  // the code in lower case, as the links write it (server/src/routes/pages.rs answers the same)
  const code = params.code.toUpperCase();
  if (params.code !== params.code.toLowerCase() || !(code in commune.de.cantons))
    error(404, 'Canton not found');
  // the page shows the layout's list of communes: without it there is nothing to show
  const { communes } = await parent();
  if (!communes.length) error(503, 'archive unavailable');
  return { code };
}
