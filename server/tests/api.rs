//! The whole server against a small database created here from the serving schema
//! (`src/opencommune/serving.sql`) and the rows below.

use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use axum::Router;
use axum::body::{Body, to_bytes};
use axum::extract::ConnectInfo;
use axum::http::{Request, StatusCode, header};
use opencommune_server::site::Site;
use opencommune_server::usage::Usage;
use opencommune_server::{AppState, app, db};
use serde_json::{Value, json};
use sqlx::sqlite::{SqliteConnectOptions, SqlitePool};
use tower::ServiceExt;

const SERVING: &str = include_str!("../../src/opencommune/serving.sql");
/// The full-text index's schema, from the Python module that builds the index.
const FULLTEXT_PY: &str = include_str!("../../src/opencommune/fulltext.py");

fn fulltext_schema() -> &'static str {
    FULLTEXT_PY
        .split("SCHEMA = \"\"\"")
        .nth(1)
        .and_then(|rest| rest.split("\"\"\"").next())
        .expect("SCHEMA in fulltext.py")
}

/// Five classified documents: one linked by two communes, one without a title or a URL, one with
/// a URL that is not http(s). Aarau has no document and no register entry; Schwaderloch and Aeugst
/// are only in the register.
const ROWS: &str = r#"
INSERT INTO communes VALUES
  (261, 'Zürich', 'ZH', 'de', 'https://www.stadt-zuerich.ch', 'custom', NULL, 47.37, 8.54),
  (5938, 'Yverdon-les-Bains', 'VD', 'fr', 'https://www.yverdon.ch', NULL, NULL, 46.78, 6.64),
  (4001, 'Aarau', 'AG', NULL, NULL, NULL, NULL, NULL, NULL);
INSERT INTO runs VALUES
  ('classify-aaaa', 'classification', 'model-a', '{"dpi": 75, "max_pages": 150}',
   'Pages are seen at 75 dpi; of documents over 150 pages the model sees 150.', 5);
INSERT INTO documents (sha256, title, body_name, first_seen, listing_date, page_count, run_id,
  kind, body_kind, date, years, year, languages, personal_data, pages_seen) VALUES
  ('aaaa', 'Protokoll Gemeindeversammlung 2024', 'Gemeindeversammlung Zürich', '2026-09-06',
   '2024-06-20', 12, 'classify-aaaa', 'meeting_booklet', 'citizens_assembly', '2024-06-12', '2024',
   2024, 'de', 0, 12),
  ('bbbb', 'Procès-verbal 2025', 'Conseil communal', '2026-09-06', NULL, 30, 'classify-aaaa',
   'meeting_booklet', 'parliament', '2025-09-03', '2025,2026', 2025, 'fr,de', 1, 30),
  ('cccc', 'Budget 2024', 'Stadtrat', '2026-09-07', '2023-11-01', 200, 'classify-aaaa',
   'standalone', 'executive', NULL, '2024', 2023, 'de', 0, 150),
  ('dddd', NULL, NULL, '2026-09-08', NULL, 3, 'classify-aaaa', NULL, NULL, NULL, '', NULL, '', 0, 3),
  ('eeee', 'Mitteilungsblatt 100% Mai', NULL, '2026-09-08', '2024-05-01', 4, 'classify-aaaa', NULL,
   NULL, NULL, '', 2024, 'de', 0, 4);
INSERT INTO document_communes VALUES
  ('aaaa', 261, 'https://www.stadt-zuerich.ch/p.pdf'),
  ('bbbb', 5938, 'https://www.yverdon.ch/pv.pdf'),
  ('cccc', 261, 'https://www.stadt-zuerich.ch/Budget Mai 2024.pdf'),
  ('cccc', 5938, 'https://www.yverdon.ch/budget.pdf'),
  ('dddd', 261, NULL),
  ('eeee', 261, 'ftp://old.example/x.pdf');
INSERT INTO sections VALUES
  ('aaaa', 1, 'invitation_agenda', 'proposals', 1, 1, 0, 1),
  ('aaaa', 2, 'minutes', 'deliberation', 2, 11, 0, 10),
  ('aaaa', 3, 'vote_result', 'deliberation', 12, 12, 0, 1),
  ('bbbb', 1, 'minutes', 'deliberation', 1, 28, 0, 28),
  ('bbbb', 2, 'minutes', 'deliberation', 29, 30, 0, 2),
  ('cccc', 1, 'budget', 'money', 1, 200, 1, 200);
INSERT INTO register VALUES
  (261, 'Zürich', 'ZH', 'configured', 'https://www.stadt-zuerich.ch', 'custom', 1, '2026-09-30',
   'reviewer', NULL, NULL),
  (4176, 'Schwaderloch', 'AG', 'not_examined', 'http://www.schwaderloch.ch', NULL, NULL, NULL, NULL,
   NULL, NULL),
  (1, 'Aeugst am Albis', 'ZH', 'blocked_robots', 'https://www.aeugst-albis.ch', 'i-web', 0,
   '2026-10-01', 'reviewer', 'robots.txt disallows the archive', 'robots.txt disallows the archive');
INSERT INTO register_publications VALUES
  (261, 0, 'parliament', 'minutes', 'configured', 'https://www.gemeinderat-zuerich.ch/protokolle',
   '2018', NULL, '50 per year', NULL, NULL),
  (261, 1, 'executive', 'decisions', 'not_published', NULL, NULL, NULL, NULL, 'not public', NULL);
INSERT INTO access_observations VALUES
  (261, 'https://archiv.stadt-zuerich.ch/', 'rate_limited',
   strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now', '-10 days'), 'https://archiv.stadt-zuerich.ch/robots.txt', NULL),
  (261, 'https://www.stadt-zuerich.ch/', 'restricted',
   strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now', '-1 day'), 'https://www.stadt-zuerich.ch/robots.txt',
   strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now', '-1 day')),
  (4176, 'http://www.schwaderloch.ch/', 'technical_error',
   strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now', '-2 days'), 'http://www.schwaderloch.ch/robots.txt', NULL);
"#;

/// The build fills the title index once the documents are written.
const TITLES: &str = "INSERT INTO documents_fts (documents_fts) VALUES ('rebuild')";

/// Page text of aaaa (the agenda page, a minutes page and the vote result, read by OCR), bbbb (two
/// French minutes pages, one with a typographic apostrophe) and cccc (two budget pages). The
/// full-text fixture also makes eeee a bulletin, which the search leaves out.
const PAGES: &str = r#"
INSERT INTO pages VALUES
  (1, 'aaaa', 1, 'layer', 'layer-1'),
  (2, 'aaaa', 2, 'layer', 'layer-1'),
  (3, 'aaaa', 12, 'ocr', 'ocr-1'),
  (4, 'bbbb', 3, 'layer', 'layer-1'),
  (5, 'bbbb', 29, 'layer', 'layer-1'),
  (6, 'cccc', 5, 'layer', 'layer-1'),
  (7, 'cccc', 6, 'layer', 'layer-1');
INSERT INTO page_fts (rowid, text) VALUES
  (1, 'Einladung zur Gemeindeversammlung vom 12. Juni 2024. Traktanden: 1. Budget 2025 mit
Steuerfuss 119 %. 2. Investitionskredit für das Schulhaus.'),
  (2, 'Protokoll. Der Gemeinderat beantragt einen Investitionskredit von Fr. 2''980''000 für das
Schulhaus. Die Versammlung stimmt dem Investitionskredit zu.'),
  (3, 'Abstimmungsresultat: Die Einführung von Tempo 30 auf der Dorfstrasse wird angenommen.'),
  (4, 'Le Conseil communal accorde un crédit d''engagement de 250''000 francs pour l''école.'),
  (5, 'Crédit d’engagement pour la route: le Conseil accepte. Ecole et crèche. <b>fin</b>'),
  (6, 'Budget 2024 der Stadt. Der Steuerfuss bleibt bei 119 %. Tempo-30-Zonen im Quartier.'),
  (7, 'Erfolgsrechnung. Gemeindesteuern und Beiträge.');
INSERT INTO build VALUES ('section_types', '{"minutes": {"pages": 1, "indexed": 1},
  "decisions": {"pages": 0, "indexed": 0}, "vote_result": {"pages": 1, "indexed": 1},
  "budget": {"pages": 1, "indexed": 1}, "accounts": {"pages": 0, "indexed": 0},
  "proposal": {"pages": 0, "indexed": 0}, "invitation_agenda": {"pages": 1, "indexed": 1},
  "member_request": {"pages": 0, "indexed": 0}}');
"#;

fn temp_path(name: &str) -> PathBuf {
    static N: AtomicUsize = AtomicUsize::new(0);
    let n = N.fetch_add(1, Ordering::Relaxed);
    std::env::temp_dir().join(format!(
        "opencommune-server-test-{}-{n}-{name}",
        std::process::id()
    ))
}

async fn create(path: &Path, sql: &[&'static str]) {
    let options = SqliteConnectOptions::new()
        .filename(path)
        .create_if_missing(true);
    let pool = SqlitePool::connect_with(options).await.unwrap();
    for s in sql {
        sqlx::raw_sql(*s).execute(&pool).await.unwrap();
    }
    pool.close().await;
}

struct Fixture {
    router: Router,
    usage_path: PathBuf,
    static_dir: PathBuf,
    files: Vec<PathBuf>,
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.static_dir);
        for f in &self.files {
            for suffix in ["", "-wal", "-shm", "-journal"] {
                let _ = std::fs::remove_file(format!("{}{suffix}", f.display()));
            }
        }
    }
}

async fn fixture(with_usage: bool) -> Fixture {
    build(with_usage, false, false, false).await
}

/// The fixture with a full-text index attached.
async fn fulltext_fixture() -> Fixture {
    build(false, false, false, true).await
}

/// The token of the operator's usage report in the fixture.
const USAGE_TOKEN: &str = "usage-token-0123456789abcdef0123456789";

/// The front end's page shell as the build writes it, reduced to what the server touches.
const SHELL: &str = "<!doctype html><html lang=\"de\"><head><meta charset=\"utf-8\"></head>\
<body data-sveltekit-preload-data=\"hover\"><div style=\"display: contents\"></div></body></html>";

async fn build(with_usage: bool, indexing: bool, index_communes: bool, fulltext: bool) -> Fixture {
    let database = temp_path("serving.db");
    create(&database, &[SERVING, ROWS, TITLES]).await;
    let index = temp_path("fulltext.db");
    if fulltext {
        create(
            &database,
            &["UPDATE documents SET kind = 'bulletin' WHERE sha256 = 'eeee'"],
        )
        .await;
        create(&index, &[fulltext_schema(), PAGES]).await;
        db::check_fulltext(&index).await.unwrap();
    }
    let usage_path = temp_path("usage.sqlite3");
    let usage = if with_usage {
        Some(
            Usage::open(&usage_path, true)
                .await
                .unwrap()
                .with_token(Some(USAGE_TOKEN)),
        )
    } else {
        None
    };
    let static_dir = temp_path("static");
    std::fs::create_dir_all(static_dir.join("_app/immutable")).unwrap();
    std::fs::write(static_dir.join("index.html"), SHELL).unwrap();
    std::fs::write(static_dir.join("_app/immutable/app.js"), "console.log(1)").unwrap();
    let pool = db::open_read_only(&database, fulltext.then_some(index.as_path()))
        .await
        .unwrap();
    db::check_tables(&pool).await.unwrap();
    let state = AppState {
        db: pool,
        fulltext,
        usage,
        site: Site::new(
            "https://example.test",
            indexing,
            index_communes,
            &static_dir,
        )
        .with_former_hosts(&["old.example.test".to_string()]),
        ask: None,
    };
    let router = app(state, &static_dir, &["localhost".to_string()]);
    Fixture {
        files: vec![database, index, usage_path.clone()],
        router,
        usage_path,
        static_dir,
    }
}

async fn send(router: &Router, request: Request<Body>) -> (StatusCode, header::HeaderMap, Vec<u8>) {
    let response = router.clone().oneshot(request).await.unwrap();
    let status = response.status();
    let headers = response.headers().clone();
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    (status, headers, body.to_vec())
}

async fn get(router: &Router, uri: &str) -> (StatusCode, Value) {
    let request = Request::get(uri).body(Body::empty()).unwrap();
    let (status, _, body) = send(router, request).await;
    (status, serde_json::from_slice(&body).unwrap_or(Value::Null))
}

fn keys(v: &Value) -> Vec<&str> {
    v.as_object().unwrap().keys().map(String::as_str).collect()
}

fn shas(page: &Value) -> Vec<&str> {
    page["items"]
        .as_array()
        .unwrap()
        .iter()
        .map(|d| d["sha256"].as_str().unwrap())
        .collect()
}

#[tokio::test]
async fn a_database_without_the_serving_tables_is_refused() {
    let path = temp_path("other.db");
    create(
        &path,
        &["CREATE TABLE communes (bfs_id INTEGER PRIMARY KEY)"],
    )
    .await;
    let pool = db::open_read_only(&path, None).await.unwrap();
    assert!(db::check_tables(&pool).await.is_err());
    pool.close().await;
    assert!(db::check_fulltext(&path).await.is_err()); // nor is it a full-text index
    let _ = std::fs::remove_file(&path);
}

#[tokio::test]
async fn communes_carry_their_documents_and_register_status() {
    let f = fixture(false).await;
    let (status, list) = get(&f.router, "/api/communes").await;
    assert_eq!(status, StatusCode::OK);
    let ids: Vec<i64> = list
        .as_array()
        .unwrap()
        .iter()
        .map(|c| c["bfs_id"].as_i64().unwrap())
        .collect();
    assert_eq!(ids, [4001, 5938, 261]); // by canton, then name; register-only communes are not listed
    let zh = &list[2];
    assert_eq!(
        keys(zh),
        [
            "bfs_id",
            "name",
            "canton",
            "language",
            "website",
            "lat",
            "lon",
            "documents",
            "register_status",
            "register_note"
        ]
    );
    assert_eq!(zh["documents"], 4);
    assert_eq!(zh["register_status"], "configured");
    assert_eq!(list[1]["documents"], 2); // a document linked by two communes counts for each
    assert_eq!(list[0]["documents"], 0);
    assert_eq!(list[0]["language"], Value::Null);
    assert_eq!(list[0]["register_status"], Value::Null);
}

#[tokio::test]
async fn a_commune_has_its_register_entry_and_access_observations() {
    let f = fixture(false).await;
    let (status, zh) = get(&f.router, "/api/communes/261").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        keys(&zh),
        [
            "bfs_id",
            "name",
            "canton",
            "language",
            "website",
            "vendor",
            "lat",
            "lon",
            "documents",
            "register",
            "access",
            "holdings"
        ]
    );
    assert_eq!(zh["vendor"], "custom");
    // per year and family, newest year first; a document without a year is not counted. aaaa
    // opens with its agenda, so it counts as a proposals document, and its pages split by section
    assert_eq!(
        zh["holdings"],
        json!([
            {"year": 2024, "family": "deliberation", "documents": 1, "pages": 11, "first_part_documents": 0},
            {"year": 2024, "family": "proposals", "documents": 1, "pages": 1, "first_part_documents": 1},
            {"year": 2023, "family": "money", "documents": 1, "pages": 200, "first_part_documents": 1}
        ])
    );
    assert_eq!(zh["documents"], 4);
    assert_eq!(zh["register"]["status"], "configured");
    assert_eq!(zh["register"]["publications"][1]["kind"], "decisions");
    // newest check first; a restriction or check a week old or more is stale
    assert_eq!(
        zh["access"][0]["source_url"],
        "https://www.stadt-zuerich.ch/"
    );
    assert_eq!(zh["access"][0]["stale"], false);
    assert_eq!(zh["access"][1]["stale"], true);
    let (_, vd) = get(&f.router, "/api/communes/5938").await;
    assert_eq!(vd["register"], Value::Null);
    assert_eq!(vd["access"], json!([]));
    for unknown in ["/api/communes/4176", "/api/communes/999"] {
        let (status, body) = get(&f.router, unknown).await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{unknown}");
        assert_eq!(body["detail"], "commune not found");
    }
    let (status, _) = get(&f.router, "/api/communes/abc").await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn the_register_lists_every_entry() {
    let f = fixture(false).await;
    let (_, register) = get(&f.router, "/api/register").await;
    let ids: Vec<i64> = register
        .as_array()
        .unwrap()
        .iter()
        .map(|e| e["bfs_id"].as_i64().unwrap())
        .collect();
    assert_eq!(ids, [4176, 1, 261]); // by canton, then BFS id
    let zh = &register[2];
    assert_eq!(zh["publications"][0]["from"], "2018");
    assert_eq!(zh["publications"][1]["kind"], "decisions");
    assert_eq!(zh["reachable"], true);
    assert_eq!(zh["checked"], "2026-09-30");
    assert_eq!(register[1]["reachable"], false);
}

#[tokio::test]
async fn documents_are_listed_newest_first_with_facets() {
    let f = fixture(false).await;
    let (status, page) = get(&f.router, "/api/documents").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(keys(&page), ["total", "offset", "limit", "facets", "items"]);
    assert_eq!(
        (&page["total"], &page["offset"], &page["limit"]),
        (&json!(5), &json!(0), &json!(20))
    );
    // year, then date or listing date, newest first; no year last
    assert_eq!(shas(&page), ["bbbb", "aaaa", "eeee", "cccc", "dddd"]);
    assert_eq!(
        page["facets"],
        json!({
            "years": [
                {"value": 2025, "count": 1},
                {"value": 2024, "count": 2},
                {"value": 2023, "count": 1},
                {"value": null, "count": 1}
            ],
            "sections": [
                {"value": "budget", "count": 1},
                {"value": "invitation_agenda", "count": 1},
                {"value": "minutes", "count": 2},
                {"value": "vote_result", "count": 1}
            ],
            "bodies": [
                {"value": "citizens_assembly", "count": 1},
                {"value": "executive", "count": 1},
                {"value": "parliament", "count": 1},
                {"value": null, "count": 2}
            ]
        })
    );
    let (_, page) = get(&f.router, "/api/documents?limit=2&offset=1").await;
    assert_eq!(shas(&page), ["aaaa", "eeee"]);
    assert_eq!(page["total"], 5);
    let (_, page) = get(&f.router, "/api/documents?limit=0").await;
    assert_eq!((page["total"].as_i64(), shas(&page).len()), (Some(5), 0));
}

#[tokio::test]
async fn each_facet_is_counted_without_its_own_filter() {
    let f = fixture(false).await;
    let (_, page) = get(&f.router, "/api/documents?section=minutes").await;
    assert_eq!(shas(&page), ["bbbb", "aaaa"]);
    assert_eq!(page["total"], 2);
    // the section facet ignores the section filter; the others apply it
    assert_eq!(page["facets"]["sections"].as_array().unwrap().len(), 4);
    assert_eq!(
        page["facets"]["years"],
        json!([{"value": 2025, "count": 1}, {"value": 2024, "count": 1}])
    );
    let (_, page) = get(&f.router, "/api/documents?commune=261&year=2024").await;
    assert_eq!(shas(&page), ["aaaa", "eeee"]);
    assert_eq!(
        page["facets"]["years"],
        json!([
            {"value": 2024, "count": 2},
            {"value": 2023, "count": 1},
            {"value": null, "count": 1}
        ])
    );
    let (_, page) = get(&f.router, "/api/documents?family=money&body=executive").await;
    assert_eq!(shas(&page), ["cccc"]);
    assert_eq!(page["facets"]["bodies"][0]["value"], "executive");
    let (_, page) = get(&f.router, "/api/documents?family=deliberation").await;
    assert_eq!(shas(&page), ["bbbb", "aaaa"]);
    // by the first section only: aaaa opens with its agenda, a proposals section
    let (_, page) = get(&f.router, "/api/documents?first_family=deliberation").await;
    assert_eq!(shas(&page), ["bbbb"]);
    let (_, page) = get(&f.router, "/api/documents?first_family=proposals").await;
    assert_eq!(shas(&page), ["aaaa"]);
    let (_, page) = get(&f.router, "/api/documents?commune=5938").await;
    assert_eq!(shas(&page), ["bbbb", "cccc"]);
    let (_, page) = get(&f.router, "/api/documents?family=&body=%20").await;
    assert_eq!(page["total"], 5); // a blank filter is no filter
}

#[tokio::test]
async fn titles_are_searched_as_plain_text() {
    let f = fixture(false).await;
    let (_, page) = get(&f.router, "/api/documents?q=PROTOKOLL").await;
    assert_eq!(shas(&page), ["aaaa"]);
    let (_, page) = get(&f.router, "/api/documents?q=%25").await;
    assert_eq!(shas(&page), ["eeee"]); // % is a character, not a wildcard
    let (_, page) = get(&f.router, "/api/documents?q=_").await;
    assert_eq!(page["total"], 0);
    // from three characters on the title index answers: accents and case do not matter
    let (_, page) = get(&f.router, "/api/documents?q=proces").await;
    assert_eq!(shas(&page), ["bbbb"]);
    let (_, page) = get(&f.router, "/api/documents?q=VERSAMMLUNG").await;
    assert_eq!(shas(&page), ["aaaa"]);
    let (_, page) = get(&f.router, "/api/documents?q=100%25").await;
    assert_eq!(shas(&page), ["eeee"]);
    let (_, page) = get(&f.router, "/api/documents?q=bud").await;
    assert_eq!(shas(&page), ["cccc"]);
    let (_, page) = get(&f.router, "/api/documents?q=Pr").await;
    assert_eq!(shas(&page), ["bbbb", "aaaa"]); // shorter: LIKE, as before
    for q in ["%22Budget", "Budget%20%22x", "title:x", "a%20OR%20b"] {
        let (status, page) = get(&f.router, &format!("/api/documents?q={q}")).await;
        assert_eq!((status, &page["total"]), (StatusCode::OK, &json!(0)), "{q}");
    }
    // the facets apply the title filter too
    let (_, page) = get(&f.router, "/api/documents?q=protokoll").await;
    assert_eq!(
        page["facets"]["years"],
        json!([{"value": 2024, "count": 1}])
    );
}

#[tokio::test]
async fn bad_pages_are_refused() {
    let f = fixture(false).await;
    for uri in [
        "/api/documents?limit=101",
        "/api/documents?limit=-1",
        "/api/documents?offset=-1",
        "/api/documents?year=abc",
    ] {
        let (status, body) = get(&f.router, uri).await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{uri}");
        assert!(body["detail"].is_string(), "{uri}");
    }
    let (status, page) = get(&f.router, "/api/documents?limit=100").await;
    assert_eq!(
        (status, page["limit"].as_i64()),
        (StatusCode::OK, Some(100))
    );
}

#[tokio::test]
async fn a_document_has_its_sections_communes_and_run() {
    let f = fixture(false).await;
    let (status, d) = get(&f.router, "/api/documents/bbbb").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        d,
        json!({
            "sha256": "bbbb",
            "title": "Procès-verbal 2025",
            "body_name": "Conseil communal",
            "first_seen": "2026-09-06",
            "listing_date": null,
            "date": "2025-09-03",
            "year": 2025,
            "years": [2025, 2026],
            "page_count": 30,
            "pages_seen": 30,
            "run_id": "classify-aaaa",
            "body_kind": "parliament",
            "languages": ["fr", "de"],
            "personal_data": true,
            "sections": [
                {"type": "minutes", "family": "deliberation", "first_page": 1, "last_page": 28, "partly_unseen": false},
                {"type": "minutes", "family": "deliberation", "first_page": 29, "last_page": 30, "partly_unseen": false}
            ],
            "communes": [{"bfs_id": 5938, "name": "Yverdon-les-Bains", "canton": "VD"}],
            "original_url": "https://example.test/original/bbbb",
            "run": {
                "id": "classify-aaaa",
                "stage": "classification",
                "model": "model-a",
                "note": "Pages are seen at 75 dpi; of documents over 150 pages the model sees 150."
            }
        })
    );
    let (_, d) = get(&f.router, "/api/documents/cccc").await;
    let names: Vec<&Value> = d["communes"]
        .as_array()
        .unwrap()
        .iter()
        .map(|c| &c["name"])
        .collect();
    assert_eq!(names, [&json!("Yverdon-les-Bains"), &json!("Zürich")]); // by name
    assert_eq!(d["sections"][0]["partly_unseen"], true);
    let (_, d) = get(&f.router, "/api/documents/dddd").await;
    assert_eq!(
        (
            &d["title"],
            &d["run"]["id"],
            &d["years"],
            &d["languages"],
            &d["personal_data"],
            &d["sections"]
        ),
        (
            &Value::Null,
            &json!("classify-aaaa"),
            &json!([]),
            &json!([]),
            &json!(false),
            &json!([])
        )
    );
    let (status, body) = get(&f.router, "/api/documents/nope").await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body, json!({"detail": "document not found"}));
    // lists never carry a commune's URL
    let (_, page) = get(&f.router, "/api/documents?limit=100").await;
    let text = serde_json::to_string(&page).unwrap();
    assert!(!text.contains("stadt-zuerich.ch/") && !text.contains("yverdon.ch/"));
}

#[tokio::test]
async fn runs_carry_their_settings_as_json() {
    let f = fixture(false).await;
    let (status, runs) = get(&f.router, "/api/runs").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        runs,
        json!([{
            "id": "classify-aaaa",
            "stage": "classification",
            "model": "model-a",
            "settings": {"dpi": 75, "max_pages": 150},
            "note": "Pages are seen at 75 dpi; of documents over 150 pages the model sees 150.",
            "documents": 5
        }])
    );
}

#[tokio::test]
async fn originals_redirect_to_the_communes_url() {
    let f = fixture(false).await;
    let location = |uri: &'static str| {
        let router = f.router.clone();
        async move {
            let request = Request::get(uri).body(Body::empty()).unwrap();
            let (status, headers, _) = send(&router, request).await;
            (
                status,
                headers
                    .get(header::LOCATION)
                    .map(|l| l.to_str().unwrap().to_string()),
                headers.get("x-robots-tag").cloned(),
            )
        }
    };
    let (status, url, robots) = location("/original/cccc").await;
    assert_eq!(status, StatusCode::FOUND);
    // the first by BFS id, percent-encoded
    assert_eq!(
        url.as_deref(),
        Some("https://www.stadt-zuerich.ch/Budget%20Mai%202024.pdf")
    );
    assert_eq!(robots.unwrap(), "noindex, nofollow");
    let (_, url, _) = location("/original/cccc?commune=5938").await;
    assert_eq!(url.as_deref(), Some("https://www.yverdon.ch/budget.pdf"));
    let (_, url, _) = location("/original/cccc?commune=4001").await;
    assert_eq!(
        url.as_deref(),
        Some("https://www.stadt-zuerich.ch/Budget%20Mai%202024.pdf")
    ); // a commune that does not link it: the first
    let (_, url, _) = location("/original/cccc?commune=5938&page=16").await;
    assert_eq!(
        url.as_deref(),
        Some("https://www.yverdon.ch/budget.pdf#page=16")
    );
    let (_, url, _) = location("/original/cccc?commune=5938&page=0").await;
    assert_eq!(url.as_deref(), Some("https://www.yverdon.ch/budget.pdf"));
    for uri in ["/original/dddd", "/original/eeee", "/original/nope"] {
        let (status, _, _) = location(uri).await; // no URL, not http(s), unknown
        assert_eq!(status, StatusCode::NOT_FOUND, "{uri}");
    }
}

async fn usage_rows(path: &Path, want: usize) -> Vec<(String, String, i64, i64, i64, i64)> {
    let pool = SqlitePool::connect_with(SqliteConnectOptions::new().filename(path))
        .await
        .unwrap();
    for _ in 0..50 {
        let rows: Vec<(String, String, i64, i64, i64, i64)> = sqlx::query_as(
            "SELECT channel, route, bot, status, sum(requests), sum(searches) FROM totals \
             GROUP BY 1, 2, 3, 4 ORDER BY 1, 2",
        )
        .fetch_all(&pool)
        .await
        .unwrap();
        if rows.iter().map(|r| r.4 as usize).sum::<usize>() >= want {
            return rows;
        }
        tokio::time::sleep(Duration::from_millis(40)).await;
    }
    panic!("usage rows not written");
}

#[tokio::test]
async fn usage_counts_route_templates_without_paths_or_addresses() {
    let f = fixture(true).await;
    let browser = "Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0";
    let peer: SocketAddr = "127.0.0.1:40000".parse().unwrap();
    for (uri, dnt) in [
        ("/api/documents?q=Protokoll", false),
        ("/api/documents?q=nichtsdergleichen", false),
        ("/api/documents?offset=20", false), // a further page, not a search
        ("/de/gemeinden/261", false),
        ("/fr/gemeinden/5938", true),
        ("/_app/immutable/app.js", false),
        ("/no/such/page", false),
        ("/api/nothing-here", false),
    ] {
        let mut request = Request::get(uri)
            .header(header::USER_AGENT, browser)
            .header("x-forwarded-for", "203.0.113.7");
        if dnt {
            request = request.header("dnt", "1");
        }
        let mut request = request.body(Body::empty()).unwrap();
        request.extensions_mut().insert(ConnectInfo(peer));
        send(&f.router, request).await;
    }
    get(&f.router, "/api/runs").await; // no user agent: a bot
    let rows = usage_rows(&f.usage_path, 6).await;
    assert_eq!(
        rows,
        [
            ("api".into(), "/api/documents".into(), 0, 200, 3, 2),
            ("api".into(), "/api/runs".into(), 1, 200, 1, 0),
            ("web".into(), "/gemeinden/{id}".into(), 0, 200, 2, 0),
        ]
    );
    let pool = SqlitePool::connect_with(SqliteConnectOptions::new().filename(&f.usage_path))
        .await
        .unwrap();
    let (empty, visitors): (i64, i64) = sqlx::query_as(
        "SELECT (SELECT sum(empty_searches) FROM totals), (SELECT count(*) FROM visitors)",
    )
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(empty, 1);
    assert_eq!(visitors, 1); // the DNT request has no visitor identifier
    let dump: Vec<(String,)> = sqlx::query_as(
        "SELECT group_concat(day || route || channel) FROM totals UNION ALL SELECT group_concat(token) FROM visitors",
    )
    .fetch_all(&pool)
    .await
    .unwrap();
    let dump = format!("{dump:?}");
    for secret in [
        "Protokoll",
        "gemeinden/261",
        "203.0.113",
        "127.0.0.1",
        "Mozilla",
    ] {
        assert!(!dump.contains(secret), "{secret} stored");
    }
}

/// `(key, requests)` rows of the usage table `referrers` or `bots`, once `want` requests are in.
async fn usage_counts(path: &Path, sql: &'static str, want: i64) -> Vec<(String, i64)> {
    let pool = SqlitePool::connect_with(SqliteConnectOptions::new().filename(path))
        .await
        .unwrap();
    for _ in 0..50 {
        let rows: Vec<(String, i64)> = sqlx::query_as(sql)
            .fetch_all(&pool)
            .await
            .unwrap_or_default();
        if rows.iter().map(|r| r.1).sum::<i64>() >= want {
            return rows;
        }
        tokio::time::sleep(Duration::from_millis(40)).await;
    }
    panic!("usage counts not written: {sql}");
}

#[tokio::test]
async fn usage_counts_crawlers_linking_domains_and_browsers_that_run_the_front_end() {
    let f = fixture(true).await;
    let firefox = "Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0";
    let peer: SocketAddr = "127.0.0.1:40000".parse().unwrap();
    let send_as = |uri: &str, agent: &str, extra: &[(&str, &str)]| {
        let mut request = Request::get(uri)
            .header(header::USER_AGENT, agent)
            .header("x-forwarded-for", "203.0.113.7");
        for (name, value) in extra {
            request = request.header(*name, *value);
        }
        let mut request = request.body(Body::empty()).unwrap();
        request.extensions_mut().insert(ConnectInfo(peer));
        send(&f.router, request)
    };
    let news = (
        "referer",
        "https://user@News.Example.ch:8443/artikel?name=Muster#top",
    );
    send_as("/de/gemeinden/261", firefox, &[news]).await;
    send_as("/fr/gemeinden/5938", firefox, &[news]).await;
    // the site itself, a former name of it, an address and an opted-out browser are not counted
    send_as(
        "/de",
        firefox,
        &[("referer", "https://example.test/de/suche")],
    )
    .await;
    send_as("/de", firefox, &[("referer", "https://old.example.test/")]).await;
    send_as("/de", firefox, &[("referer", "http://192.0.2.1/")]).await;
    send_as("/de", firefox, &[news, ("dnt", "1")]).await;
    // a crawler with a link is counted by name, its link not
    let google = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)";
    send_as("/de/gemeinden/261", google, &[news]).await;
    send_as("/api/communes", "", &[]).await;
    // the front end's first request: a browser that runs it
    send_as("/api/communes", firefox, &[]).await;
    let referrers = usage_counts(&f.usage_path, "SELECT host, requests FROM referrers", 2).await;
    assert_eq!(referrers, [("news.example.ch".to_string(), 2)]);
    let bots = usage_counts(
        &f.usage_path,
        "SELECT name, requests FROM bots ORDER BY 1",
        2,
    )
    .await;
    assert_eq!(
        bots,
        [("Googlebot".to_string(), 1), ("none".to_string(), 1)]
    );
    let browsers = usage_counts(
        &f.usage_path,
        "SELECT day, count(*) FROM browser_visitors GROUP BY day",
        1,
    )
    .await;
    assert_eq!(browsers.len(), 1);
    assert_eq!(browsers[0].1, 1);
    let rows = usage_rows(&f.usage_path, 9).await;
    assert!(
        rows.iter()
            .any(|r| r == &("web".into(), "/gemeinden/{id}".into(), 1, 200, 1, 0))
    );
    let pool = SqlitePool::connect_with(SqliteConnectOptions::new().filename(&f.usage_path))
        .await
        .unwrap();
    let dump: Vec<(String,)> = sqlx::query_as(
        "SELECT group_concat(host) FROM referrers UNION ALL SELECT group_concat(name) FROM bots \
         UNION ALL SELECT group_concat(token) FROM browser_visitors",
    )
    .fetch_all(&pool)
    .await
    .unwrap();
    let dump = format!("{dump:?}");
    for secret in ["artikel", "Muster", "user", "8443", "203.0.113", "Mozilla"] {
        assert!(!dump.contains(secret), "{secret} stored");
    }
}

#[tokio::test]
async fn the_usage_report_needs_its_token_and_is_not_counted() {
    let off = fixture(false).await;
    let (status, _) = get(&off.router, "/api/usage").await;
    assert_eq!(status, StatusCode::NOT_FOUND); // no usage statistics, no report
    let f = fixture(true).await;
    get(&f.router, "/api/runs").await;
    usage_rows(&f.usage_path, 1).await;
    for authorization in [None, Some("Bearer wrong"), Some(USAGE_TOKEN)] {
        let mut request = Request::get("/api/usage");
        if let Some(value) = authorization {
            request = request.header(header::AUTHORIZATION, value);
        }
        let (status, headers, _) = send(&f.router, request.body(Body::empty()).unwrap()).await;
        assert_eq!(status, StatusCode::UNAUTHORIZED, "{authorization:?}");
        assert_eq!(headers[header::WWW_AUTHENTICATE], "Bearer");
    }
    let request = Request::get("/api/usage?days=7")
        .header(header::AUTHORIZATION, format!("Bearer {USAGE_TOKEN}"))
        .body(Body::empty())
        .unwrap();
    let (status, headers, body) = send(&f.router, request).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(headers[header::CACHE_CONTROL], "no-store");
    let report: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(keys(&report), ["totals", "visitors", "referrers", "bots"]);
    let totals = report["totals"].as_array().unwrap();
    assert_eq!(totals.len(), 1);
    assert_eq!(totals[0]["route"], "/api/runs");
    assert_eq!(totals[0]["bot"], true);
    assert_eq!(report["bots"][0]["name"], "none");
    // the report itself stays out of the counts
    tokio::time::sleep(Duration::from_millis(200)).await;
    let rows = usage_rows(&f.usage_path, 1).await;
    assert!(rows.iter().all(|r| r.1 != "/api/usage"), "{rows:?}");
}

#[tokio::test]
async fn former_names_of_the_site_lead_to_the_same_path_and_are_counted() {
    let f = fixture(true).await;
    for host in ["old.example.test", "OLD.example.test:443"] {
        let request = Request::post("/mcp?x=1")
            .header(header::HOST, host)
            .body(Body::empty())
            .unwrap();
        let (status, headers, _) = send(&f.router, request).await;
        assert_eq!(status, StatusCode::PERMANENT_REDIRECT);
        assert_eq!(headers[header::LOCATION], "https://example.test/mcp?x=1");
    }
    // an old link to a page without a language: to the German page in one step
    let request = Request::get("/gemeinden/261?commune=1")
        .header(header::HOST, "old.example.test")
        .body(Body::empty())
        .unwrap();
    let (status, headers, _) = send(&f.router, request).await;
    assert_eq!(status, StatusCode::PERMANENT_REDIRECT);
    assert_eq!(
        headers[header::LOCATION],
        "https://example.test/de/gemeinden/261?commune=1"
    );
    for (uri, to) in [
        ("/gemeinden/261", "/de/gemeinden/261"),
        ("/kantone/zh", "/de/kantone/zh"),
        ("/suche?q=Budget", "/de/suche?q=Budget"),
        ("/ueber-uns", "/de/ueber-uns"),
        ("/api", "/de/api"),
    ] {
        let request = Request::get(uri)
            .header(header::HOST, "example.test")
            .body(Body::empty())
            .unwrap();
        let (status, headers, _) = send(&f.router, request).await;
        assert_eq!(status, StatusCode::PERMANENT_REDIRECT, "{uri}");
        assert_eq!(headers[header::LOCATION], to, "{uri}");
    }
    let request = Request::get("/de/gemeinden/261")
        .header(header::HOST, "example.test")
        .body(Body::empty())
        .unwrap();
    let (status, _, _) = send(&f.router, request).await;
    assert_eq!(status, StatusCode::OK);
    let rows = usage_rows(&f.usage_path, 4).await;
    assert!(
        rows.contains(&("redirect".into(), "old.example.test".into(), 1, 308, 3, 0)),
        "{rows:?}"
    );
}

#[tokio::test]
async fn usage_keeps_daily_counts_and_turns_past_visitors_into_a_number() {
    let f = fixture(true).await;
    let pool = SqlitePool::connect_with(SqliteConnectOptions::new().filename(&f.usage_path))
        .await
        .unwrap();
    sqlx::raw_sql(
        "INSERT INTO totals VALUES ('2020-01-01', 'web', '/', 0, 200, 5, 0, 0, 10.0); \
         INSERT INTO visitors VALUES ('2020-01-01', 'a'), ('2020-01-01', 'b'); \
         INSERT INTO daily_keys VALUES ('2020-01-01', x'00');",
    )
    .execute(&pool)
    .await
    .unwrap();
    send(
        &f.router,
        Request::get("/api/communes").body(Body::empty()).unwrap(),
    )
    .await;
    for _ in 0..50 {
        let counted: Option<i64> =
            sqlx::query_scalar("SELECT visitors FROM daily_visitors WHERE day = '2020-01-01'")
                .fetch_optional(&pool)
                .await
                .unwrap();
        if counted.is_some() {
            assert_eq!(counted, Some(2));
            let left: (i64, i64, i64) = sqlx::query_as(
                "SELECT (SELECT count(*) FROM visitors WHERE day < date('now')), \
                 (SELECT count(*) FROM daily_keys WHERE day < date('now')), \
                 (SELECT requests FROM totals WHERE day = '2020-01-01')",
            )
            .fetch_one(&pool)
            .await
            .unwrap();
            assert_eq!(left, (0, 0, 5)); // no HMAC and no key of a past day; its counts stay
            return;
        }
        tokio::time::sleep(Duration::from_millis(40)).await;
    }
    panic!("the past day's visitors were not counted");
}

#[tokio::test]
async fn the_front_end_is_served_with_a_fallback_and_api_errors_stay_json() {
    let f = fixture(false).await;
    let request = Request::get("/irgendwo").body(Body::empty()).unwrap();
    let (status, headers, body) = send(&f.router, request).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        body,
        std::fs::read(f.static_dir.join("index.html")).unwrap()
    );
    assert_eq!(headers["x-robots-tag"], "noindex, nofollow");
    let request = Request::get("/_app/immutable/app.js")
        .body(Body::empty())
        .unwrap();
    let (status, headers, _) = send(&f.router, request).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        headers[header::CACHE_CONTROL],
        "public, max-age=31536000, immutable"
    );
    // the dropped decision and dataset endpoints are unknown paths now
    for uri in [
        "/api/nothing-here",
        "/api/decisions",
        "/api/stats",
        "/api/dataset/coverage",
        "/api/archive/communes",
    ] {
        let (status, body) = get(&f.router, uri).await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{uri}");
        assert_eq!(body, json!({"detail": "Not Found"}));
    }
}

#[tokio::test]
async fn mcp_lists_the_tools_and_answers_a_search() {
    let f = fixture(false).await;
    let post = |body: Value| {
        Request::post("/mcp")
            .header(header::HOST, "localhost")
            .header(header::CONTENT_TYPE, "application/json")
            .header(header::ACCEPT, "application/json, text/event-stream")
            .header("mcp-protocol-version", "2025-06-18")
            .body(Body::from(body.to_string()))
            .unwrap()
    };
    let call = |id: i64, name: &str, arguments: Value| {
        post(json!({"jsonrpc": "2.0", "id": id, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}))
    };
    let init = json!({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}});
    let (status, _, body) = send(&f.router, post(init)).await;
    assert_eq!(status, StatusCode::OK);
    let body: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(body["result"]["serverInfo"]["name"], "tractanda");
    let (_, _, body) = send(
        &f.router,
        post(json!({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})),
    )
    .await;
    let body: Value = serde_json::from_slice(&body).unwrap();
    let tools = body["result"]["tools"].as_array().unwrap();
    let mut names: Vec<&str> = tools.iter().map(|t| t["name"].as_str().unwrap()).collect();
    names.sort();
    assert_eq!(
        names,
        [
            "get_commune",
            "get_document",
            "list_communes",
            "read_pages",
            "search_documents",
            "search_text"
        ]
    );
    for tool in tools {
        assert!(tool["title"].is_string(), "{tool}");
        assert_eq!(tool["annotations"]["readOnlyHint"], true);
        assert_eq!(tool["annotations"]["destructiveHint"], false);
        assert_eq!(tool["annotations"]["idempotentHint"], true);
        if tool["name"] != "list_communes" {
            let description = tool["description"].as_str().unwrap();
            assert!(description.contains("no person has checked yet"), "{tool}");
        }
    }
    let search = tools
        .iter()
        .find(|t| t["name"] == "search_documents")
        .unwrap();
    let mut parameters: Vec<&str> = keys(&search["inputSchema"]["properties"]);
    parameters.sort();
    assert_eq!(
        parameters,
        [
            "body",
            "commune",
            "family",
            "first_family",
            "limit",
            "offset",
            "q",
            "section",
            "year"
        ]
    );

    let (_, _, body) = send(
        &f.router,
        call(3, "search_documents", json!({"q": "protokoll"})),
    )
    .await;
    let body: Value = serde_json::from_slice(&body).unwrap();
    let page = &body["result"]["structuredContent"];
    assert_eq!(page["total"], 1);
    assert_eq!(page["items"][0]["sha256"], "aaaa");
    assert_eq!(
        page["items"][0]["title"],
        "Protokoll Gemeindeversammlung 2024"
    );
    // the result of the API, shorter for a model
    let (_, api) = get(&f.router, "/api/documents?q=protokoll").await;
    assert_eq!(page, &brief(&api));
    assert_eq!(
        keys(&page["items"][0]),
        [
            "sha256",
            "title",
            "body_name",
            "listing_date",
            "date",
            "year",
            "years",
            "page_count",
            "body_kind",
            "languages",
            "sections",
            "communes",
            "original_url"
        ]
    );
    assert_eq!(
        page["items"][0]["sections"],
        json!(["invitation_agenda 1", "minutes 2-11", "vote_result 12"])
    );
    // the text a model reads is the same JSON
    let text: Value =
        serde_json::from_str(body["result"]["content"][0]["text"].as_str().unwrap()).unwrap();
    assert_eq!(&text, page);

    let (_, _, body) = send(
        &f.router,
        call(4, "search_documents", json!({"limit": 500})),
    )
    .await;
    let body: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(body["result"]["isError"], true);

    let (_, _, body) = send(
        &f.router,
        call(5, "get_document", json!({"sha256": "bbbb"})),
    )
    .await;
    let body: Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(
        body["result"]["structuredContent"]["run"]["model"],
        "model-a"
    );
    // an unknown document is a tool error, as in the other tools, not a result
    let result = call_tool(&f.router, "get_document", json!({"sha256": "nope"})).await;
    assert_eq!(result["isError"], true);
    assert!(
        result["content"][0]["text"]
            .as_str()
            .unwrap()
            .starts_with("Error executing tool get_document: no document has this sha256")
    );
    // the descriptions name the brackets of the marks, which are not ASCII
    for name in ["search_text", "read_pages"] {
        let tool = tools.iter().find(|t| t["name"] == name).unwrap();
        let description = tool["description"].as_str().unwrap();
        assert!(description.contains("U+FF3B ［ and U+FF3D ］"), "{name}");
    }

    // another Host is refused (DNS rebinding guard)
    let mut request = post(json!({"jsonrpc": "2.0", "id": 7, "method": "tools/list"}));
    request
        .headers_mut()
        .insert(header::HOST, "evil.example".parse().unwrap());
    let (status, _, _) = send(&f.router, request).await;
    assert!(status.is_client_error());
}

/// An API answer of `/api/documents` or `/api/pages` as the MCP search tools give it: documents
/// without the fields a model does not use, sections as labels, pages without highlights and links.
fn brief(api: &Value) -> Value {
    let mut page = api.clone();
    for item in page["items"].as_array_mut().unwrap() {
        let item = item.as_object_mut().unwrap();
        for key in ["first_seen", "pages_seen", "run_id", "personal_data"] {
            item.remove(key);
        }
        let sections: Vec<Value> = item["sections"]
            .as_array()
            .unwrap()
            .iter()
            .map(|s| {
                let (first, last) = (
                    s["first_page"].as_i64().unwrap(),
                    s["last_page"].as_i64().unwrap(),
                );
                let pages = if first == last {
                    first.to_string()
                } else {
                    format!("{first}-{last}")
                };
                let unseen = if s["partly_unseen"] == true {
                    " (partly unseen)"
                } else {
                    ""
                };
                json!(format!("{} {pages}{unseen}", s["type"].as_str().unwrap()))
            })
            .collect();
        item.insert("sections".into(), json!(sections));
        if let Some(pages) = item.get_mut("pages") {
            for p in pages.as_array_mut().unwrap() {
                let p = p.as_object_mut().unwrap();
                p.remove("highlights");
                p.remove("original_url");
            }
        }
    }
    page
}

#[tokio::test]
async fn mcp_finds_communes_by_canton_or_name_and_gives_one() {
    let f = fixture(false).await;
    // without a filter: the number of communes and documents per canton, not the communes
    let result = call_tool(&f.router, "list_communes", json!({})).await;
    assert_eq!(
        result["structuredContent"]["cantons"],
        json!([
            {"canton": "AG", "communes": 1, "documents": 0},
            {"canton": "VD", "communes": 1, "documents": 2},
            {"canton": "ZH", "communes": 1, "documents": 4}
        ])
    );
    let text = result["content"][0]["text"].as_str().unwrap();
    assert!(
        text.starts_with("The archive has 3 communes in 3 cantons. Pass canton"),
        "{text}"
    );
    assert!(
        text.ends_with("cantons[3]{canton,communes,documents}:\n  AG,1,0\n  VD,1,2\n  ZH,1,4"),
        "{text}"
    );
    // by canton, in any case: a table for the model, JSON for programs
    let result = call_tool(&f.router, "list_communes", json!({"canton": "zh"})).await;
    assert_eq!(
        result["content"][0]["text"],
        "communes[1]{bfs_id,name,canton,language,documents,register_status}:\n  \
         261,Zürich,ZH,de,4,configured"
    );
    assert_eq!(
        result["structuredContent"]["communes"],
        json!([{"bfs_id": 261, "name": "Zürich", "canton": "ZH", "language": "de",
                "documents": 4, "register_status": "configured"}])
    );
    let found = |arguments: Value| {
        let router = f.router.clone();
        async move {
            let result = call_tool(&router, "list_communes", arguments).await;
            result["structuredContent"]["communes"]
                .as_array()
                .unwrap()
                .iter()
                .map(|c| c["bfs_id"].as_i64().unwrap())
                .collect::<Vec<_>>()
        }
    };
    // by name: without case, accents and punctuation; ü also as ue
    for q in ["zurich", "ZUERICH", "Zürich", "rich"] {
        assert_eq!(found(json!({"q": q})).await, [261], "{q}");
    }
    assert_eq!(found(json!({"q": "yverdon les bains"})).await, [5938]);
    assert_eq!(found(json!({"q": "a"})).await, [4001, 5938]); // Aarau, Yverdon-les-Bains
    assert_eq!(found(json!({"q": "a", "canton": "VD"})).await, [5938]);
    // a commune without language or register entry
    let result = call_tool(&f.router, "list_communes", json!({"q": "aarau"})).await;
    assert!(
        result["content"][0]["text"]
            .as_str()
            .unwrap()
            .ends_with("\n  4001,Aarau,AG,null,0,null")
    );
    // nothing found says how to search
    let result = call_tool(&f.router, "list_communes", json!({"canton": "Zürich"})).await;
    assert!(
        result["content"][0]["text"]
            .as_str()
            .unwrap()
            .starts_with("No commune matches. canton takes a code such as ZH")
    );
    assert_eq!(result["structuredContent"]["communes"], json!([]));
    // one commune: as the API gives it
    let result = call_tool(&f.router, "get_commune", json!({"bfs_id": 261})).await;
    let (_, api) = get(&f.router, "/api/communes/261").await;
    assert_eq!(result["structuredContent"], api);
    assert_eq!(api["register"]["status"], "configured");
    let result = call_tool(&f.router, "get_commune", json!({"bfs_id": 9})).await;
    assert_eq!(result["isError"], true);
    assert!(
        result["content"][0]["text"]
            .as_str()
            .unwrap()
            .contains("list_communes finds it")
    );
}

async fn page(router: &Router, uri: &str) -> (StatusCode, header::HeaderMap, String) {
    let request = Request::get(uri).body(Body::empty()).unwrap();
    let (status, headers, body) = send(router, request).await;
    (status, headers, String::from_utf8(body).unwrap())
}

#[tokio::test]
async fn commune_pages_carry_their_facts_for_crawlers() {
    let f = fixture(false).await;
    let (status, headers, html) = page(&f.router, "/fr/gemeinden/261").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(headers["x-robots-tag"], "noindex, nofollow"); // indexing is off by default
    assert!(html.starts_with("<!doctype html><html lang=\"fr\">"));
    let title = "Zürich : procès-verbaux, ordres du jour et budgets de la commune — Tractanda";
    assert!(html.contains(&format!("<title>{title}</title>")));
    assert!(html.contains(
        "<meta name=\"description\" content=\"Zürich (ZH) : procès-verbaux, ordres du jour, budgets \
         et comptes 2023–2024, 4 documents, recherche en texte intégral et liens vers les originaux.\">"
    ));
    assert!(
        html.contains("<link rel=\"canonical\" href=\"https://example.test/fr/gemeinden/261\">")
    );
    assert!(html.contains("hreflang=\"de\" href=\"https://example.test/de/gemeinden/261\""));
    assert!(html.contains("\"@type\":\"AdministrativeArea\""));
    // link previews in the page's language
    assert!(html.contains(&format!("<meta property=\"og:title\" content=\"{title}\">")));
    assert!(html.contains("<meta property=\"og:locale\" content=\"fr_CH\">"));
    assert!(html.contains("<meta property=\"og:locale:alternate\" content=\"de_CH\">"));
    assert!(html.contains(
        "<meta property=\"og:image\" content=\"https://example.test/og/tractanda-fr.png\">"
    ));
    assert!(html.contains("<meta name=\"twitter:card\" content=\"summary_large_image\">"));
    assert!(html.contains(
        "\"containedInPlace\":{\"@type\":\"AdministrativeArea\",\"name\":\"Canton de Zurich\",\"url\":\"https://example.test/fr/kantone/zh\"}"
    ));
    // commune pages are not indexed until SEARCH_INDEX_COMMUNES is set
    assert!(html.contains("<meta name=\"robots\" content=\"noindex\">"));
    // the fact sheet opens the body: counts, years and the newest documents
    let facts = html.split("<main id=\"oc-facts\">").nth(1).unwrap();
    assert!(html.contains("data-sveltekit-preload-data=\"hover\"><main id=\"oc-facts\">"));
    assert!(facts.starts_with("<h1>Zürich</h1><p>Zürich (ZH) : 4 documents"));
    assert!(facts.contains("<p><a href=\"/fr/kantone/zh\">Canton de Zurich</a></p>"));
    assert!(facts.contains("<p>2024: 2 · 2023: 1 · sans année: 1</p>"));
    assert!(facts.contains(
        "<li>12.06.2024: <a href=\"https://example.test/original/aaaa?commune=261\" rel=\"nofollow\">Protokoll Gemeindeversammlung 2024</a></li>"
    ));
    // a newly deployed front end names new script files: the next page uses its shell at once
    let deployed = SHELL.replace("<head>", "<head><script src=\"/_app/new.js\"></script>");
    std::fs::write(f.static_dir.join("index.html"), deployed).unwrap();
    let (_, _, html) = page(&f.router, "/fr/gemeinden/261").await;
    assert!(html.contains("/_app/new.js"));
    for unknown in [
        "/de/gemeinden/999",
        "/de/gemeinden/abc",
        "/de/gemeinden/4176", // only in the register
    ] {
        let (status, _, html) = page(&f.router, unknown).await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{unknown}");
        assert!(html.contains("<meta name=\"robots\" content=\"noindex\">"));
    }
}

#[tokio::test]
async fn canton_pages_list_their_communes_for_crawlers() {
    let f = fixture(false).await;
    let (status, _, html) = page(&f.router, "/fr/kantone/zh").await;
    assert_eq!(status, StatusCode::OK);
    assert!(html.contains(
        "<title>Canton de Zurich : procès-verbaux, ordres du jour et budgets des communes — Tractanda</title>"
    ));
    assert!(html.contains(
        "<meta name=\"description\" content=\"Canton de Zurich : procès-verbaux, ordres du jour, \
         budgets et comptes de 1 communes, recherche en texte intégral et liens vers les originaux.\">"
    ));
    assert!(html.contains("<link rel=\"canonical\" href=\"https://example.test/fr/kantone/zh\">"));
    assert!(html.contains("hreflang=\"it\" href=\"https://example.test/it/kantone/zh\""));
    assert!(html.contains("\"identifier\":\"ZH\""));
    // the communes with documents, linked; Aeugst am Albis is only in the register
    let facts = html.split("<main id=\"oc-facts\">").nth(1).unwrap();
    assert!(facts.starts_with(
        "<h1>Canton de Zurich</h1><p>Canton de Zurich : 1 communes avec des documents"
    ));
    assert!(facts.contains(
        "<h2>Communes</h2><ul><li><a href=\"/fr/gemeinden/261\">Zürich</a> (4)</li></ul>"
    ));
    // not indexed until SEARCH_INDEX_COMMUNES is set
    assert!(html.contains("<meta name=\"robots\" content=\"noindex\">"));
    for unknown in ["/de/kantone/xx", "/de/kantone/ZH", "/de/kantone/zhh"] {
        let (status, _, html) = page(&f.router, unknown).await;
        assert_eq!(status, StatusCode::NOT_FOUND, "{unknown}");
        assert!(html.contains("<meta name=\"robots\" content=\"noindex\">"));
    }
    let communes = build(false, true, true, false).await;
    let (_, _, html) = page(&communes.router, "/en/kantone/vd").await;
    assert!(!html.contains("<meta name=\"robots\""));
    assert!(html.contains("<h1>Canton of Vaud</h1>"));
    // a canton without documents has nothing to find
    let (status, _, html) = page(&communes.router, "/de/kantone/ag").await;
    assert_eq!(status, StatusCode::OK);
    assert!(html.contains("<meta name=\"robots\" content=\"noindex\">"));
    assert!(!html.contains("<h2>"));
}

#[tokio::test]
async fn start_about_privacy_and_search_pages() {
    let f = fixture(false).await;
    let (_, _, html) = page(&f.router, "/it").await;
    assert!(html.contains("\"@type\":\"Dataset\""));
    assert!(html.contains("5 documenti da 2 comuni"));
    assert!(html.contains("Sono classificati automaticamente"));
    assert!(html.contains("<a href=\"/it/gemeinden/261\">Zürich (ZH)</a>"));
    assert!(!html.contains("/it/gemeinden/4001")); // no documents
    assert!(html.contains(
        "<h2>Cantoni</h2><ul><li><a href=\"/it/kantone/vd\">Cantone di Vaud</a></li>\
         <li><a href=\"/it/kantone/zh\">Cantone di Zurigo</a></li></ul>"
    ));
    assert!(html.contains("\"includedInDataCatalog\":{\"@type\":\"DataCatalog\""));
    assert!(!html.contains("/api/documents")); // closed to crawlers: no download there
    let (status, _, html) = page(&f.router, "/de/ueber-uns").await;
    assert_eq!(status, StatusCode::OK);
    assert!(html.contains("<h1>Über Tractanda</h1>"));
    let (status, _, html) = page(&f.router, "/fr/datenschutz").await;
    assert_eq!(status, StatusCode::OK);
    assert!(html.contains("<h1>Protection des données</h1>"));
    let (_, _, html) = page(&f.router, "/de/suche").await;
    assert!(html.contains("<meta name=\"robots\" content=\"noindex\">"));
    for (uri, heading) in [
        ("/fr/api", "<h1>API et serveur MCP</h1>"),
        ("/it/lizenzen", "<h1>Licenze e ringraziamenti</h1>"),
    ] {
        let (status, _, html) = page(&f.router, uri).await;
        assert_eq!(status, StatusCode::OK, "{uri}");
        assert!(html.contains(heading), "{uri}");
        assert!(html.contains(&format!(
            "<link rel=\"canonical\" href=\"https://example.test{uri}\">"
        )));
        assert!(!html.contains("<meta name=\"robots\""), "{uri}");
    }
    assert!(
        page(&f.router, "/it/api")
            .await
            .2
            .starts_with("<!doctype html><html lang=\"it\">")
    );
    // any other path under a language: the front end's error page in that language, status 404
    let (status, _, html) = page(&f.router, "/fr/nexiste/pas").await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert!(html.starts_with("<!doctype html><html lang=\"fr\">"));
    assert!(html.contains("<meta name=\"robots\" content=\"noindex\">"));
    let request = Request::get("/").body(Body::empty()).unwrap();
    let (status, headers, _) = send(&f.router, request).await;
    assert_eq!(status, StatusCode::TEMPORARY_REDIRECT);
    assert_eq!(headers[header::LOCATION], "/de");
    // the browser's language, in its order, when the site has it
    for (accept, to) in [
        ("en-US,en;q=0.9", "/en"),
        ("fr-CH, fr;q=0.9, de;q=0.8", "/fr"),
        ("es-ES,it;q=0.8", "/it"),
        ("es", "/de"),
    ] {
        let request = Request::get("/")
            .header(header::ACCEPT_LANGUAGE, accept)
            .body(Body::empty())
            .unwrap();
        let (status, headers, _) = send(&f.router, request).await;
        assert_eq!(status, StatusCode::TEMPORARY_REDIRECT);
        assert_eq!(headers[header::LOCATION], to, "{accept}");
        assert_eq!(headers[header::VARY], "Accept-Language");
    }
}

#[tokio::test]
async fn robots_sitemap_and_llms_txt() {
    let f = fixture(false).await;
    let (status, headers, robots) = page(&f.router, "/robots.txt").await;
    assert_eq!(status, StatusCode::OK);
    assert!(
        headers[header::CONTENT_TYPE]
            .to_str()
            .unwrap()
            .starts_with("text/plain")
    );
    // every crawler reads the pages, none the API, the MCP server, the originals or searches
    assert!(
        robots.contains("User-agent: *\nDisallow: /api/\nDisallow: /mcp\nDisallow: /original/\n")
    );
    assert!(robots.contains("Disallow: /it/suche\nDisallow: /en/suche\nAllow: /\n"));
    assert_eq!(robots.matches("User-agent:").count(), 1);
    assert!(robots.contains("# https://huggingface.co/datasets/davidrzs/tractanda\n"));
    assert!(
        !robots.contains("Sitemap:"),
        "no sitemap while indexing is off"
    );
    let (_, _, xml) = page(&f.router, "/sitemap.xml").await;
    assert!(!xml.contains("/gemeinden/")); // not until SEARCH_INDEX_COMMUNES is set
    assert!(!xml.contains("/kantone/"));
    assert!(!xml.contains("entscheide"));
    assert!(xml.contains("<loc>https://example.test/fr</loc>"));
    assert!(xml.contains("<loc>https://example.test/it/datenschutz</loc>"));
    assert!(xml.contains("<loc>https://example.test/de/api</loc>"));
    assert!(xml.contains("<loc>https://example.test/fr/lizenzen</loc>"));
    let (status, headers, llms) = page(&f.router, "/llms.txt").await;
    assert_eq!(status, StatusCode::OK);
    assert!(
        headers[header::CONTENT_TYPE]
            .to_str()
            .unwrap()
            .starts_with("text/markdown")
    );
    assert!(llms.starts_with("# Tractanda\n\n> Open archive"));
    assert!(llms.contains("The archive lists 5 documents from 2 communes, each classified"));
    assert!(llms.contains("[MCP server](https://example.test/mcp)"));
    assert!(llms.contains("`search_documents`"));
    assert!(!llms.contains("search_text")); // no index, no full-text search
    assert!(!llms.contains("decisions`") && !llms.contains("huggingface"));

    let indexed = build(false, true, false, false).await;
    let (_, headers, _) = page(&indexed.router, "/de").await;
    assert!(headers.get("x-robots-tag").is_none());
    let (_, _, robots) = page(&indexed.router, "/robots.txt").await;
    assert!(robots.ends_with("Sitemap: https://example.test/sitemap.xml\n"));
    // with SEARCH_INDEX_COMMUNES the commune pages may be indexed and join the sitemap
    let communes = build(false, true, true, false).await;
    let (_, _, html) = page(&communes.router, "/de/gemeinden/261").await;
    assert!(!html.contains("<meta name=\"robots\""));
    let (_, _, xml) = page(&communes.router, "/sitemap.xml").await;
    for id in [261, 5938] {
        assert!(xml.contains(&format!(
            "<loc>https://example.test/de/gemeinden/{id}</loc>"
        )));
    }
    // a commune without documents stays out of the sitemap and is not indexed
    assert!(!xml.contains("/gemeinden/4001"));
    assert!(xml.contains("<loc>https://example.test/fr/kantone/zh</loc>"));
    assert!(xml.contains("<loc>https://example.test/en/kantone/vd</loc>"));
    assert!(!xml.contains("/kantone/ag"));
    let (_, _, html) = page(&communes.router, "/de/gemeinden/4001").await;
    assert!(html.contains("<meta name=\"robots\" content=\"noindex\">"));
    assert!(!xml.contains("/gemeinden/4176")); // only in the register
}

#[tokio::test]
async fn openapi_describes_every_route_and_the_fields_the_api_sends() {
    let f = fixture(false).await;
    let (status, spec) = get(&f.router, "/api/openapi.json").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(spec["openapi"], "3.1.0");
    // every /api and /original route of the router, and nothing else
    let mut routes: Vec<&str> = include_str!("../src/routes/mod.rs")
        .split(".route(\"")
        .skip(1)
        .filter_map(|rest| rest.split('"').next())
        .filter(|p| (p.starts_with("/api/") && *p != "/api/{*rest}") || p.starts_with("/original/"))
        // the operator's reports and the chatbot of the site's own page: not the public API
        .filter(|p| !p.starts_with("/api/usage") && !p.starts_with("/api/ask"))
        .collect();
    routes.sort();
    let mut described = keys(&spec["paths"]);
    described.sort();
    assert_eq!(routes, described);
    // every reference resolves
    fn refs<'a>(v: &'a Value, out: &mut Vec<&'a str>) {
        match v {
            Value::Object(o) => {
                if let Some(Value::String(r)) = o.get("$ref") {
                    out.push(r);
                }
                o.values().for_each(|v| refs(v, out));
            }
            Value::Array(a) => a.iter().for_each(|v| refs(v, out)),
            _ => {}
        }
    }
    let mut found = Vec::new();
    refs(&spec, &mut found);
    assert!(!found.is_empty());
    for r in found {
        let name = r.strip_prefix("#/components/schemas/").expect(r);
        assert!(spec["components"]["schemas"][name].is_object(), "{r}");
    }
    // the described fields are the fields the API sends, in its order
    let fields = |name: &str| keys(&spec["components"]["schemas"][name]["properties"]);
    let (_, page) = get(&f.router, "/api/documents?limit=1").await;
    assert_eq!(fields("DocumentPage"), keys(&page));
    assert_eq!(fields("Document"), keys(&page["items"][0]));
    assert_eq!(fields("Facets"), keys(&page["facets"]));
    let (_, commune) = get(&f.router, "/api/communes/261").await;
    assert_eq!(fields("CommuneDetail"), keys(&commune));
    let (_, communes) = get(&f.router, "/api/communes").await;
    assert_eq!(fields("Commune"), keys(&communes[0]));
    // the query parameters are the ones the handler reads
    let parameters: Vec<&str> = spec["paths"]["/api/documents"]["get"]["parameters"]
        .as_array()
        .unwrap()
        .iter()
        .map(|p| p["name"].as_str().unwrap())
        .collect();
    assert_eq!(
        parameters,
        [
            "commune",
            "section",
            "family",
            "first_family",
            "body",
            "year",
            "q",
            "offset",
            "limit"
        ]
    );
}

/// The documents of a full-text result and, per document, its pages.
fn hits(page: &Value) -> Vec<(&str, Vec<i64>)> {
    page["items"]
        .as_array()
        .unwrap()
        .iter()
        .map(|d| {
            let pages = d["pages"].as_array().unwrap();
            (
                d["sha256"].as_str().unwrap(),
                pages.iter().map(|p| p["page"].as_i64().unwrap()).collect(),
            )
        })
        .collect()
}

/// The highlighted parts of a page's snippet.
fn highlighted(page: &Value) -> Vec<String> {
    let chars: Vec<char> = page["snippet"].as_str().unwrap().chars().collect();
    page["highlights"]
        .as_array()
        .unwrap()
        .iter()
        .map(|h| {
            let (a, b) = (h[0].as_u64().unwrap(), h[1].as_u64().unwrap());
            chars[a as usize..b as usize].iter().collect()
        })
        .collect()
}

#[tokio::test]
async fn full_text_finds_documents_with_their_best_pages() {
    let f = fulltext_fixture().await;
    let (status, page) = get(&f.router, "/api/pages?q=Investitionskredit").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        keys(&page),
        ["total", "capped", "offset", "limit", "facets", "items"]
    );
    assert_eq!(
        (&page["total"], &page["capped"], &page["limit"]),
        (&json!(1), &json!(false), &json!(10))
    );
    // the page with the word twice ranks first
    assert_eq!(hits(&page), [("aaaa", vec![2, 1])]);
    let item = &page["items"][0];
    assert_eq!(item["title"], "Protokoll Gemeindeversammlung 2024");
    assert_eq!(item["matching_pages"], 2);
    // full links, so that a client without the host can hand them on
    assert_eq!(item["original_url"], "https://example.test/original/aaaa");
    let best = &item["pages"][0];
    assert_eq!(
        keys(best),
        [
            "page",
            "section_types",
            "snippet",
            "highlights",
            "original_url"
        ]
    );
    assert_eq!(best["section_types"], json!(["minutes"]));
    assert_eq!(
        best["original_url"],
        "https://example.test/original/aaaa?page=2"
    );
    assert_eq!(
        best["snippet"],
        "Protokoll. Der Gemeinderat beantragt einen Investitionskredit von Fr. 2'980'000 für das \
         Schulhaus. Die Versammlung stimmt dem Investitionskredit zu."
    );
    assert_eq!(
        highlighted(best),
        ["Investitionskredit", "Investitionskredit"]
    );
    assert_eq!(
        item["pages"][1]["section_types"],
        json!(["invitation_agenda"])
    );

    // words in any order and place; case and accents do not matter
    let page_29 = |page: &Value| -> Value {
        page["items"][0]["pages"]
            .as_array()
            .unwrap()
            .iter()
            .find(|p| p["page"] == 29)
            .unwrap()
            .clone()
    };
    let (_, page) = get(&f.router, "/api/pages?q=CREDIT%20d'engagement").await;
    assert_eq!(page["items"][0]["matching_pages"], 2); // also with ’ on page 29
    assert_eq!(highlighted(&page_29(&page)), ["Crédit", "d’engagement"]);
    let (_, page) = get(&f.router, "/api/pages?q=ecole").await;
    assert_eq!(page["items"][0]["matching_pages"], 2);
    // the snippet is plain text: markup in the page is text, never ours
    let page = page_29(&page);
    let snippet = page["snippet"].as_str().unwrap();
    assert!(snippet.ends_with("<b>fin</b>"), "{snippet}");
    assert!(!snippet.contains(['\u{1}', '\u{2}']));
    // phrases and prefixes
    let (_, page) = get(&f.router, "/api/pages?q=%22Tempo%2030%22").await;
    assert_eq!(hits(&page).len(), 2); // "Tempo 30" and "Tempo-30-Zonen"
    let (_, page) = get(&f.router, "/api/pages?q=%2230%20Tempo%22").await;
    assert_eq!(page["total"], 0);
    let (_, page) = get(&f.router, "/api/pages?q=30%20Tempo").await;
    assert_eq!(page["total"], 2);
    let (_, page) = get(&f.router, "/api/pages?q=Gemeinde").await;
    assert_eq!(page["total"], 0);
    let (_, page) = get(&f.router, "/api/pages?q=Gemeinde*").await;
    let mut found: Vec<&str> = hits(&page).iter().map(|h| h.0).collect();
    found.sort();
    assert_eq!(found, ["aaaa", "cccc"]);
    assert!(
        highlighted(&page["items"][0]["pages"][0])
            .iter()
            .all(|w| w.starts_with("Gemeinde"))
    );
    // FTS5 syntax that is not ours is text
    for q in [
        "text:Tempo",
        "Tempo%20^30",
        "Tempo%20or%20xyz",
        "%22Tempo%20OR%22",
    ] {
        let (status, _) = get(&f.router, &format!("/api/pages?q={q}")).await;
        assert_eq!(status, StatusCode::OK, "{q}");
    }
    let (_, page) = get(&f.router, "/api/pages?q=Tempo%20or%20xyz").await;
    assert_eq!(page["total"], 0); // or in small letters is a word that must occur
}

#[tokio::test]
async fn full_text_takes_or_groups_and_near() {
    let f = fulltext_fixture().await;
    let found = |q: &'static str| {
        let router = f.router.clone();
        async move {
            let (status, page) = get(&router, &format!("/api/pages?q={q}")).await;
            assert_eq!(status, StatusCode::OK, "{q}");
            let mut found: Vec<(String, Vec<i64>)> = hits(&page)
                .into_iter()
                .map(|(sha, mut pages)| {
                    pages.sort();
                    (sha.to_string(), pages)
                })
                .collect();
            found.sort();
            found
        }
    };
    let hit = |sha: &str, pages: &[i64]| (sha.to_string(), pages.to_vec());
    // one query across languages: the German pages and the French ones
    assert_eq!(
        found("Investitionskredit%20OR%20%22cr%C3%A9dit%20d'engagement%22").await,
        vec![hit("aaaa", &[1, 2]), hit("bbbb", &[3, 29])]
    );
    // the words around OR bind more tightly: (Steuerfuss Budget) OR Tempo
    assert_eq!(
        found("Steuerfuss%20Budget%20OR%20Tempo").await,
        vec![hit("aaaa", &[1, 12]), hit("cccc", &[5])]
    );
    // parentheses group: (Steuerfuss OR Tempo) Budget
    assert_eq!(
        found("(Steuerfuss%20OR%20Tempo)%20Budget").await,
        vec![hit("aaaa", &[1]), hit("cccc", &[5])]
    );
    assert_eq!(
        found("Investitionskredit%20AND%20Protokoll").await,
        vec![hit("aaaa", &[2])]
    );
    // NEAR: "Investitionskredit für das Schulhaus" on page 1; seven words between on page 2
    assert_eq!(
        found("NEAR(Investitionskredit%20Schulhaus,%202)").await,
        vec![hit("aaaa", &[1])]
    );
    assert_eq!(
        found("NEAR(Investitionskredit%20Schulhaus)").await,
        vec![hit("aaaa", &[1, 2])]
    );
    // both alternatives are highlighted in the snippet
    let (_, page) = get(
        &f.router,
        "/api/pages?q=Steuerfuss%20OR%20Tempo&document=cccc",
    )
    .await;
    assert_eq!(
        highlighted(&page["items"][0]["pages"][0]),
        ["Steuerfuss", "Tempo"]
    );
}

#[tokio::test]
async fn full_text_refuses_what_it_would_not_search_as_typed() {
    let f = fulltext_fixture().await;
    for (q, says) in [
        ("*rasen", "can only end a word"),
        ("Kunst*rasen", "can only end a word"),
        ("Budget%20-Konto", "no exclusion"),
        ("Budget%20NOT%20Konto", "NOT is not supported"),
        ("Budget%20OR", "on each side"),
        ("OR%20Budget", "on each side"),
        ("Budget%20)", "without its ("),
        ("NEAR(Budget)", "at least two"),
    ] {
        let (status, body) = get(&f.router, &format!("/api/pages?q={q}")).await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{q}");
        let detail = body["detail"].as_str().unwrap();
        assert!(detail.contains(says), "{q}: {detail}");
    }
    let result = call_tool(&f.router, "search_text", json!({"q": "*rasen"})).await;
    assert_eq!(result["isError"], true);
    assert!(
        result["content"][0]["text"]
            .as_str()
            .unwrap()
            .contains("words that end in a word (*rasen) cannot be searched")
    );
}

#[tokio::test]
async fn full_text_filters_by_language_and_lists_of_sections_and_sorts_by_date() {
    let f = fulltext_fixture().await;
    let only = |uri: String| {
        let router = f.router.clone();
        async move {
            let (status, page) = get(&router, &uri).await;
            assert_eq!(status, StatusCode::OK, "{uri}");
            hits(&page)
                .into_iter()
                .map(|(sha, pages)| (sha.to_string(), pages))
                .collect::<Vec<_>>()
        }
    };
    let shas = |found: Vec<(String, Vec<i64>)>| -> Vec<String> {
        let mut shas: Vec<String> = found.into_iter().map(|h| h.0).collect();
        shas.sort();
        shas
    };
    let both = "/api/pages?q=Investitionskredit%20OR%20%22cr%C3%A9dit%20d'engagement%22";
    // aaaa is in German, bbbb in French and German
    assert_eq!(shas(only(format!("{both}&language=fr")).await), ["bbbb"]);
    assert_eq!(shas(only(format!("{both}&language=FR")).await), ["bbbb"]);
    assert_eq!(
        shas(only(format!("{both}&language=de")).await),
        ["aaaa", "bbbb"]
    );
    assert_eq!(shas(only(format!("{both}&language=it")).await).len(), 0);
    assert_eq!(
        shas(only(format!("{both}&language=it,%20fr")).await),
        ["bbbb"]
    );
    assert_eq!(shas(only(format!("{both}&language=")).await).len(), 2); // blank: no filter
    // several section types or families, and pages left out by type or family
    let steuerfuss = "/api/pages?q=Steuerfuss";
    assert_eq!(
        shas(only(format!("{steuerfuss}&section=budget,invitation_agenda")).await),
        ["aaaa", "cccc"]
    );
    assert_eq!(
        shas(only(format!("{steuerfuss}&section=budget,%20minutes")).await),
        ["cccc"]
    );
    assert_eq!(
        shas(only(format!("{steuerfuss}&family=money,proposals")).await),
        ["aaaa", "cccc"]
    );
    assert_eq!(
        shas(only(format!("{steuerfuss}&exclude_section=budget")).await),
        ["aaaa"]
    );
    assert_eq!(
        shas(only(format!("{steuerfuss}&exclude_family=money")).await),
        ["aaaa"]
    );
    assert_eq!(
        shas(
            only(format!(
                "{steuerfuss}&exclude_section=budget,invitation_agenda"
            ))
            .await
        )
        .len(),
        0
    );
    // exclusion applies to the page: aaaa's minutes page is left out, its agenda page stays
    assert_eq!(
        only("/api/pages?q=Investitionskredit&exclude_section=minutes".into()).await,
        [("aaaa".to_string(), vec![1])]
    );
    let (_, page) = get(
        &f.router,
        "/api/pages?q=Investitionskredit&exclude_section=minutes",
    )
    .await;
    assert_eq!(page["items"][0]["matching_pages"], 1);
    // by date: aaaa is of 2024-06-12, cccc has only its listing date 2023-11-01
    let order = |found: Vec<(String, Vec<i64>)>| -> Vec<String> {
        found.into_iter().map(|h| h.0).collect()
    };
    assert_eq!(
        order(only(format!("{steuerfuss}&sort=newest")).await),
        ["aaaa", "cccc"]
    );
    assert_eq!(
        order(only(format!("{steuerfuss}&sort=oldest")).await),
        ["cccc", "aaaa"]
    );
    assert_eq!(
        order(only(format!("{steuerfuss}&sort=oldest&limit=1&offset=1")).await),
        ["aaaa"]
    );
    assert_eq!(
        order(only(format!("{steuerfuss}&sort=")).await).len(),
        2 // blank: by relevance
    );
    // a document's pages stay best first when the documents are sorted by date
    let (_, page) = get(&f.router, "/api/pages?q=Gemeinde*&sort=newest").await;
    assert_eq!(page["items"][0]["sha256"], "aaaa");
    // the commune facet: 20 by default, more or fewer on request
    let (_, page) = get(&f.router, &format!("{steuerfuss}&commune_facets=1")).await;
    assert_eq!(
        page["facets"]["communes"],
        json!([{"bfs_id": 261, "name": "Zürich", "canton": "ZH", "count": 2}])
    );
    let (_, page) = get(&f.router, &format!("{steuerfuss}&commune_facets=0")).await;
    assert_eq!(page["facets"]["communes"], json!([]));
    assert_eq!(page["total"], 2);
    for bad in ["sort=best", "commune_facets=-1", "commune_facets=3001"] {
        let (status, body) = get(&f.router, &format!("{steuerfuss}&{bad}")).await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{bad}");
        assert!(body["detail"].is_string(), "{bad}");
    }
    // the MCP tool takes the same parameters
    let result = call_tool(
        &f.router,
        "search_text",
        json!({"q": "Steuerfuss", "sort": "oldest", "exclude_section": "accounts", "commune_facets": 1}),
    )
    .await;
    let (_, api) = get(
        &f.router,
        &format!("{steuerfuss}&sort=oldest&exclude_section=accounts&commune_facets=1"),
    )
    .await;
    assert_eq!(result["structuredContent"], brief(&api));
}

#[tokio::test]
async fn full_text_searches_inside_one_document_and_shows_more_or_fewer_pages() {
    let f = fulltext_fixture().await;
    // Gemeinde* matches pages 1 and 2 of aaaa and page 6 of cccc
    let (_, found) = get(&f.router, "/api/pages?q=Gemeinde*&document=cccc").await;
    assert_eq!(
        (&found["total"], hits(&found)),
        (&json!(1), vec![("cccc", vec![6])])
    );
    let (_, found) = get(
        &f.router,
        "/api/pages?q=Gemeinde*&document=aaaa&pages_per_document=1",
    )
    .await;
    assert_eq!(found["items"][0]["matching_pages"], 2);
    assert_eq!(hits(&found)[0].1.len(), 1);
    let (_, found) = get(&f.router, "/api/pages?q=Gemeinde*&document=nope").await;
    assert_eq!(found["total"], 0);
    for bad in ["0", "21", "x"] {
        let (status, _) = get(
            &f.router,
            &format!("/api/pages?q=Gemeinde*&pages_per_document={bad}"),
        )
        .await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{bad}");
    }
    // llms.txt tells agents about the search when the server has the index
    let (_, _, llms) = page(&f.router, "/llms.txt").await;
    assert!(llms.contains("`search_text`, `read_pages`"), "{llms}");
    assert!(llms.contains("/api/pages?q=Kunstrasen*%20Kredit"), "{llms}");
    assert!(
        llms.contains("`capped` is true") && llms.contains("［name］"),
        "{llms}"
    );
}

#[tokio::test]
async fn full_text_filters_and_facets() {
    let f = fulltext_fixture().await;
    let (_, page) = get(&f.router, "/api/pages?q=Steuerfuss").await;
    assert_eq!(page["total"], 2);
    assert_eq!(
        page["facets"],
        json!({
            "years": [{"value": 2024, "count": 1}, {"value": 2023, "count": 1}],
            "sections": [
                {"value": "budget", "count": 1},
                {"value": "invitation_agenda", "count": 1}
            ],
            "cantons": [{"value": "VD", "count": 1}, {"value": "ZH", "count": 2}],
            "communes": [
                {"bfs_id": 261, "name": "Zürich", "canton": "ZH", "count": 2},
                {"bfs_id": 5938, "name": "Yverdon-les-Bains", "canton": "VD", "count": 1}
            ]
        })
    );
    let only = |uri: &'static str| {
        let router = f.router.clone();
        async move {
            let (status, page) = get(&router, uri).await;
            assert_eq!(status, StatusCode::OK, "{uri}");
            let mut found: Vec<String> = hits(&page).iter().map(|h| h.0.to_string()).collect();
            found.sort();
            found
        }
    };
    assert_eq!(only("/api/pages?q=Steuerfuss&commune=5938").await, ["cccc"]);
    assert_eq!(
        only("/api/pages?q=Steuerfuss&canton=zh").await,
        ["aaaa", "cccc"]
    );
    assert_eq!(only("/api/pages?q=Steuerfuss&canton=VD").await, ["cccc"]);
    assert_eq!(only("/api/pages?q=Steuerfuss&year=2024").await, ["aaaa"]);
    assert_eq!(
        only("/api/pages?q=Steuerfuss&body=executive").await,
        ["cccc"]
    );
    assert_eq!(
        only("/api/pages?q=Steuerfuss&from=2024-06-01&to=2024-06-12").await,
        ["aaaa"]
    );
    // the listing date stands in for a missing date
    assert_eq!(
        only("/api/pages?q=Steuerfuss&from=2023-11-01&to=2023-11-01").await,
        ["cccc"]
    );
    assert_eq!(only("/api/pages?q=Steuerfuss&family=money").await, ["cccc"]);
    assert_eq!(only("/api/pages?q=Steuerfuss&family=").await.len(), 2); // blank: no filter
    // section and family apply to the page: aaaa's agenda page matches, its minutes page not
    assert_eq!(
        only("/api/pages?q=Steuerfuss&section=minutes").await.len(),
        0
    );
    let (_, page) = get(&f.router, "/api/pages?q=Investitionskredit&section=minutes").await;
    assert_eq!(hits(&page), [("aaaa", vec![2])]);
    assert_eq!(page["items"][0]["matching_pages"], 1);
    assert_eq!(
        page["facets"]["sections"],
        json!([{"value": "minutes", "count": 1}])
    );
    let (_, page) = get(
        &f.router,
        "/api/pages?q=Investitionskredit&section=invitation_agenda",
    )
    .await;
    assert_eq!(hits(&page), [("aaaa", vec![1])]);
    // pages of results
    let (_, page) = get(&f.router, "/api/pages?q=Steuerfuss&limit=1&offset=1").await;
    assert_eq!((page["total"].as_i64(), hits(&page).len()), (Some(2), 1));
    let (_, page) = get(&f.router, "/api/pages?q=Steuerfuss&limit=0").await;
    assert_eq!((page["total"].as_i64(), hits(&page).len()), (Some(2), 0));
}

#[tokio::test]
async fn full_text_refuses_bad_queries_with_a_reason() {
    let f = fulltext_fixture().await;
    for uri in [
        "/api/pages",
        "/api/pages?q=",
        "/api/pages?q=%22%22",
        "/api/pages?q=*",
        "/api/pages?q=ge*",
        "/api/pages?q=Budget&limit=51",
        "/api/pages?q=Budget&offset=-1",
        "/api/pages?q=Budget&from=2024",
        "/api/pages?q=Budget&to=31.12.2024",
        "/api/pages?q=Budget&year=x",
    ] {
        let (status, body) = get(&f.router, uri).await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{uri}");
        assert!(body["detail"].as_str().unwrap().len() > 5, "{uri}");
    }
    let (status, page) = get(&f.router, "/api/pages?q=Budget&limit=50").await;
    assert_eq!((status, &page["limit"]), (StatusCode::OK, &json!(50)));
}

#[tokio::test]
async fn pages_are_read_with_their_text_or_a_reason() {
    let f = fulltext_fixture().await;
    let (status, read) = get(&f.router, "/api/documents/aaaa/pages?from=1&to=3").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(keys(&read), ["sha256", "page_count", "pages"]);
    assert_eq!(read["page_count"], 12);
    let pages = read["pages"].as_array().unwrap();
    assert_eq!(pages.len(), 3);
    assert_eq!(
        keys(&pages[0]),
        [
            "page",
            "section_types",
            "source",
            "text",
            "reason",
            "original_url"
        ]
    );
    assert_eq!(
        (
            &pages[0]["source"],
            &pages[0]["reason"],
            &pages[0]["original_url"]
        ),
        (
            &json!("layer"),
            &Value::Null,
            &json!("https://example.test/original/aaaa?page=1")
        )
    );
    // the text as stored, line breaks and all
    assert!(
        pages[0]["text"]
            .as_str()
            .unwrap()
            .contains("mit\nSteuerfuss")
    );
    // a minutes page without text
    assert_eq!(pages[2]["section_types"], json!(["minutes"]));
    assert_eq!(
        (&pages[2]["text"], &pages[2]["source"]),
        (&Value::Null, &Value::Null)
    );
    assert!(
        pages[2]["reason"]
            .as_str()
            .unwrap()
            .starts_with("No text yet")
    );
    // not past the last page
    let (_, read) = get(&f.router, "/api/documents/aaaa/pages?from=10&to=19").await;
    let numbers: Vec<i64> = read["pages"]
        .as_array()
        .unwrap()
        .iter()
        .map(|p| p["page"].as_i64().unwrap())
        .collect();
    assert_eq!(numbers, [10, 11, 12]);
    assert_eq!(read["pages"][2]["source"], "ocr");
    assert_eq!(read["pages"][2]["section_types"], json!(["vote_result"]));
    // ten pages from `from` by default
    let (_, read) = get(&f.router, "/api/documents/bbbb/pages").await;
    assert_eq!(read["pages"].as_array().unwrap().len(), 10);
    let (_, read) = get(&f.router, "/api/documents/bbbb/pages?from=25").await;
    assert_eq!(read["pages"].as_array().unwrap().len(), 6); // 25 to 30
    // pages outside the search: no section of a searched type, or a bulletin
    let (_, read) = get(&f.router, "/api/documents/dddd/pages").await;
    assert_eq!(read["pages"].as_array().unwrap().len(), 3);
    assert!(
        read["pages"][0]["reason"]
            .as_str()
            .unwrap()
            .contains("no section")
    );
    let (_, read) = get(&f.router, "/api/documents/eeee/pages?from=1&to=1").await;
    assert!(
        read["pages"][0]["reason"]
            .as_str()
            .unwrap()
            .contains("bulletins")
    );
    // at most ten pages, a valid range, a known document
    for uri in [
        "/api/documents/aaaa/pages?from=1&to=11",
        "/api/documents/aaaa/pages?from=0",
        "/api/documents/aaaa/pages?from=5&to=4",
        "/api/documents/aaaa/pages?from=x",
    ] {
        let (status, body) = get(&f.router, uri).await;
        assert_eq!(status, StatusCode::BAD_REQUEST, "{uri}");
        assert!(body["detail"].is_string(), "{uri}");
    }
    let (status, body) = get(&f.router, "/api/documents/nope/pages").await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(body, json!({"detail": "document not found"}));
}

#[tokio::test]
async fn without_an_index_full_text_answers_503() {
    let f = fixture(false).await;
    for uri in ["/api/pages?q=Budget", "/api/documents/aaaa/pages"] {
        let (status, body) = get(&f.router, uri).await;
        assert_eq!(status, StatusCode::SERVICE_UNAVAILABLE, "{uri}");
        assert!(body["detail"].as_str().unwrap().contains("not available"));
    }
    // everything else works as before
    let (status, _) = get(&f.router, "/api/documents?q=protokoll").await;
    assert_eq!(status, StatusCode::OK);
}

#[tokio::test]
async fn the_index_is_attached_read_only() {
    let database = temp_path("serving.db");
    let index = temp_path("fulltext.db");
    create(&database, &[SERVING]).await;
    create(&index, &[fulltext_schema()]).await;
    let pool = db::open_read_only(&database, Some(index.as_path()))
        .await
        .unwrap();
    let count: i64 = sqlx::query_scalar("SELECT count(*) FROM fulltext.pages")
        .fetch_one(&pool)
        .await
        .unwrap();
    assert_eq!(count, 0);
    let written = sqlx::query("INSERT INTO fulltext.build VALUES ('x', 'y')")
        .execute(&pool)
        .await;
    assert!(written.is_err());
    pool.close().await;
    for path in [database, index] {
        let _ = std::fs::remove_file(path);
    }
}

/// An MCP request to the router.
fn mcp(body: Value) -> Request<Body> {
    Request::post("/mcp")
        .header(header::HOST, "localhost")
        .header(header::CONTENT_TYPE, "application/json")
        .header(header::ACCEPT, "application/json, text/event-stream")
        .header("mcp-protocol-version", "2025-06-18")
        .body(Body::from(body.to_string()))
        .unwrap()
}

async fn call_tool(router: &Router, name: &str, arguments: Value) -> Value {
    let request = mcp(json!({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments}}));
    let (_, _, body) = send(router, request).await;
    let mut body: Value = serde_json::from_slice(&body).unwrap();
    body["result"].take()
}

#[tokio::test]
async fn mcp_searches_and_reads_the_text_as_the_api_does() {
    let f = fulltext_fixture().await;
    let result = call_tool(
        &f.router,
        "search_text",
        json!({"q": "Steuerfuss", "canton": "ZH"}),
    )
    .await;
    let (_, api) = get(&f.router, "/api/pages?q=Steuerfuss&canton=ZH").await;
    assert_eq!(result["structuredContent"], brief(&api));
    assert_eq!(api["total"], 2);
    // a page: its number, its sections and the snippet; the document's link opens it
    let hit = &result["structuredContent"]["items"][0]["pages"][0];
    assert_eq!(keys(hit), ["page", "section_types", "snippet"]);
    assert!(
        result["structuredContent"]["items"][0]["original_url"]
            .as_str()
            .unwrap()
            .starts_with("https://example.test/original/")
    );
    let result = call_tool(
        &f.router,
        "read_pages",
        json!({"sha256": "aaaa", "from": 11, "to": 12}),
    )
    .await;
    let (_, api) = get(&f.router, "/api/documents/aaaa/pages?from=11&to=12").await;
    assert_eq!(result["structuredContent"], api);
    for (name, arguments) in [
        ("search_text", json!({"q": "ge*"})),
        ("search_text", json!({"q": "Budget", "limit": 51})),
        ("read_pages", json!({"sha256": "aaaa", "from": 1, "to": 20})),
        ("read_pages", json!({"sha256": "nope"})),
    ] {
        let result = call_tool(&f.router, name, arguments.clone()).await;
        assert_eq!(result["isError"], true, "{name} {arguments}");
        let text = result["content"][0]["text"].as_str().unwrap();
        assert!(
            text.starts_with(&format!("Error executing tool {name}: ")),
            "{text}"
        );
    }
    // the instructions say how to use the two tools
    let init = json!({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}});
    let (_, _, body) = send(&f.router, mcp(init)).await;
    let body: Value = serde_json::from_slice(&body).unwrap();
    let instructions = body["result"]["instructions"].as_str().unwrap();
    assert!(instructions.contains("search_text, then read the pages around a hit with read_pages"));
    assert!(instructions.contains("machine-extracted"));
    assert!(instructions.contains("full-width brackets (U+FF3B and U+FF3D, not ASCII [ ])"));

    // without an index both tools answer with an error that says so
    let f = fixture(false).await;
    for (name, arguments) in [
        ("search_text", json!({"q": "Budget"})),
        ("read_pages", json!({"sha256": "aaaa"})),
    ] {
        let result = call_tool(&f.router, name, arguments).await;
        assert_eq!(result["isError"], true);
        assert!(
            result["content"][0]["text"]
                .as_str()
                .unwrap()
                .contains("not available")
        );
    }
}

#[tokio::test]
async fn openapi_describes_the_full_text_endpoints() {
    let f = fulltext_fixture().await;
    let (_, spec) = get(&f.router, "/api/openapi.json").await;
    let fields = |name: &str| keys(&spec["components"]["schemas"][name]["properties"]);
    let (_, page) = get(&f.router, "/api/pages?q=Steuerfuss").await;
    assert_eq!(fields("TextPage"), keys(&page));
    assert_eq!(fields("TextFacets"), keys(&page["facets"]));
    assert_eq!(fields("PageHit"), keys(&page["items"][0]["pages"][0]));
    assert_eq!(fields("CommuneCount"), keys(&page["facets"]["communes"][0]));
    let (_, read) = get(&f.router, "/api/documents/aaaa/pages?from=1&to=1").await;
    assert_eq!(fields("DocumentPages"), keys(&read));
    assert_eq!(fields("PageText"), keys(&read["pages"][0]));
    let parameters = |path: &str| -> Vec<String> {
        spec["paths"][path]["get"]["parameters"]
            .as_array()
            .unwrap()
            .iter()
            .map(|p| p["name"].as_str().unwrap().to_string())
            .collect()
    };
    assert_eq!(
        parameters("/api/pages"),
        [
            "q",
            "commune",
            "canton",
            "year",
            "from",
            "to",
            "language",
            "section",
            "family",
            "exclude_section",
            "exclude_family",
            "body",
            "document",
            "sort",
            "offset",
            "limit",
            "pages_per_document",
            "commune_facets"
        ]
    );
    assert_eq!(
        parameters("/api/documents/{sha256}/pages"),
        ["sha256", "from", "to"]
    );
    let responses = &spec["paths"]["/api/pages"]["get"]["responses"];
    assert!(responses["503"].is_object());
}
