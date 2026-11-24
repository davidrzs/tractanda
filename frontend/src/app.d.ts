// See https://svelte.dev/docs/kit/types#app.d.ts
declare global {
  namespace App {
    interface PageState {
      /** A search made on this site's own pages: the answer above the results starts by itself. */
      ask?: boolean;
    }
  }
}

export {};
