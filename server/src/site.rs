//! The pages search engines and AI assistants read. The front end renders in the browser, so the
//! server puts each page's facts into the HTML it sends anyway: title, description, canonical and
//! language links, schema.org data and a short fact sheet (`<main id="oc-facts">`). A visitor with
//! JavaScript never sees the fact sheet: an inline script hides it, the app removes it on start,
//! shows the same facts and writes its own titles into the same `<title>`. Every visitor receives
//! the same HTML.

use std::path::Path;
use std::sync::Arc;

use axum::extract::{Request, State};
use axum::http::header;
use axum::middleware::Next;
use axum::response::{IntoResponse, Redirect, Response};

use crate::AppState;
use crate::usage::{Hit, bot_name};

/// The site's languages; the first is the default.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Lang {
    De,
    Fr,
    It,
    En,
}

impl Lang {
    pub const ALL: [Lang; 4] = [Lang::De, Lang::Fr, Lang::It, Lang::En];

    pub fn code(self) -> &'static str {
        match self {
            Lang::De => "de",
            Lang::Fr => "fr",
            Lang::It => "it",
            Lang::En => "en",
        }
    }

    /// The Open Graph locale.
    pub fn locale(self) -> &'static str {
        match self {
            Lang::De => "de_CH",
            Lang::Fr => "fr_CH",
            Lang::It => "it_CH",
            Lang::En => "en_GB",
        }
    }

    pub fn words(self) -> &'static Words {
        match self {
            Lang::De => &DE,
            Lang::Fr => &FR,
            Lang::It => &IT,
            Lang::En => &EN,
        }
    }

    /// A count with the thousands separator of the language in Switzerland.
    pub fn number(self, n: i64) -> String {
        let sep = if self == Lang::Fr { "\u{202f}" } else { "’" };
        let digits = n.unsigned_abs().to_string();
        let mut out = String::new();
        for (i, c) in digits.chars().enumerate() {
            if i > 0 && (digits.len() - i).is_multiple_of(3) {
                out.push_str(sep);
            }
            out.push(c);
        }
        if n < 0 { format!("-{out}") } else { out }
    }
}

/// The fixed texts of the pages, per language. `{x}` marks a value filled in by the page.
pub struct Words {
    pub site: &'static str,
    pub totals: &'static str,
    pub with_documents: &'static str,
    pub commune_line: &'static str,
    /// The commune page's title in search results, with the words people search for.
    pub commune_title: &'static str,
    /// The commune page's description in search results; `{years}` is " 2016–2026" or empty.
    pub commune_description: &'static str,
    pub by_year: &'static str,
    pub no_year: &'static str,
    pub untitled: &'static str,
    pub recent: &'static str,
    pub website: &'static str,
    pub about: &'static str,
    pub about_text: &'static str,
    pub privacy: &'static str,
    pub privacy_text: &'static str,
    pub search: &'static str,
    /// The start page's heading over the cantons and the canton page's over its communes.
    pub cantons: &'static str,
    pub communes: &'static str,
    /// The canton page's line, title and description in search results; `{name}` is the canton's
    /// name with its word for canton ("Kanton Zürich"), `{communes}` counts those with documents.
    pub canton_line: &'static str,
    pub canton_title: &'static str,
    pub canton_description: &'static str,
    pub api: &'static str,
    pub api_text: &'static str,
    pub licences: &'static str,
    pub licences_text: &'static str,
}

const DE: Words = Words {
    site: "Offenes Archiv der Dokumente Schweizer Gemeinden: Protokolle, Beschlüsse, Budgets, \
           Rechnungen und weitere Unterlagen von Gemeindeversammlungen, Parlamenten und \
           Gemeinderäten, mit Links zu den Originalen.",
    totals: "{documents} Dokumente aus {communes} Gemeinden, verlinkt auf die Originale. Sie \
             sind automatisch nach Titel, Gremium und Abschnitten eingeordnet; diese Einordnung \
             hat noch niemand geprüft.",
    with_documents: "Gemeinden mit Dokumenten",
    commune_line: "{name} ({canton}): {documents} Dokumente im offenen Archiv Tractanda.",
    commune_title: "{name}: Protokolle, Traktanden und Budgets der Gemeinde",
    commune_description: "{name} ({canton}): Protokolle, Traktandenlisten, Budgets und Rechnungen{years}, \
                          {documents} Dokumente, im Volltext durchsuchbar, mit Links zu den Originalen.",
    by_year: "Dokumente nach Jahr",
    no_year: "ohne Jahr",
    untitled: "Dokument ohne Titel",
    recent: "Neueste Dokumente",
    website: "Website der Gemeinde",
    about: "Über Tractanda",
    about_text: "Tractanda sammelt die öffentlichen Dokumente der Schweizer Gemeinden von ihren \
                 Websites, ordnet sie automatisch nach Art, Gremium und Abschnitten ein und \
                 verlinkt sie auf die Originale. Die Einträge sind offene Daten (CC0).",
    privacy: "Datenschutz",
    privacy_text: "Keine Cookies, nichts von Dritten; die Nutzungsstatistik zählt Aufrufe ohne \
                   IP-Adressen und Suchbegriffe. Das Archiv nennt keine Privatpersonen: Dokumente \
                   tragen feste Bezeichnungen, Einbürgerungsunterlagen werden nicht aufgeführt.",
    search: "Suche",
    cantons: "Kantone",
    communes: "Gemeinden",
    canton_line: "{name}: {communes} Gemeinden mit Dokumenten im offenen Archiv Tractanda.",
    canton_title: "{name}: Protokolle, Traktanden und Budgets der Gemeinden",
    canton_description: "{name}: Protokolle, Traktandenlisten, Budgets und Rechnungen aus \
                         {communes} Gemeinden, im Volltext durchsuchbar, mit Links zu den \
                         Originalen.",
    api: "API und MCP-Server",
    api_text: "Das Archiv ohne Schlüssel abfragen: eine JSON-API und ein MCP-Server für \
               KI-Assistenten, mit Volltextsuche in den Seiten der Dokumente.",
    licences: "Lizenzen und Dank",
    licences_text: "Die Lizenzen von Code (MIT) und Daten (CC0 1.0) und die offene Software, die \
                    Modelle und die Daten, auf denen Tractanda aufbaut.",
};

const FR: Words = Words {
    site: "Archive ouverte des documents des communes suisses : procès-verbaux, décisions, \
           budgets, comptes et autres documents des assemblées communales, conseils généraux et \
           conseils communaux, avec des liens vers les originaux.",
    totals: "{documents} documents de {communes} communes, liés aux originaux. Ils sont \
             classés automatiquement par titre, organe et sections ; personne n’a encore \
             vérifié ce classement.",
    with_documents: "Communes avec des documents",
    commune_line: "{name} ({canton}) : {documents} documents dans l’archive ouverte Tractanda.",
    commune_title: "{name} : procès-verbaux, ordres du jour et budgets de la commune",
    commune_description: "{name} ({canton}) : procès-verbaux, ordres du jour, budgets et \
                          comptes{years}, {documents} documents, recherche en texte intégral et liens \
                          vers les originaux.",
    by_year: "Documents par année",
    no_year: "sans année",
    untitled: "Document sans titre",
    recent: "Documents récents",
    website: "Site de la commune",
    about: "À propos de Tractanda",
    about_text: "Tractanda rassemble les documents publics des communes suisses sur leurs \
                 sites, les classe automatiquement par type, organe et sections et renvoie aux \
                 originaux. Les entrées sont des données ouvertes (CC0).",
    privacy: "Protection des données",
    privacy_text: "Pas de cookies, rien de tiers ; les statistiques comptent les accès sans \
                   adresses IP ni termes de recherche. L’archive ne nomme aucune personne privée : \
                   les documents portent des intitulés fixes et les documents de naturalisation ne \
                   sont pas répertoriés.",
    search: "Recherche",
    cantons: "Cantons",
    communes: "Communes",
    canton_line: "{name} : {communes} communes avec des documents dans l’archive ouverte \
                  Tractanda.",
    canton_title: "{name} : procès-verbaux, ordres du jour et budgets des communes",
    canton_description: "{name} : procès-verbaux, ordres du jour, budgets et comptes de \
                         {communes} communes, recherche en texte intégral et liens vers les \
                         originaux.",
    api: "API et serveur MCP",
    api_text: "Interroger l’archive sans clé : une API JSON et un serveur MCP pour les \
               assistants d’IA, avec une recherche plein texte dans les pages des documents.",
    licences: "Licences et remerciements",
    licences_text: "Les licences du code (MIT) et des données (CC0 1.0), et les logiciels, \
                    modèles et données ouverts sur lesquels Tractanda s’appuie.",
};

const IT: Words = Words {
    site: "Archivio aperto dei documenti dei comuni svizzeri: verbali, decisioni, preventivi, \
           consuntivi e altri documenti delle assemblee comunali, dei consigli comunali e dei \
           municipi, con link agli originali.",
    totals: "{documents} documenti da {communes} comuni, con link agli originali. Sono \
             classificati automaticamente per titolo, organo e sezioni; nessuno ha ancora \
             verificato questa classificazione.",
    with_documents: "Comuni con documenti",
    commune_line: "{name} ({canton}): {documents} documenti nell’archivio aperto Tractanda.",
    commune_title: "{name}: verbali, ordini del giorno e preventivi del comune",
    commune_description: "{name} ({canton}): verbali, ordini del giorno, preventivi e \
                          consuntivi{years}, {documents} documenti, ricerca a testo integrale e link \
                          agli originali.",
    by_year: "Documenti per anno",
    no_year: "senza anno",
    untitled: "Documento senza titolo",
    recent: "Documenti recenti",
    website: "Sito del comune",
    about: "Informazioni su Tractanda",
    about_text: "Tractanda raccoglie i documenti pubblici dei comuni svizzeri dai loro siti, li \
                 classifica automaticamente per tipo, organo e sezioni e rimanda agli originali. \
                 Le voci sono dati aperti (CC0).",
    privacy: "Protezione dei dati",
    privacy_text: "Nessun cookie, nulla da terzi; le statistiche contano gli accessi senza \
                   indirizzi IP né termini di ricerca. L’archivio non nomina persone private: i \
                   documenti portano denominazioni fisse e i documenti di naturalizzazione non \
                   sono elencati.",
    search: "Ricerca",
    cantons: "Cantoni",
    communes: "Comuni",
    canton_line: "{name}: {communes} comuni con documenti nell’archivio aperto Tractanda.",
    canton_title: "{name}: verbali, ordini del giorno e preventivi dei comuni",
    canton_description: "{name}: verbali, ordini del giorno, preventivi e consuntivi di \
                         {communes} comuni, ricerca a testo integrale e link agli originali.",
    api: "API e server MCP",
    api_text: "Interrogare l’archivio senza chiave: un’API JSON e un server MCP per gli \
               assistenti IA, con ricerca a testo integrale nelle pagine dei documenti.",
    licences: "Licenze e ringraziamenti",
    licences_text: "Le licenze del codice (MIT) e dei dati (CC0 1.0), e il software, i modelli e \
                    i dati aperti su cui si basa Tractanda.",
};

const EN: Words = Words {
    site: "Open archive of the documents of Swiss municipalities: minutes, decisions, budgets, \
           accounts and other records of citizens' assemblies, municipal parliaments and \
           municipal councils, with links to the originals.",
    totals: "{documents} documents from {communes} municipalities, linked to the originals. They \
             are sorted automatically by title, body and sections; nobody has checked this \
             sorting yet.",
    with_documents: "Municipalities with documents",
    commune_line: "{name} ({canton}): {documents} documents in the open archive Tractanda.",
    commune_title: "{name}: minutes, agendas and budgets of the municipality",
    commune_description: "{name} ({canton}): minutes, agendas, budgets and accounts{years}, \
                          {documents} documents, full-text search and links to the originals.",
    by_year: "Documents by year",
    no_year: "no year",
    untitled: "Untitled document",
    recent: "Latest documents",
    website: "Municipality website",
    about: "About Tractanda",
    about_text: "Tractanda collects the public documents of Swiss municipalities from their \
                 websites, sorts them automatically by type, body and sections and links to the \
                 originals. The entries are open data (CC0).",
    privacy: "Privacy",
    privacy_text: "No cookies, nothing from third parties; the usage statistics count visits \
                   without IP addresses or search terms. The archive names no private persons: \
                   documents carry fixed designations, and naturalisation records are not listed.",
    search: "Search",
    cantons: "Cantons",
    communes: "Municipalities",
    canton_line: "{name}: {communes} municipalities with documents in the open archive \
                  Tractanda.",
    canton_title: "{name}: minutes, agendas and budgets of the municipalities",
    canton_description: "{name}: minutes, agendas, budgets and accounts of {communes} \
                         municipalities, full-text search and links to the originals.",
    api: "API and MCP server",
    api_text: "Query the archive without a key: a JSON API and an MCP server for AI assistants, \
               with full-text search in the pages of the documents.",
    licences: "Licences and thanks",
    licences_text: "The licences of the code (MIT) and the data (CC0 1.0), and the open software, \
                    models and data Tractanda builds on.",
};

/// The cantons in the federal order: code and name in German, French, Italian and English, as the
/// front end writes them (`frontend/src/lib/copy/commune.ts`).
#[rustfmt::skip]
const CANTONS: [(&str, [&str; 4]); 26] = [
    ("ZH", ["Kanton Zürich", "Canton de Zurich", "Cantone di Zurigo", "Canton of Zurich"]),
    ("BE", ["Kanton Bern", "Canton de Berne", "Cantone di Berna", "Canton of Bern"]),
    ("LU", ["Kanton Luzern", "Canton de Lucerne", "Cantone di Lucerna", "Canton of Lucerne"]),
    ("UR", ["Kanton Uri", "Canton d’Uri", "Cantone di Uri", "Canton of Uri"]),
    ("SZ", ["Kanton Schwyz", "Canton de Schwytz", "Cantone di Svitto", "Canton of Schwyz"]),
    ("OW", ["Kanton Obwalden", "Canton d’Obwald", "Cantone di Obvaldo", "Canton of Obwalden"]),
    ("NW", ["Kanton Nidwalden", "Canton de Nidwald", "Cantone di Nidvaldo", "Canton of Nidwalden"]),
    ("GL", ["Kanton Glarus", "Canton de Glaris", "Cantone di Glarona", "Canton of Glarus"]),
    ("ZG", ["Kanton Zug", "Canton de Zoug", "Cantone di Zugo", "Canton of Zug"]),
    ("FR", ["Kanton Freiburg", "Canton de Fribourg", "Cantone di Friburgo", "Canton of Fribourg"]),
    ("SO", ["Kanton Solothurn", "Canton de Soleure", "Cantone di Soletta", "Canton of Solothurn"]),
    ("BS", ["Kanton Basel-Stadt", "Canton de Bâle-Ville", "Cantone di Basilea Città", "Canton of Basel-Stadt"]),
    ("BL", ["Kanton Basel-Landschaft", "Canton de Bâle-Campagne", "Cantone di Basilea Campagna", "Canton of Basel-Landschaft"]),
    ("SH", ["Kanton Schaffhausen", "Canton de Schaffhouse", "Cantone di Sciaffusa", "Canton of Schaffhausen"]),
    ("AR", ["Kanton Appenzell Ausserrhoden", "Canton d’Appenzell Rhodes-Extérieures", "Cantone di Appenzello Esterno", "Canton of Appenzell Ausserrhoden"]),
    ("AI", ["Kanton Appenzell Innerrhoden", "Canton d’Appenzell Rhodes-Intérieures", "Cantone di Appenzello Interno", "Canton of Appenzell Innerrhoden"]),
    ("SG", ["Kanton St. Gallen", "Canton de Saint-Gall", "Cantone di San Gallo", "Canton of St. Gallen"]),
    ("GR", ["Kanton Graubünden", "Canton des Grisons", "Cantone dei Grigioni", "Canton of Graubünden"]),
    ("AG", ["Kanton Aargau", "Canton d’Argovie", "Cantone di Argovia", "Canton of Aargau"]),
    ("TG", ["Kanton Thurgau", "Canton de Thurgovie", "Cantone di Turgovia", "Canton of Thurgau"]),
    ("TI", ["Kanton Tessin", "Canton du Tessin", "Cantone Ticino", "Canton of Ticino"]),
    ("VD", ["Kanton Waadt", "Canton de Vaud", "Cantone di Vaud", "Canton of Vaud"]),
    ("VS", ["Kanton Wallis", "Canton du Valais", "Cantone del Vallese", "Canton of Valais"]),
    ("NE", ["Kanton Neuenburg", "Canton de Neuchâtel", "Cantone di Neuchâtel", "Canton of Neuchâtel"]),
    ("GE", ["Kanton Genf", "Canton de Genève", "Cantone di Ginevra", "Canton of Geneva"]),
    ("JU", ["Kanton Jura", "Canton du Jura", "Cantone del Giura", "Canton of Jura"]),
];

/// A canton's name in `lang` ("Kanton Zürich"); None for a code that names no canton.
pub fn canton_name(code: &str, lang: Lang) -> Option<&'static str> {
    let i = Lang::ALL.iter().position(|l| *l == lang)?;
    CANTONS
        .iter()
        .find(|(c, _)| c.eq_ignore_ascii_case(code))
        .map(|(_, names)| names[i])
}

/// The path of a canton's page after the language: `/kantone/zh`.
pub fn canton_path(code: &str) -> String {
    format!("/kantone/{}", code.to_ascii_lowercase())
}

/// One page as the server sends it: what goes into `<head>` and the fact sheet.
pub struct Page {
    pub lang: Lang,
    /// The path after the language, `""` for the start page, e.g. `/gemeinden/261`.
    pub path: String,
    pub title: String,
    pub description: String,
    /// schema.org JSON-LD, when the page has some.
    pub json_ld: Option<serde_json::Value>,
    /// The fact sheet's HTML, already escaped.
    pub facts: String,
    /// Search result pages are never indexed.
    pub noindex: bool,
}

/// The page shell when the front end is not built: the pages carry only their facts.
const NO_SHELL: &str = "<!doctype html><html lang=\"de\"><head></head><body></body></html>";

/// What the page renderer needs: the public address, whether search engines may index the site
/// and its commune pages, and where the front end's `index.html` is.
#[derive(Clone)]
pub struct Site {
    pub public_url: String,
    pub indexing: bool,
    pub index_communes: bool,
    shell: Arc<Path>,
    /// The host of `public_url`, lower case.
    host: String,
    /// Former names of the site, lower case (`with_former_hosts`).
    former_hosts: Arc<[String]>,
}

impl Site {
    pub fn new(public_url: &str, indexing: bool, index_communes: bool, static_dir: &Path) -> Self {
        Site {
            public_url: public_url.trim_end_matches('/').to_string(),
            indexing,
            index_communes,
            shell: static_dir.join("index.html").into(),
            host: host_of(public_url),
            former_hosts: Arc::from([]),
        }
    }

    /// Other names of the site (`FORMER_HOSTS`): requests to them are sent on to `public_url`
    /// (`former_hosts`).
    pub fn with_former_hosts(mut self, hosts: &[String]) -> Self {
        self.former_hosts = hosts.iter().map(|h| h.to_ascii_lowercase()).collect();
        self
    }

    /// Whether `host` (lower case, without port) names this site, now or formerly.
    pub fn is_own_host(&self, host: &str) -> bool {
        host == self.host || self.former_hosts.iter().any(|h| h == host)
    }

    /// The front end's `index.html`, read for every page: it names the build's script files, so a
    /// newly deployed front end must be picked up without a restart.
    pub async fn shell(&self) -> String {
        tokio::fs::read_to_string(&*self.shell)
            .await
            .unwrap_or_else(|_| NO_SHELL.to_string())
    }

    pub fn url(&self, lang: Lang, path: &str) -> String {
        format!("{}/{}{}", self.public_url, lang.code(), path)
    }

    /// `shell` with the page's head tags and fact sheet.
    pub fn render(&self, shell: &str, page: &Page) -> String {
        let url = self.url(page.lang, &page.path);
        let mut head = format!(
            "<title>{}</title>\n<meta name=\"description\" content=\"{}\">\n\
             <link rel=\"canonical\" href=\"{}\">\n",
            escape(&page.title),
            escape(&page.description),
            escape(&url),
        );
        // link previews (Open Graph), in the page's language
        head.push_str(&format!(
            "<meta property=\"og:type\" content=\"website\">\n\
             <meta property=\"og:site_name\" content=\"Tractanda\">\n\
             <meta property=\"og:title\" content=\"{}\">\n\
             <meta property=\"og:description\" content=\"{}\">\n\
             <meta property=\"og:url\" content=\"{}\">\n\
             <meta property=\"og:image\" content=\"{}/og/tractanda-{}.png\">\n\
             <meta property=\"og:image:width\" content=\"1200\">\n\
             <meta property=\"og:image:height\" content=\"630\">\n\
             <meta property=\"og:locale\" content=\"{}\">\n\
             <meta name=\"twitter:card\" content=\"summary_large_image\">\n",
            escape(&page.title),
            escape(&page.description),
            escape(&url),
            escape(&self.public_url),
            page.lang.code(),
            page.lang.locale(),
        ));
        for lang in Lang::ALL.into_iter().filter(|l| *l != page.lang) {
            head.push_str(&format!(
                "<meta property=\"og:locale:alternate\" content=\"{}\">\n",
                lang.locale()
            ));
        }
        for lang in Lang::ALL {
            head.push_str(&format!(
                "<link rel=\"alternate\" hreflang=\"{}\" href=\"{}\">\n",
                lang.code(),
                escape(&self.url(lang, &page.path))
            ));
        }
        head.push_str(&format!(
            "<link rel=\"alternate\" hreflang=\"x-default\" href=\"{}\">\n",
            escape(&self.url(Lang::De, &page.path))
        ));
        if page.noindex {
            head.push_str("<meta name=\"robots\" content=\"noindex\">\n");
        }
        if let Some(data) = &page.json_ld {
            // `</` would end the script element early
            let json = data.to_string().replace("</", "<\\/");
            head.push_str(&format!(
                "<script type=\"application/ld+json\">{json}</script>\n"
            ));
        }
        head.push_str(
            "<script>document.documentElement.classList.add('js')</script>\n\
             <style>.js #oc-facts{display:none}</style>\n",
        );
        let body = format!("<main id=\"oc-facts\">{}</main>", page.facts);
        let html = shell.replacen(
            "<html lang=\"de\">",
            &format!("<html lang=\"{}\">", page.lang.code()),
            1,
        );
        inject(&html, &head, &body)
    }
}

/// Puts `head` before `</head>` and `body` right after the opening `<body>` tag.
fn inject(html: &str, head: &str, body: &str) -> String {
    let mut out = String::with_capacity(html.len() + head.len() + body.len());
    let (before_head, after_head) = match html.find("</head>") {
        Some(i) => html.split_at(i),
        None => ("", html),
    };
    out.push_str(before_head);
    out.push_str(head);
    match after_head
        .find("<body")
        .and_then(|i| after_head[i..].find('>').map(|j| i + j + 1))
    {
        Some(i) => {
            out.push_str(&after_head[..i]);
            out.push_str(body);
            out.push_str(&after_head[i..]);
        }
        None => {
            out.push_str(after_head);
            out.push_str(body);
        }
    }
    out
}

/// Text for HTML element content and attribute values.
pub fn escape(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#39;"),
            _ => out.push(c),
        }
    }
    out
}

/// `YYYY-MM-DD` as `DD.MM.YYYY`; anything else as it is.
pub fn date(iso: &str) -> String {
    match (iso.get(0..4), iso.get(5..7), iso.get(8..10)) {
        (Some(y), Some(m), Some(d)) if iso.len() >= 10 => format!("{d}.{m}.{y}"),
        _ => iso.to_string(),
    }
}

/// The host of an address such as `https://www.example.ch/`, lower case, without port.
fn host_of(url: &str) -> String {
    let rest = url.split_once("://").map_or(url, |(_, rest)| rest);
    rest.split(['/', ':'])
        .next()
        .unwrap_or("")
        .to_ascii_lowercase()
}

/// A page's path without a language, as old links name them (`/gemeinden/261`): the same page in
/// German. None for any other path: the start page (`root` chooses its language), the API, files.
pub fn in_german(path: &str) -> Option<String> {
    let page = ["/ueber-uns", "/datenschutz", "/suche", "/api", "/lizenzen"].contains(&path)
        || ["/gemeinden/", "/kantone/"]
            .iter()
            .any(|p| path.starts_with(p));
    page.then(|| format!("/de{path}"))
}

/// Middleware: one permanent redirect (308, so the method stays) to the address a page has now. A
/// request to a former name of the site goes to the same path under `public_url`, and a page path
/// without a language to the German page, both at once when both apply. A request to a former
/// name is counted under that name, so the usage statistics show how many still arrive through
/// old links.
pub async fn redirects(State(state): State<AppState>, request: Request, next: Next) -> Response {
    let host = request
        .headers()
        .get(header::HOST)
        .and_then(|v| v.to_str().ok())
        .or_else(|| request.uri().host())
        .map(|h| h.split(':').next().unwrap_or(h).to_ascii_lowercase());
    let former = host.filter(|h| state.site.former_hosts.iter().any(|f| f == h));
    let german = in_german(request.uri().path());
    if former.is_none() && german.is_none() {
        return next.run(request).await;
    }
    let path = german.as_deref().unwrap_or(request.uri().path());
    let query = request
        .uri()
        .query()
        .map(|q| format!("?{q}"))
        .unwrap_or_default();
    let Some(host) = former else {
        return Redirect::permanent(&format!("{path}{query}")).into_response();
    };
    if let Some(usage) = &state.usage {
        let user_agent = request
            .headers()
            .get(header::USER_AGENT)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("");
        usage.record(Hit {
            channel: "redirect",
            route: host,
            bot: bot_name(user_agent),
            status: 308,
            duration_ms: 0.0,
            search_total: None,
            visitor: None,
            referrer: None,
            commune: None,
        });
    }
    Redirect::permanent(&format!("{}{path}{query}", state.site.public_url)).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn numbers_dates_and_escaping() {
        assert_eq!(Lang::De.number(360723), "360’723");
        assert_eq!(Lang::Fr.number(1000), "1\u{202f}000");
        assert_eq!(Lang::It.number(999), "999");
        assert_eq!(date("2026-03-14"), "14.03.2026");
        assert_eq!(
            escape("<a href=\"x\">&'"),
            "&lt;a href=&quot;x&quot;&gt;&amp;&#39;"
        );
    }

    #[test]
    fn pages_without_a_language_and_canton_names() {
        assert_eq!(
            in_german("/gemeinden/261").as_deref(),
            Some("/de/gemeinden/261")
        );
        assert_eq!(in_german("/kantone/zh").as_deref(), Some("/de/kantone/zh"));
        assert_eq!(in_german("/ueber-uns").as_deref(), Some("/de/ueber-uns"));
        assert_eq!(in_german("/api").as_deref(), Some("/de/api"));
        for other in [
            "/",
            "/de/gemeinden/261",
            "/api/documents",
            "/gemeinden",
            "/favicon.svg",
        ] {
            assert_eq!(in_german(other), None, "{other}");
        }
        assert_eq!(canton_name("ZH", Lang::Fr), Some("Canton de Zurich"));
        assert_eq!(canton_name("ti", Lang::En), Some("Canton of Ticino"));
        assert_eq!(canton_name("XX", Lang::De), None);
        assert_eq!(canton_path("ZH"), "/kantone/zh");
    }

    #[test]
    fn head_and_facts_go_into_the_shell() {
        let html = inject(
            "<html lang=\"de\"><head><meta></head><body data-x=\"1\"><div></div></body></html>",
            "<title>t</title>",
            "<main>f</main>",
        );
        assert_eq!(
            html,
            "<html lang=\"de\"><head><meta><title>t</title></head><body data-x=\"1\"><main>f</main><div></div></body></html>"
        );
    }
}
