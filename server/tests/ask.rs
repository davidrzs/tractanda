//! The chatbot (`/api/ask`) against a scripted model: the model's tool calls run on the real tools,
//! in-process, over a small serving database. No request leaves the machine.

use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

use axum::Router;
use axum::body::{Body, to_bytes};
use axum::http::{Request, StatusCode, header};
use opencommune_server::ask::challenge::solve;
use opencommune_server::ask::{Ask, Settings};
use opencommune_server::site::Site;
use opencommune_server::usage::Usage;
use opencommune_server::{AppState, app, db, mcp};
use rig_core::completion::{GenerationOptions, Usage as Tokens};
use rig_core::message::{AssistantContent, ToolName};
use rig_core::test_utils::{MockCompletionModel, MockTurn};
use serde_json::{Value, json};
use sqlx::sqlite::{SqliteConnectOptions, SqlitePool};
use tower::ServiceExt;

const SERVING: &str = include_str!("../../src/opencommune/serving.sql");

const ROWS: &str = r#"
INSERT INTO communes VALUES
  (261, 'Zürich', 'ZH', 'de', 'https://www.stadt-zuerich.ch', 'custom', NULL, 47.37, 8.54);
INSERT INTO runs VALUES
  ('classify-aaaa', 'classification', 'model-a', '{}', 'A note.', 1);
INSERT INTO documents (sha256, title, body_name, first_seen, listing_date, page_count, run_id,
  kind, body_kind, date, years, year, languages, personal_data, pages_seen) VALUES
  ('aaaa', 'Protokoll Gemeindeversammlung 2024', 'Gemeindeversammlung Zürich', '2026-09-06',
   '2024-06-20', 12, 'classify-aaaa', 'meeting_booklet', 'citizens_assembly', '2024-06-12', '2024',
   2024, 'de', 0, 12);
INSERT INTO document_communes VALUES ('aaaa', 261, 'https://www.stadt-zuerich.ch/p.pdf');
"#;

const TOKEN: &str = "usage-token-0123456789abcdef0123456789";
const ORIGIN: &str = "https://example.test";
const BROWSER: &str = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0";
/// Dollars per million tokens in the tests, so that costs are easy to check.
const PRICE_INPUT: f64 = 1.0;
const PRICE_OUTPUT: f64 = 10.0;
const PRICE_INPUT_LONG: f64 = 5.0;
const PRICE_OUTPUT_LONG: f64 = 50.0;

fn temp_path(name: &str) -> PathBuf {
    static N: AtomicUsize = AtomicUsize::new(0);
    let n = N.fetch_add(1, Ordering::Relaxed);
    std::env::temp_dir().join(format!(
        "opencommune-ask-test-{}-{n}-{name}",
        std::process::id()
    ))
}

struct Fixture {
    router: Router,
    ask: Arc<Ask>,
    files: Vec<PathBuf>,
}

impl Drop for Fixture {
    fn drop(&mut self) {
        for f in &self.files {
            for suffix in ["", "-wal", "-shm", "-journal"] {
                let _ = std::fs::remove_file(format!("{}{suffix}", f.display()));
            }
            let _ = std::fs::remove_dir_all(f);
        }
    }
}

fn tokens(input: u64, output: u64) -> Tokens {
    Tokens::new().input_tokens(input).output_tokens(output)
}

async fn fixture(turns: Vec<MockTurn>, change: impl FnOnce(&mut Settings)) -> Fixture {
    let database = temp_path("serving.db");
    let options = SqliteConnectOptions::new()
        .filename(&database)
        .create_if_missing(true);
    let pool = SqlitePool::connect_with(options).await.unwrap();
    sqlx::raw_sql(SERVING).execute(&pool).await.unwrap();
    sqlx::raw_sql(ROWS).execute(&pool).await.unwrap();
    pool.close().await;
    let static_dir = temp_path("static");
    std::fs::create_dir_all(&static_dir).unwrap();
    std::fs::write(
        static_dir.join("index.html"),
        "<!doctype html><html></html>",
    )
    .unwrap();
    let usage_path = temp_path("usage.sqlite3");
    let ask_path = temp_path("ask.sqlite3");
    let mut state = AppState {
        db: db::open_read_only(&database, None).await.unwrap(),
        fulltext: false,
        usage: Some(
            Usage::open(&usage_path, true)
                .await
                .unwrap()
                .with_token(Some(TOKEN)),
        ),
        site: Site::new(ORIGIN, false, false, &static_dir),
        ask: None,
    };
    let tools = mcp::chat_tools(state.clone()).await.unwrap();
    let mut settings = Settings {
        model: "mock".into(),
        effort: "low".into(),
        price_input: PRICE_INPUT,
        price_output: PRICE_OUTPUT,
        price_input_long: PRICE_INPUT_LONG,
        price_output_long: PRICE_OUTPUT_LONG,
        daily_usd: 1.0,
        model_calls_per_minute: None,
        off_file: None,
        path: ask_path.clone(),
    };
    change(&mut settings);
    let ask = Ask::open(
        MockCompletionModel::from_turns(turns),
        tools,
        GenerationOptions::new(),
        settings,
    )
    .await
    .unwrap();
    state.ask = Some(ask.clone());
    Fixture {
        router: app(state, &static_dir, &["localhost".to_string()]),
        ask,
        files: vec![database, usage_path, ask_path, static_dir],
    }
}

async fn send(router: &Router, request: Request<Body>) -> (StatusCode, String) {
    let response = router.clone().oneshot(request).await.unwrap();
    let status = response.status();
    let body = to_bytes(response.into_body(), usize::MAX).await.unwrap();
    (status, String::from_utf8(body.to_vec()).unwrap())
}

async fn challenge(router: &Router) -> String {
    let request = Request::get("/api/ask/challenge")
        .header(header::USER_AGENT, BROWSER)
        .body(Body::empty())
        .unwrap();
    let (status, body) = send(router, request).await;
    assert_eq!(status, StatusCode::OK, "{body}");
    let body: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(body["bits"], 16);
    body["challenge"].as_str().unwrap().to_string()
}

fn question(body: Value, origin: Option<&str>, agent: &str) -> Request<Body> {
    let mut request = Request::post("/api/ask")
        .header(header::USER_AGENT, agent)
        .header(header::CONTENT_TYPE, "application/json");
    if let Some(origin) = origin {
        request = request.header(header::ORIGIN, origin);
    }
    request.body(Body::from(body.to_string())).unwrap()
}

/// Ask `text` as a browser on the site would, with a solved challenge.
async fn ask(router: &Router, text: &str) -> (StatusCode, String) {
    let challenge = challenge(router).await;
    let solution = solve(&challenge);
    let body =
        json!({"question": text, "lang": "de", "challenge": challenge, "solution": solution});
    send(router, question(body, Some(ORIGIN), BROWSER)).await
}

/// The server-sent events of a body: (event, data).
fn events(body: &str) -> Vec<(String, Value)> {
    body.split("\n\n")
        .filter_map(|block| {
            let mut event = None;
            let mut data = None;
            for line in block.lines() {
                if let Some(e) = line.strip_prefix("event: ") {
                    event = Some(e.to_string());
                } else if let Some(d) = line.strip_prefix("data: ") {
                    data = serde_json::from_str(d).ok();
                }
            }
            Some((event?, data?))
        })
        .collect()
}

fn reason(body: &str) -> String {
    let body: Value = serde_json::from_str(body).unwrap();
    body["reason"].as_str().unwrap().to_string()
}

#[tokio::test]
async fn a_question_is_answered_with_its_steps_and_kept_without_the_asker() {
    let fixture = fixture(
        vec![
            MockTurn::tool_call("c1", "list_communes", json!({"q": "zurich"}))
                .with_usage(tokens(1000, 100)),
            MockTurn::text("Zürich hat 2024 getagt ([Protokoll, S. 1](https://example.test/original/aaaa?page=1)).")
                .with_usage(tokens(2000, 200)),
        ],
        |_| {},
    )
    .await;
    let (status, body) = ask(&fixture.router, "Was hat Zürich 2024 beschlossen?").await;
    assert_eq!(status, StatusCode::OK, "{body}");
    let events = events(&body);
    let names: Vec<&str> = events.iter().map(|(e, _)| e.as_str()).collect();
    assert_eq!(names, ["step", "answer", "end"]);
    assert_eq!(events[0].1["tool"], "list_communes");
    assert_eq!(events[0].1["args"]["q"], "zurich");
    assert!(events[1].1["text"].as_str().unwrap().contains("?page=1"));
    assert_eq!(events[2].1["outcome"], "answered");

    let kept = fixture.ask.store.questions(1).await.unwrap();
    let kept = kept.as_array().unwrap();
    assert_eq!(kept.len(), 1);
    let entry = &kept[0];
    assert_eq!(entry["question"], "Was hat Zürich 2024 beschlossen?");
    assert_eq!(entry["lang"], "de");
    assert_eq!(entry["outcome"], "answered");
    assert_eq!(entry["model_calls"], 2);
    assert_eq!(entry["input_tokens"], 3000);
    assert_eq!(entry["output_tokens"], 300);
    assert_eq!(entry["steps"][0]["tool"], "list_communes");
    let usd = (3000.0 * PRICE_INPUT + 300.0 * PRICE_OUTPUT) / 1e6;
    assert!((entry["usd"].as_f64().unwrap() - usd).abs() < 1e-12);
    // nothing in the entry identifies the asker
    let fields: Vec<&str> = entry
        .as_object()
        .unwrap()
        .keys()
        .map(String::as_str)
        .collect();
    assert_eq!(
        fields,
        [
            "asked_at",
            "lang",
            "question",
            "answer",
            "steps",
            "outcome",
            "prompt_version",
            "model",
            "model_calls",
            "input_tokens",
            "output_tokens",
            "usd",
            "duration_ms"
        ]
    );
    assert!((fixture.ask.spent() - usd).abs() < 1e-12);
}

#[tokio::test]
async fn the_operator_reads_questions_and_counts_with_the_usage_token_only() {
    let fixture = fixture(vec![MockTurn::text("Nichts gefunden.")], |_| {}).await;
    let (status, _) = ask(&fixture.router, "Gibt es etwas zu Tempo 30?").await;
    assert_eq!(status, StatusCode::OK);
    let anonymous = Request::get("/api/usage/questions")
        .body(Body::empty())
        .unwrap();
    assert_eq!(
        send(&fixture.router, anonymous).await.0,
        StatusCode::UNAUTHORIZED
    );
    let authorised = |uri: &str| {
        Request::get(uri)
            .header(header::AUTHORIZATION, format!("Bearer {TOKEN}"))
            .body(Body::empty())
            .unwrap()
    };
    let (status, body) = send(&fixture.router, authorised("/api/usage/questions")).await;
    assert_eq!(status, StatusCode::OK);
    let rows: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(rows[0]["question"], "Gibt es etwas zu Tempo 30?");
    let (status, body) = send(&fixture.router, authorised("/api/usage?days=1")).await;
    assert_eq!(status, StatusCode::OK);
    let report: Value = serde_json::from_str(&body).unwrap();
    assert_eq!(report["ask"][0]["outcome"], "answered");
    assert_eq!(report["ask"][0]["questions"], 1);
}

#[tokio::test]
async fn questions_without_the_site_a_browser_a_fitting_length_or_a_solved_challenge_are_refused() {
    let fixture = fixture(vec![MockTurn::text("unused")], |_| {}).await;
    let router = &fixture.router;
    let body = |text: &str, challenge: &str, solution: &str| json!({"question": text, "challenge": challenge, "solution": solution});

    let c = challenge(router).await;
    let s = solve(&c);
    let (status, response) = send(router, question(body("Tempo 30?", &c, &s), None, BROWSER)).await;
    assert_eq!(
        (status, reason(&response).as_str()),
        (StatusCode::FORBIDDEN, "origin")
    );
    let (status, response) = send(
        router,
        question(
            body("Tempo 30?", &c, &s),
            Some("https://elsewhere.test"),
            BROWSER,
        ),
    )
    .await;
    assert_eq!(
        (status, reason(&response).as_str()),
        (StatusCode::FORBIDDEN, "origin")
    );
    let (status, response) = send(
        router,
        question(body("Tempo 30?", &c, &s), Some(ORIGIN), "curl/8.5"),
    )
    .await;
    assert_eq!(
        (status, reason(&response).as_str()),
        (StatusCode::FORBIDDEN, "client")
    );
    let (status, response) =
        send(router, question(body("ab", &c, &s), Some(ORIGIN), BROWSER)).await;
    assert_eq!(
        (status, reason(&response).as_str()),
        (StatusCode::BAD_REQUEST, "short")
    );
    let long = "x".repeat(501);
    let (status, response) =
        send(router, question(body(&long, &c, &s), Some(ORIGIN), BROWSER)).await;
    assert_eq!(
        (status, reason(&response).as_str()),
        (StatusCode::BAD_REQUEST, "long")
    );
    let (status, response) = send(
        router,
        question(body("Tempo 30?", &c, "1"), Some(ORIGIN), BROWSER),
    )
    .await;
    assert_eq!(
        (status, reason(&response).as_str()),
        (StatusCode::FORBIDDEN, "challenge")
    );
    let (status, response) = send(
        router,
        question(
            body("Tempo 30?", "made.up.challenge", "1"),
            Some(ORIGIN),
            BROWSER,
        ),
    )
    .await;
    assert_eq!(
        (status, reason(&response).as_str()),
        (StatusCode::FORBIDDEN, "challenge")
    );
    // a solved challenge counts once
    let (status, _) = send(
        router,
        question(body("Tempo 30?", &c, &s), Some(ORIGIN), BROWSER),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    // (another question: the first one's answer is now kept and needs no challenge)
    let (status, response) = send(
        router,
        question(body("Tempo 40?", &c, &s), Some(ORIGIN), BROWSER),
    )
    .await;
    assert_eq!(
        (status, reason(&response).as_str()),
        (StatusCode::FORBIDDEN, "challenge")
    );
    // crawlers get no challenge
    let crawler = Request::get("/api/ask/challenge")
        .header(header::USER_AGENT, "Googlebot/2.1")
        .body(Body::empty())
        .unwrap();
    assert_eq!(send(router, crawler).await.0, StatusCode::FORBIDDEN);
    // refusals are counted, without the question, in memory until they are written
    fixture.ask.flush().await;
    let report = fixture.ask.store.report(1).await.unwrap();
    let outcomes: Vec<&str> = report
        .as_array()
        .unwrap()
        .iter()
        .map(|r| r["outcome"].as_str().unwrap())
        .collect();
    assert!(outcomes.contains(&"refused:origin"));
    assert!(outcomes.contains(&"refused:challenge"));
    assert_eq!(
        fixture
            .ask
            .store
            .questions(1)
            .await
            .unwrap()
            .as_array()
            .unwrap()
            .len(),
        1
    );
}

#[tokio::test]
async fn the_off_file_and_the_daily_dollars_stop_the_chatbot() {
    let off = temp_path("off");
    let fixture = fixture(vec![MockTurn::text("unused")], |s| {
        s.off_file = Some(off.clone());
    })
    .await;
    std::fs::write(&off, "").unwrap();
    let request = Request::get("/api/ask/challenge")
        .header(header::USER_AGENT, BROWSER)
        .body(Body::empty())
        .unwrap();
    let (status, body) = send(&fixture.router, request).await;
    assert_eq!(
        (status, reason(&body).as_str()),
        (StatusCode::SERVICE_UNAVAILABLE, "off")
    );
    std::fs::remove_file(&off).unwrap();
    assert_eq!(ask(&fixture.router, "Tempo 30?").await.0, StatusCode::OK);

    let fixture = fixture_spent().await;
    let (status, body) = ask(&fixture.router, "Tempo 30?").await;
    assert_eq!(
        (status, reason(&body).as_str()),
        (StatusCode::TOO_MANY_REQUESTS, "used_up")
    );
}

async fn fixture_spent() -> Fixture {
    fixture(vec![MockTurn::text("unused")], |s| s.daily_usd = 0.0).await
}

#[tokio::test]
async fn a_question_that_needs_too_many_model_calls_ends_without_an_answer() {
    let turns = (0..10)
        .map(|n| {
            MockTurn::tool_call(format!("c{n}"), "list_communes", json!({"q": "z"}))
                .with_usage(tokens(100, 10))
        })
        .collect();
    let fixture = fixture(turns, |_| {}).await;
    let (status, body) = ask(&fixture.router, "Alles über alle Gemeinden?").await;
    assert_eq!(status, StatusCode::OK);
    let events = events(&body);
    assert!(events.iter().all(|(e, _)| e != "answer"));
    assert_eq!(events.last().unwrap().1["outcome"], "too_broad");
    let kept = fixture.ask.store.questions(1).await.unwrap();
    assert_eq!(kept[0]["outcome"], "too_broad");
    assert_eq!(kept[0]["answer"], Value::Null);
}

#[tokio::test]
async fn pages_beyond_the_limit_are_not_read() {
    // seven readings of ten pages in one turn: each is cut to five, and the seventh would pass 30
    let name = ToolName::new("read_pages").unwrap();
    let calls = (0..7u64).map(|n| {
        let from = 1 + n * 10;
        AssistantContent::tool_call(
            format!("r{n}"),
            name.clone(),
            json!({"sha256": "aaaa", "from": from, "to": from + 9}),
        )
    });
    let fixture = fixture(
        vec![
            MockTurn::from_contents(calls).with_usage(tokens(100, 10)),
            MockTurn::text("Mehr steht nicht da.").with_usage(tokens(100, 10)),
        ],
        |_| {},
    )
    .await;
    let (status, body) = ask(&fixture.router, "Was steht im Protokoll?").await;
    assert_eq!(status, StatusCode::OK);
    let steps: Vec<Value> = events(&body)
        .into_iter()
        .filter(|(e, _)| e == "step")
        .map(|(_, d)| d)
        .collect();
    assert_eq!(steps.len(), 6);
    for step in &steps {
        let from = step["args"]["from"].as_u64().unwrap();
        assert_eq!(step["args"]["to"].as_u64().unwrap(), from + 4);
    }
}

#[tokio::test]
async fn questions_older_than_ninety_days_are_deleted() {
    let fixture = fixture(
        vec![MockTurn::text("Eins."), MockTurn::text("Zwei.")],
        |_| {},
    )
    .await;
    assert_eq!(ask(&fixture.router, "Erste Frage?").await.0, StatusCode::OK);
    fixture.ask.store.age_for_test(91).await.unwrap();
    assert_eq!(
        ask(&fixture.router, "Zweite Frage?").await.0,
        StatusCode::OK
    );
    let kept = fixture.ask.store.questions(1000).await.unwrap();
    let kept = kept.as_array().unwrap();
    assert_eq!(kept.len(), 1);
    assert_eq!(kept[0]["question"], "Zweite Frage?");
}

#[tokio::test]
async fn an_example_search_is_answered_again_without_the_model_or_a_limit_others_are_not_kept() {
    // the model answers twice: a third call would find no turn left and fail
    let fixture = fixture(
        vec![
            MockTurn::text("Tempo 30 in Port ([S. 15](/original/aaaa?page=15)).")
                .with_usage(tokens(100, 10)),
            MockTurn::text("Eine Antwort für eine eigene Frage.").with_usage(tokens(100, 10)),
        ],
        |_| {},
    )
    .await;
    let router = &fixture.router;
    // "Tempo 30" is one of the start page's examples: kept and given again
    assert_eq!(ask(router, "Tempo 30").await.0, StatusCode::OK);
    let (status, body) = ask(router, "  tempo   30 ?").await;
    assert_eq!(status, StatusCode::OK);
    let replayed = events(&body);
    assert_eq!(
        replayed[0].1["text"],
        "Tempo 30 in Port ([S. 15](/original/aaaa?page=15))."
    );
    assert_eq!(replayed[1].1["outcome"], "answered");
    // a question of one's own is not kept: asking it again goes to the model (and finds no turn)
    assert_eq!(
        ask(router, "Was hat Familie Muster gefragt?").await.0,
        StatusCode::OK
    );
    let (status, body) = ask(router, "Was hat Familie Muster gefragt?").await;
    assert_eq!(status, StatusCode::OK);
    assert_ne!(events(&body).last().unwrap().1["outcome"], "answered");
    // a kept answer needs the proof of work too
    let unsolved =
        json!({"question": "Tempo 30", "lang": "de", "challenge": "x.1.y", "solution": "1"});
    let (status, body) = send(router, question(unsolved, Some(ORIGIN), BROWSER)).await;
    assert_eq!(
        (status, reason(&body).as_str()),
        (StatusCode::FORBIDDEN, "challenge")
    );
    // the repeat was counted, not logged
    fixture.ask.flush().await;
    let report = fixture.ask.store.report(1).await.unwrap();
    let cached = report
        .as_array()
        .unwrap()
        .iter()
        .find(|r| r["outcome"] == "cached")
        .unwrap()
        .clone();
    assert_eq!(cached["questions"], 1);
}

#[tokio::test]
async fn the_chatbot_searches_with_smaller_maxima() {
    let fixture = fixture(
        vec![
            MockTurn::tool_call(
                "s1",
                "search_text",
                json!({"q": "Gemeindeversammlung", "limit": 50, "pages_per_document": 20}),
            ),
            MockTurn::text("Nichts."),
        ],
        |_| {},
    )
    .await;
    let (status, body) = ask(&fixture.router, "Was gab es an Versammlungen?").await;
    assert_eq!(status, StatusCode::OK);
    let step = &events(&body)[0];
    assert_eq!(step.0, "step");
    assert_eq!(step.1["args"]["limit"], 30);
    assert_eq!(step.1["args"]["pages_per_document"], 2);
}

#[tokio::test]
async fn a_call_without_usage_counts_as_the_most_a_call_can_cost() {
    let fixture = fixture(vec![MockTurn::text("Ohne Zahlen.")], |_| {}).await;
    assert_eq!(
        ask(&fixture.router, "Wie viel kostet das?").await.0,
        StatusCode::OK
    );
    let kept = fixture.ask.store.questions(1).await.unwrap();
    // 150,000 input tokens (long-context prices) and 4,000 output tokens
    let worst = (150_000.0 * PRICE_INPUT_LONG + 4_000.0 * PRICE_OUTPUT_LONG) / 1e6;
    assert!((kept[0]["usd"].as_f64().unwrap() - worst).abs() < 1e-9);
}

#[tokio::test]
async fn no_other_site_may_frame_the_pages() {
    let fixture = fixture(vec![MockTurn::text("unused")], |_| {}).await;
    let request = Request::get("/de/suche?q=Tempo+30")
        .body(Body::empty())
        .unwrap();
    let response = fixture.router.clone().oneshot(request).await.unwrap();
    let headers = response.headers();
    assert_eq!(headers["content-security-policy"], "frame-ancestors 'self'");
    assert_eq!(headers["x-frame-options"], "SAMEORIGIN");
}

#[tokio::test]
async fn when_the_money_runs_out_answers_pause_and_kept_ones_are_still_given() {
    let fixture = fixture(
        vec![
            MockTurn::text("Das war noch bezahlt.").with_usage(tokens(100, 10)),
            MockTurn::provider_response_error(
                StatusCode::BAD_REQUEST,
                r#"{"type":"error","error":{"type":"invalid_request_error","message":"You have reached your specified API usage limits. You will regain access on 2026-12-01 at 00:00 UTC."}}"#,
                "req_1",
            ),
        ],
        |_| {},
    )
    .await;
    let router = &fixture.router;
    assert_eq!(ask(router, "Tempo 30").await.0, StatusCode::OK);
    // the provider refuses: the question ends calmly, and answers pause
    let (status, body) = ask(router, "Zweite Frage?").await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(events(&body).last().unwrap().1["outcome"], "no_credit");
    assert!(fixture.ask.paused());
    let (status, body) = ask(router, "Dritte Frage?").await;
    assert_eq!(
        (status, reason(&body).as_str()),
        (StatusCode::TOO_MANY_REQUESTS, "no_credit")
    );
    // an example answered before is still given
    let (status, body) = ask(router, "tempo 30").await;
    assert_eq!(status, StatusCode::OK);
    assert!(body.contains("Das war noch bezahlt."));
    let kept = fixture.ask.store.questions(1).await.unwrap();
    assert_eq!(kept[0]["outcome"], "no_credit");
}

#[tokio::test]
async fn an_off_file_the_service_cannot_see_counts_as_off() {
    use std::os::unix::fs::PermissionsExt;
    let folder = temp_path("offdir");
    std::fs::create_dir_all(&folder).unwrap();
    let off = folder.join("ask-off");
    let fixture = fixture(vec![MockTurn::text("unused")], |s| {
        s.off_file = Some(off.clone());
    })
    .await;
    assert!(fixture.ask.is_on());
    // a folder the service may not enter: whether the file is there cannot be told
    std::fs::set_permissions(&folder, std::fs::Permissions::from_mode(0o000)).unwrap();
    let readable = std::fs::read_dir(&folder).is_ok(); // root reads it anyway
    let on = fixture.ask.is_on();
    std::fs::set_permissions(&folder, std::fs::Permissions::from_mode(0o700)).unwrap();
    let _ = std::fs::remove_dir_all(&folder);
    if !readable {
        assert!(!on);
    }
}
