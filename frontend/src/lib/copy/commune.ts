// Texts of the commune page (src/routes/[[lang=language]]/gemeinden/[id]/+page.svelte,
// Holdings.svelte, AccessNotice.svelte). The older shared strings stay in i18n.ts.
import { fmt, type Lang } from '$lib/i18n';

// the register's statuses (data/register/), for a commune and for each publication
type Status =
  | 'configured'
  | 'candidate'
  | 'not_published'
  | 'not_found'
  | 'blocked_robots'
  | 'blocked_js'
  | 'blocked_network'
  | 'blocked_waf'
  | 'needs_feature'
  | 'not_examined';
type Texts = {
  path: string;
  /** The page's title, the server's (server/src/site.rs, commune_title); {name} is the commune's. */
  title: string;
  cantons: Record<string, string>;
  searchPlaceholder: string;
  checked: string;
  status: Record<Status, string>;
  disturbed: string;
  noDocumentsYet: string;
  pagesPerYear: string;
  documentsPerYear: string;
  view: string;
  pages: string;
  documents: string;
  page: [string, string];
  document: [string, string];
  older: string;
  before: string;
  later: string;
  after: string;
  pagesNote: string;
  documentsNote: string;
  clickHint: string;
  asTable: string;
  chartLabel: string;
  published: string;
  organ: string;
  bodies: Record<string, string>;
  kinds: Record<string, string>;
  since: string;
  until: string;
  website: string;
  websiteNote: string;
  data: string;
  dataTitle: string;
  dataNote: string;
  dataLink: string;
};

export const commune: Record<Lang, Texts> = {
  de: {
    path: 'Pfad',
    title: '{name}: Protokolle, Traktanden und Budgets der Gemeinde',
    cantons: {
      ZH: 'Kanton Zürich',
      BE: 'Kanton Bern',
      LU: 'Kanton Luzern',
      UR: 'Kanton Uri',
      SZ: 'Kanton Schwyz',
      OW: 'Kanton Obwalden',
      NW: 'Kanton Nidwalden',
      GL: 'Kanton Glarus',
      ZG: 'Kanton Zug',
      FR: 'Kanton Freiburg',
      SO: 'Kanton Solothurn',
      BS: 'Kanton Basel-Stadt',
      BL: 'Kanton Basel-Landschaft',
      SH: 'Kanton Schaffhausen',
      AR: 'Kanton Appenzell Ausserrhoden',
      AI: 'Kanton Appenzell Innerrhoden',
      SG: 'Kanton St. Gallen',
      GR: 'Kanton Graubünden',
      AG: 'Kanton Aargau',
      TG: 'Kanton Thurgau',
      TI: 'Kanton Tessin',
      VD: 'Kanton Waadt',
      VS: 'Kanton Wallis',
      NE: 'Kanton Neuenburg',
      GE: 'Kanton Genf',
      JU: 'Kanton Jura'
    },
    searchPlaceholder: 'In den Dokumenten von {name} suchen',
    checked: 'Quelle zuletzt geprüft am {date}',
    disturbed: 'Sammeln vorübergehend gestört',
    noDocumentsYet: 'Eingerichtet, noch keine Dokumente',
    status: {
      configured: 'Wird gesammelt',
      candidate: 'Quelle gefunden, noch nicht gesammelt',
      not_published: 'Veröffentlicht keine Protokolle',
      not_found: 'Keine Quelle gefunden',
      blocked_robots: 'Durch robots.txt ausgeschlossen',
      blocked_js: 'Nur mit JavaScript lesbar',
      blocked_network: 'Website nicht erreichbar',
      blocked_waf: 'Website verweigert den Zugriff',
      needs_feature: 'Noch nicht unterstützt',
      not_examined: 'Noch nicht geprüft'
    },
    pagesPerYear: 'Seiten pro Jahr',
    documentsPerYear: 'Dokumente pro Jahr',
    view: 'Darstellung',
    pages: 'Seiten',
    documents: 'Dokumente',
    page: ['Seite', 'Seiten'],
    document: ['Dokument', 'Dokumente'],
    older: 'Dazu {count} aus den Jahren vor {year}',
    before: 'vor {year}',
    later: 'Dazu {count} aus den Jahren nach {year}',
    after: 'nach {year}',
    pagesNote: 'Eine Seite, die zu zwei Teilen gehört, zählt beim ersten.',
    documentsNote: 'Jedes Dokument zählt einmal, nach seinem ersten Teil.',
    clickHint: 'Ein Klick zeigt diese Dokumente in der Liste.',
    asTable: 'Als Tabelle',
    chartLabel: '{title}, {from} bis {to}',
    published: 'Was {name} veröffentlicht',
    organ: 'Gremium',
    bodies: {
      assembly: 'Gemeindeversammlung',
      parliament: 'Gemeindeparlament',
      executive: 'Exekutive'
    },
    kinds: {
      minutes: 'Protokolle',
      decisions: 'Beschlüsse',
      extracts: 'Protokollauszüge',
      results: 'Abstimmungsergebnisse',
      agenda: 'Traktandenlisten',
      documents: 'Dokumente'
    },
    since: 'seit {year}',
    until: 'bis {year}',
    website: 'Website',
    websiteNote: 'Die Website der Gemeinde, von der die Dokumente stammen.',
    data: 'Daten',
    dataTitle: 'JSON-API',
    dataNote: 'Die Dokumente von {name} als Daten, mit Titel, Datum, Gremium und Teilen.',
    dataLink: 'API-Beschreibung'
  },
  fr: {
    path: 'Fil d’Ariane',
    title: '{name} : procès-verbaux, ordres du jour et budgets de la commune',
    cantons: {
      ZH: 'Canton de Zurich',
      BE: 'Canton de Berne',
      LU: 'Canton de Lucerne',
      UR: 'Canton d’Uri',
      SZ: 'Canton de Schwytz',
      OW: 'Canton d’Obwald',
      NW: 'Canton de Nidwald',
      GL: 'Canton de Glaris',
      ZG: 'Canton de Zoug',
      FR: 'Canton de Fribourg',
      SO: 'Canton de Soleure',
      BS: 'Canton de Bâle-Ville',
      BL: 'Canton de Bâle-Campagne',
      SH: 'Canton de Schaffhouse',
      AR: 'Canton d’Appenzell Rhodes-Extérieures',
      AI: 'Canton d’Appenzell Rhodes-Intérieures',
      SG: 'Canton de Saint-Gall',
      GR: 'Canton des Grisons',
      AG: 'Canton d’Argovie',
      TG: 'Canton de Thurgovie',
      TI: 'Canton du Tessin',
      VD: 'Canton de Vaud',
      VS: 'Canton du Valais',
      NE: 'Canton de Neuchâtel',
      GE: 'Canton de Genève',
      JU: 'Canton du Jura'
    },
    searchPlaceholder: 'Rechercher dans les documents de {name}',
    checked: 'Source vérifiée le {date}',
    disturbed: 'Collecte momentanément perturbée',
    noDocumentsYet: 'Configurée, pas encore de documents',
    status: {
      configured: 'Collecte en cours',
      candidate: 'Source trouvée, pas encore collectée',
      not_published: 'Ne publie pas de procès-verbaux',
      not_found: 'Aucune source trouvée',
      blocked_robots: 'Exclu par robots.txt',
      blocked_js: 'Lisible seulement avec JavaScript',
      blocked_network: 'Site inaccessible',
      blocked_waf: 'Le site refuse l’accès',
      needs_feature: 'Pas encore pris en charge',
      not_examined: 'Pas encore examiné'
    },
    pagesPerYear: 'Pages par année',
    documentsPerYear: 'Documents par année',
    view: 'Affichage',
    pages: 'Pages',
    documents: 'Documents',
    page: ['page', 'pages'],
    document: ['document', 'documents'],
    older: 'S’y ajoutent {count} d’avant {year}',
    before: 'avant {year}',
    later: 'S’y ajoutent {count} d’après {year}',
    after: 'après {year}',
    pagesNote: 'Une page qui appartient à deux parties compte pour la première.',
    documentsNote: 'Chaque document compte une fois, selon sa première partie.',
    clickHint: 'Un clic affiche ces documents dans la liste.',
    asTable: 'Sous forme de tableau',
    chartLabel: '{title}, de {from} à {to}',
    published: 'Ce que publie {name}',
    organ: 'Organe',
    bodies: {
      assembly: 'Assemblée communale',
      parliament: 'Parlement communal',
      executive: 'Exécutif communal'
    },
    kinds: {
      minutes: 'Procès-verbaux',
      decisions: 'Décisions',
      extracts: 'Extraits de procès-verbaux',
      results: 'Résultats des votes',
      agenda: 'Ordres du jour',
      documents: 'Documents'
    },
    since: 'depuis {year}',
    until: 'jusqu’en {year}',
    website: 'Site web',
    websiteNote: 'Le site de la commune, d’où proviennent les documents.',
    data: 'Données',
    dataTitle: 'API JSON',
    dataNote:
      'Les documents de {name} en données structurées, avec titre, date, organe et parties.',
    dataLink: 'Description de l’API'
  },
  it: {
    path: 'Percorso',
    title: '{name}: verbali, ordini del giorno e preventivi del comune',
    cantons: {
      ZH: 'Cantone di Zurigo',
      BE: 'Cantone di Berna',
      LU: 'Cantone di Lucerna',
      UR: 'Cantone di Uri',
      SZ: 'Cantone di Svitto',
      OW: 'Cantone di Obvaldo',
      NW: 'Cantone di Nidvaldo',
      GL: 'Cantone di Glarona',
      ZG: 'Cantone di Zugo',
      FR: 'Cantone di Friburgo',
      SO: 'Cantone di Soletta',
      BS: 'Cantone di Basilea Città',
      BL: 'Cantone di Basilea Campagna',
      SH: 'Cantone di Sciaffusa',
      AR: 'Cantone di Appenzello Esterno',
      AI: 'Cantone di Appenzello Interno',
      SG: 'Cantone di San Gallo',
      GR: 'Cantone dei Grigioni',
      AG: 'Cantone di Argovia',
      TG: 'Cantone di Turgovia',
      TI: 'Cantone Ticino',
      VD: 'Cantone di Vaud',
      VS: 'Cantone del Vallese',
      NE: 'Cantone di Neuchâtel',
      GE: 'Cantone di Ginevra',
      JU: 'Cantone del Giura'
    },
    searchPlaceholder: 'Cerca nei documenti di {name}',
    checked: 'Fonte verificata il {date}',
    disturbed: 'Raccolta temporaneamente disturbata',
    noDocumentsYet: 'Configurata, ancora nessun documento',
    status: {
      configured: 'Raccolta in corso',
      candidate: 'Fonte trovata, non ancora raccolta',
      not_published: 'Non pubblica verbali',
      not_found: 'Nessuna fonte trovata',
      blocked_robots: 'Escluso da robots.txt',
      blocked_js: 'Leggibile solo con JavaScript',
      blocked_network: 'Sito non raggiungibile',
      blocked_waf: 'Il sito nega l’accesso',
      needs_feature: 'Non ancora supportato',
      not_examined: 'Non ancora esaminato'
    },
    pagesPerYear: 'Pagine per anno',
    documentsPerYear: 'Documenti per anno',
    view: 'Visualizzazione',
    pages: 'Pagine',
    documents: 'Documenti',
    page: ['pagina', 'pagine'],
    document: ['documento', 'documenti'],
    older: 'Inoltre {count} degli anni prima del {year}',
    before: 'prima del {year}',
    later: 'Inoltre {count} degli anni dopo il {year}',
    after: 'dopo il {year}',
    pagesNote: 'Una pagina che appartiene a due parti conta per la prima.',
    documentsNote: 'Ogni documento conta una volta, secondo la sua prima parte.',
    clickHint: 'Un clic mostra questi documenti nell’elenco.',
    asTable: 'Come tabella',
    chartLabel: '{title}, dal {from} al {to}',
    published: 'Cosa pubblica {name}',
    organ: 'Organo',
    bodies: {
      assembly: 'Assemblea comunale',
      parliament: 'Consiglio comunale',
      executive: 'Esecutivo comunale'
    },
    kinds: {
      minutes: 'Verbali',
      decisions: 'Decisioni',
      extracts: 'Estratti dei verbali',
      results: 'Risultati delle votazioni',
      agenda: 'Ordini del giorno',
      documents: 'Documenti'
    },
    since: 'dal {year}',
    until: 'fino al {year}',
    website: 'Sito web',
    websiteNote: 'Il sito del comune, da cui provengono i documenti.',
    data: 'Dati',
    dataTitle: 'API JSON',
    dataNote: 'I documenti di {name} come dati, con titolo, data, organo e parti.',
    dataLink: 'Descrizione dell’API'
  },
  en: {
    path: 'Breadcrumb',
    title: '{name}: minutes, agendas and budgets of the municipality',
    cantons: {
      ZH: 'Canton of Zurich',
      BE: 'Canton of Bern',
      LU: 'Canton of Lucerne',
      UR: 'Canton of Uri',
      SZ: 'Canton of Schwyz',
      OW: 'Canton of Obwalden',
      NW: 'Canton of Nidwalden',
      GL: 'Canton of Glarus',
      ZG: 'Canton of Zug',
      FR: 'Canton of Fribourg',
      SO: 'Canton of Solothurn',
      BS: 'Canton of Basel-Stadt',
      BL: 'Canton of Basel-Landschaft',
      SH: 'Canton of Schaffhausen',
      AR: 'Canton of Appenzell Ausserrhoden',
      AI: 'Canton of Appenzell Innerrhoden',
      SG: 'Canton of St. Gallen',
      GR: 'Canton of Graubünden',
      AG: 'Canton of Aargau',
      TG: 'Canton of Thurgau',
      TI: 'Canton of Ticino',
      VD: 'Canton of Vaud',
      VS: 'Canton of Valais',
      NE: 'Canton of Neuchâtel',
      GE: 'Canton of Geneva',
      JU: 'Canton of Jura'
    },
    searchPlaceholder: 'Search the documents of {name}',
    checked: 'Source last checked on {date}',
    disturbed: 'Collection temporarily disrupted',
    noDocumentsYet: 'Set up, no documents yet',
    status: {
      configured: 'Being collected',
      candidate: 'Source found, not yet collected',
      not_published: 'Publishes no minutes',
      not_found: 'No source found',
      blocked_robots: 'Excluded by robots.txt',
      blocked_js: 'Readable only with JavaScript',
      blocked_network: 'Website unreachable',
      blocked_waf: 'Website refuses access',
      needs_feature: 'Not yet supported',
      not_examined: 'Not yet checked'
    },
    pagesPerYear: 'Pages per year',
    documentsPerYear: 'Documents per year',
    view: 'View',
    pages: 'Pages',
    documents: 'Documents',
    page: ['page', 'pages'],
    document: ['document', 'documents'],
    older: 'Plus {count} from the years before {year}',
    before: 'before {year}',
    later: 'Plus {count} from the years after {year}',
    after: 'after {year}',
    pagesNote: 'A page that belongs to two parts counts for the first.',
    documentsNote: 'Each document counts once, by its first part.',
    clickHint: 'Clicking shows these documents in the list.',
    asTable: 'As a table',
    chartLabel: '{title}, {from} to {to}',
    published: 'What {name} publishes',
    organ: 'Body',
    bodies: {
      assembly: 'Citizens’ assembly',
      parliament: 'Municipal parliament',
      executive: 'Municipal executive'
    },
    kinds: {
      minutes: 'Minutes',
      decisions: 'Decisions',
      extracts: 'Extracts from minutes',
      results: 'Vote results',
      agenda: 'Agendas',
      documents: 'Documents'
    },
    since: 'since {year}',
    until: 'until {year}',
    website: 'Website',
    websiteNote: 'The municipality’s website, where the documents come from.',
    data: 'Data',
    dataTitle: 'JSON API',
    dataNote: 'The documents of {name} as data, with title, date, body and parts.',
    dataLink: 'API description'
  }
};

// "1 Seite", "12 Seiten"; "1 document", "12 documents"
export const count = (n: number, unit: 'page' | 'document', lang: Lang) =>
  `${fmt(n, lang)} ${commune[lang][unit][n === 1 ? 0 : 1]}`;
