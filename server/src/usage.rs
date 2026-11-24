//! In-house usage statistics in a separate SQLite file: daily aggregates per channel, route
//! template, bot flag and status, kept for good, with the names of recognised crawlers and the
//! domains of the sites that link to a page. No raw paths, query strings, search terms, headers
//! or IP addresses are stored. The daily visitor estimates keep an HMAC of address and browser
//! under a key that is replaced every day; once the day is over its key and its HMACs are deleted
//! and only their number stays. There are two estimates: clients that load a page, and browsers
//! that also run the front end (it asks for `/api/communes` on every page load), which tells
//! people apart from programs that only fetch HTML.
//!
//! `GET /api/usage` reports these counts to the operator: it needs the token in `USAGE_TOKEN`
//! and is not part of the public API.

use std::net::SocketAddr;
use std::path::Path;
use std::time::{Duration, Instant};

use axum::Json;
use axum::extract::{ConnectInfo, MatchedPath, Request, State};
use axum::http::{HeaderMap, HeaderValue, Method, header};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use hmac::{Hmac, KeyInit, Mac};
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use sqlx::SqlitePool;
use sqlx::sqlite::{SqliteConnectOptions, SqlitePoolOptions};

use crate::AppState;
use crate::error::{ApiError, Query};

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS totals (
    day TEXT, channel TEXT, route TEXT, bot INTEGER, status INTEGER,
    requests INTEGER NOT NULL, searches INTEGER NOT NULL,
    empty_searches INTEGER NOT NULL, duration_ms REAL NOT NULL,
    PRIMARY KEY (day, channel, route, bot, status)
);
CREATE TABLE IF NOT EXISTS visitors (day TEXT, token TEXT, PRIMARY KEY (day, token));
CREATE TABLE IF NOT EXISTS daily_keys (day TEXT PRIMARY KEY, secret BLOB);
CREATE TABLE IF NOT EXISTS daily_visitors (day TEXT PRIMARY KEY, visitors INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS browser_visitors (day TEXT, token TEXT, PRIMARY KEY (day, token));
CREATE TABLE IF NOT EXISTS daily_browser_visitors (day TEXT PRIMARY KEY, visitors INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS referrers (
    day TEXT, host TEXT, requests INTEGER NOT NULL, PRIMARY KEY (day, host)
);
CREATE TABLE IF NOT EXISTS bots (
    day TEXT, name TEXT, requests INTEGER NOT NULL, PRIMARY KEY (day, name)
);
";

const REFERRERS: &str = "SELECT day, host, requests FROM referrers WHERE day >= date('now', ?) \
                         ORDER BY day, requests DESC, host";
const BOTS: &str = "SELECT day, name, requests FROM bots WHERE day >= date('now', ?) \
                    ORDER BY day, requests DESC, name";

/// The route whose requests count browsers that run the front end: its layout asks for it on
/// every page load.
const FRONT_END_ROUTE: &str = "/api/communes";

/// A row of `totals`: day, channel, route, bot, status, requests, searches, empty searches and
/// the summed duration in milliseconds.
type TotalsRow = (String, String, String, i64, i64, i64, i64, i64, f64);

/// Set as a response extension by a handler that ran a search: the number of results.
#[derive(Clone, Copy)]
pub struct SearchTotal(pub i64);

/// One counted request.
pub struct Hit {
    pub channel: &'static str,
    pub route: String,
    /// The crawler's name (`bot_name`) when the client is a recognisable automated one.
    pub bot: Option<&'static str>,
    pub status: u16,
    pub duration_ms: f64,
    pub search_total: Option<i64>,
    /// Client address and user agent; hashed, never stored.
    pub visitor: Option<(String, String)>,
    /// The domain of the linking site, for a page opened from a link elsewhere.
    pub referrer: Option<String>,
}

#[derive(Clone)]
pub struct Usage {
    pool: SqlitePool,
    visitors: bool,
    /// SHA-256 of the token `GET /api/usage` asks for; without one the report is off.
    token: Option<Vec<u8>>,
}

impl Usage {
    /// Turn on `GET /api/usage` for the holder of `token`.
    pub fn with_token(mut self, token: Option<&str>) -> Self {
        self.token = token.map(|t| Sha256::digest(t.as_bytes()).to_vec());
        self
    }

    fn authorised(&self, headers: &HeaderMap) -> bool {
        let given = headers
            .get(header::AUTHORIZATION)
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.strip_prefix("Bearer "))
            .map(str::trim);
        // digests are compared, so the time taken tells nothing about the token
        matches!((&self.token, given), (Some(expected), Some(given))
            if Sha256::digest(given.as_bytes()).as_slice() == expected.as_slice())
    }

    pub async fn open(path: &Path, visitors: bool) -> Result<Self, sqlx::Error> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let options = SqliteConnectOptions::new()
            .filename(path)
            .create_if_missing(true)
            .busy_timeout(Duration::from_secs(1))
            .pragma("secure_delete", "ON");
        let pool = SqlitePoolOptions::new()
            .max_connections(1)
            .connect_with(options)
            .await?;
        sqlx::raw_sql(SCHEMA).execute(&pool).await?;
        Ok(Self {
            pool,
            visitors,
            token: None,
        })
    }

    /// The counts of the last `days` days, today included: the rows of `totals`, the visitor
    /// estimates (today's so far), the linking domains and the crawlers.
    pub async fn report(&self, days: i64) -> sqlx::Result<Value> {
        let since = format!("-{} days", days - 1);
        let totals: Vec<TotalsRow> = sqlx::query_as(
            "SELECT day, channel, route, bot, status, requests, searches, empty_searches, \
                 duration_ms FROM totals WHERE day >= date('now', ?) \
                 ORDER BY day, channel, route, bot, status",
        )
        .bind(&since)
        .fetch_all(&self.pool)
        .await?;
        let visitors: Vec<(String, Option<i64>, Option<i64>)> = sqlx::query_as(
            "SELECT day, max(pages), max(browsers) FROM ( \
             SELECT day, visitors AS pages, NULL AS browsers FROM daily_visitors \
             UNION ALL SELECT day, count(*), NULL FROM visitors GROUP BY day \
             UNION ALL SELECT day, NULL, visitors FROM daily_browser_visitors \
             UNION ALL SELECT day, NULL, count(*) FROM browser_visitors GROUP BY day) \
             WHERE day >= date('now', ?) GROUP BY day ORDER BY day",
        )
        .bind(&since)
        .fetch_all(&self.pool)
        .await?;
        let referrers = self.daily_counts(REFERRERS, &since).await?;
        let bots = self.daily_counts(BOTS, &since).await?;
        Ok(json!({
            "totals": totals.into_iter().map(|r| json!({
                "day": r.0, "channel": r.1, "route": r.2, "bot": r.3 != 0, "status": r.4,
                "requests": r.5, "searches": r.6, "empty_searches": r.7, "duration_ms": r.8,
            })).collect::<Vec<_>>(),
            "visitors": visitors.into_iter().map(|r| json!({
                "day": r.0, "pages": r.1, "browsers": r.2,
            })).collect::<Vec<_>>(),
            "referrers": referrers.into_iter().map(|r| json!({
                "day": r.0, "host": r.1, "requests": r.2,
            })).collect::<Vec<_>>(),
            "bots": bots.into_iter().map(|r| json!({
                "day": r.0, "name": r.1, "requests": r.2,
            })).collect::<Vec<_>>(),
        }))
    }

    /// `(day, key, requests)` rows of `REFERRERS` or `BOTS` since `since`.
    async fn daily_counts(
        &self,
        sql: &'static str,
        since: &str,
    ) -> sqlx::Result<Vec<(String, String, i64)>> {
        sqlx::query_as(sql).bind(since).fetch_all(&self.pool).await
    }

    /// Store a hit in the background; a failure is logged and never reaches the request.
    pub fn record(&self, hit: Hit) {
        let pool = self.pool.clone();
        tokio::spawn(async move {
            if write(&pool, hit).await.is_err() {
                // no error details: they may contain environment details
                tracing::warn!("usage statistics could not be stored");
            }
        });
    }
}

async fn write(pool: &SqlitePool, hit: Hit) -> sqlx::Result<()> {
    let mut tx = pool.begin().await?;
    // the first statement writes, so the transaction holds the write lock from the start; a past
    // day's visitors become a number, and its HMACs and key go
    for statement in [
        "INSERT OR IGNORE INTO daily_visitors \
         SELECT day, count(*) FROM visitors WHERE day < date('now') GROUP BY day",
        "DELETE FROM visitors WHERE day < date('now')",
        "INSERT OR IGNORE INTO daily_browser_visitors \
         SELECT day, count(*) FROM browser_visitors WHERE day < date('now') GROUP BY day",
        "DELETE FROM browser_visitors WHERE day < date('now')",
        "DELETE FROM daily_keys WHERE day != date('now')",
    ] {
        sqlx::query(statement).execute(&mut *tx).await?;
    }
    sqlx::query(
        "INSERT INTO totals VALUES (date('now'), ?, ?, ?, ?, 1, ?, ?, ?) \
         ON CONFLICT (day, channel, route, bot, status) DO UPDATE SET \
         requests = requests + 1, searches = searches + excluded.searches, \
         empty_searches = empty_searches + excluded.empty_searches, \
         duration_ms = duration_ms + excluded.duration_ms",
    )
    .bind(hit.channel)
    .bind(&hit.route)
    .bind(hit.bot.is_some())
    .bind(i64::from(hit.status))
    .bind(hit.search_total.is_some())
    .bind(hit.search_total == Some(0))
    .bind(hit.duration_ms)
    .execute(&mut *tx)
    .await?;
    if let Some(name) = hit.bot {
        sqlx::query(
            "INSERT INTO bots VALUES (date('now'), ?, 1) \
             ON CONFLICT (day, name) DO UPDATE SET requests = requests + 1",
        )
        .bind(name)
        .execute(&mut *tx)
        .await?;
    }
    let person = hit.bot.is_none() && (200..300).contains(&hit.status);
    if let Some(host) = hit.referrer.as_deref().filter(|_| person) {
        sqlx::query(
            "INSERT INTO referrers VALUES (date('now'), ?, 1) \
             ON CONFLICT (day, host) DO UPDATE SET requests = requests + 1",
        )
        .bind(host)
        .execute(&mut *tx)
        .await?;
    }
    let insert = match (hit.channel, hit.route.as_str()) {
        ("web", _) => Some("INSERT OR IGNORE INTO visitors VALUES (date('now'), ?)"),
        ("api", FRONT_END_ROUTE) => {
            Some("INSERT OR IGNORE INTO browser_visitors VALUES (date('now'), ?)")
        }
        _ => None,
    };
    if let (Some(visitor), Some(insert), true) = (&hit.visitor, insert, person) {
        sqlx::query("INSERT OR IGNORE INTO daily_keys VALUES (date('now'), randomblob(32))")
            .execute(&mut *tx)
            .await?;
        let key: Vec<u8> =
            sqlx::query_scalar("SELECT secret FROM daily_keys WHERE day = date('now')")
                .fetch_one(&mut *tx)
                .await?;
        sqlx::query(insert)
            .bind(token(&key, visitor))
            .execute(&mut *tx)
            .await?;
    }
    tx.commit().await
}

fn token(key: &[u8], visitor: &(String, String)) -> String {
    let mut mac = <Hmac<Sha256> as KeyInit>::new_from_slice(key).expect("HMAC takes any key");
    mac.update(
        serde_json::to_string(visitor)
            .unwrap_or_default()
            .as_bytes(),
    );
    mac.finalize()
        .into_bytes()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// Recognisable automated clients; a heuristic.
pub fn is_bot(user_agent: &str) -> bool {
    const MARKERS: [&str; 9] = [
        "bot", "crawler", "spider", "slurp", "headless", "curl", "wget", "python", "httpx",
    ];
    let ua = user_agent.to_ascii_lowercase();
    ua.is_empty() || MARKERS.iter().any(|m| ua.contains(m))
}

/// Crawlers counted by name: a marker in the lower-cased user agent, and the name.
const CRAWLERS: [(&str, &str); 39] = [
    ("googlebot", "Googlebot"),
    ("bingbot", "Bingbot"),
    ("applebot", "Applebot"),
    ("duckduckbot", "DuckDuckBot"),
    ("yandex", "YandexBot"),
    ("baiduspider", "Baiduspider"),
    ("slurp", "Yahoo Slurp"),
    ("qwant", "Qwant"),
    ("seznambot", "SeznamBot"),
    ("petalbot", "PetalBot"),
    ("gptbot", "GPTBot"),
    ("oai-searchbot", "OAI-SearchBot"),
    ("claudebot", "ClaudeBot"),
    ("claude-searchbot", "Claude-SearchBot"),
    ("perplexitybot", "PerplexityBot"),
    ("ccbot", "CCBot"),
    ("bytespider", "Bytespider"),
    ("amazonbot", "Amazonbot"),
    ("meta-externalagent", "Meta"),
    ("ahrefsbot", "AhrefsBot"),
    ("semrushbot", "SemrushBot"),
    ("mj12bot", "MJ12bot"),
    ("dotbot", "DotBot"),
    ("dataforseobot", "DataForSeoBot"),
    ("linkedinbot", "LinkedInBot"),
    ("twitterbot", "Twitterbot"),
    ("slackbot", "Slackbot"),
    ("discordbot", "Discordbot"),
    ("telegrambot", "TelegramBot"),
    ("tractandabot", "TractandaBot"),
    ("opencommunebot", "OpenCommuneBot"),
    ("headless", "Headless browser"),
    ("curl", "curl"),
    ("wget", "Wget"),
    ("python", "Python"),
    ("httpx", "httpx"),
    ("go-http-client", "Go"),
    ("java/", "Java"),
    ("node-fetch", "Node"),
];

/// The name an automated client is counted under (`is_bot`), or None for a browser: a known
/// crawler's name, "none" without a user agent, "other" for the rest. The user agent itself is
/// never stored.
pub fn bot_name(user_agent: &str) -> Option<&'static str> {
    if !is_bot(user_agent) {
        return None;
    }
    if user_agent.is_empty() {
        return Some("none");
    }
    let ua = user_agent.to_ascii_lowercase();
    Some(
        CRAWLERS
            .iter()
            .find(|(marker, _)| ua.contains(marker))
            .map_or("other", |(_, name)| name),
    )
}

/// The domain of a `Referer` value, lower case, without user, port or path; None for anything
/// that is not a domain name (an address, `localhost`, a malformed value).
pub fn referrer_host(referer: &str) -> Option<String> {
    let (_, rest) = referer.split_once("://")?;
    let authority = rest.split(['/', '?', '#']).next()?;
    let host = authority.rsplit('@').next()?.split(':').next()?;
    let host = host.trim_end_matches('.').to_ascii_lowercase();
    let name = host.len() <= 253
        && host.contains('.')
        && host
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'.')
        && !host.bytes().all(|b| b.is_ascii_digit() || b == b'.');
    name.then_some(host)
}

/// The route template of a page, without the language prefix; None for anything else (assets,
/// unknown paths).
pub fn page_template(path: &str) -> Option<&'static str> {
    match path {
        "/robots.txt" => return Some("/robots.txt"),
        "/sitemap.xml" => return Some("/sitemap.xml"),
        "/llms.txt" => return Some("/llms.txt"),
        _ => {}
    }
    let rest = ["/de", "/fr", "/it", "/en"]
        .iter()
        .find_map(|l| {
            path.strip_prefix(l)
                .filter(|r| r.is_empty() || r.starts_with('/'))
        })
        .unwrap_or(path);
    match rest {
        "" | "/" => Some("/"),
        "/suche" => Some("/suche"),
        "/ueber-uns" => Some("/ueber-uns"),
        "/datenschutz" => Some("/datenschutz"),
        "/api" => Some("/api"),
        "/lizenzen" => Some("/lizenzen"),
        _ => {
            if let Some(id) = rest.strip_prefix("/gemeinden/") {
                (!id.is_empty() && id.bytes().all(|b| b.is_ascii_digit()))
                    .then_some("/gemeinden/{id}")
            } else if let Some(code) = rest.strip_prefix("/kantone/") {
                (code.len() == 2 && code.bytes().all(|b| b.is_ascii_lowercase()))
                    .then_some("/kantone/{code}")
            } else {
                None
            }
        }
    }
}

/// The channel and route a request is counted under, or None when it is not counted.
fn classify(matched: Option<&str>, path: &str) -> Option<(&'static str, String)> {
    match matched {
        // MCP tools are counted one by one by the MCP handler; the operator's report is not counted
        Some("/mcp") | Some("/api/{*rest}") | Some("/api/usage") | Some("/api/usage/questions") => {
            None
        }
        Some(route) if route.starts_with("/api/") => Some(("api", route.to_string())),
        Some(route @ "/original/{sha256}") => Some(("original", route.to_string())),
        // pages, whether the server renders their facts or only serves the front end
        _ => page_template(path).map(|r| ("web", r.to_string())),
    }
}

/// The client's address: the peer, or behind a reverse proxy on this machine the address it
/// forwards. Forwarded headers from anyone else are ignored.
pub fn client_address(peer: Option<SocketAddr>, headers: &HeaderMap) -> Option<String> {
    let peer = peer?;
    if peer.ip().is_loopback()
        && let Some(forwarded) = headers
            .get("x-forwarded-for")
            .and_then(|v| v.to_str().ok())
            .and_then(|v| v.rsplit(',').next())
            .map(str::trim)
            .filter(|v| !v.is_empty())
    {
        return Some(forwarded.to_string());
    }
    Some(peer.ip().to_string())
}

/// Middleware: count GET requests to API routes, the document redirect and front-end pages.
pub async fn count(State(state): State<AppState>, request: Request, next: Next) -> Response {
    let Some(usage) = state.usage.clone() else {
        return next.run(request).await;
    };
    let matched = request
        .extensions()
        .get::<MatchedPath>()
        .map(|m| m.as_str());
    let counted = if request.method() == Method::GET {
        classify(matched, request.uri().path())
    } else {
        None
    };
    let Some((channel, route)) = counted else {
        return next.run(request).await;
    };
    let headers = request.headers();
    let header = |name: &str| {
        headers
            .get(name)
            .and_then(|v| v.to_str().ok())
            .unwrap_or("")
    };
    let user_agent = header("user-agent").to_string();
    let bot = bot_name(&user_agent);
    let opted_out = header("dnt") == "1" || header("sec-gpc") == "1";
    let person = bot.is_none() && !opted_out;
    // a page from a link on another site
    let referrer = (person && channel == "web")
        .then(|| referrer_host(header("referer")))
        .flatten()
        .filter(|host| !state.site.is_own_host(host));
    let estimated = channel == "web" || (channel == "api" && route == FRONT_END_ROUTE);
    let visitor = if usage.visitors && estimated && person {
        let peer = request
            .extensions()
            .get::<ConnectInfo<SocketAddr>>()
            .map(|c| c.0);
        client_address(peer, headers).map(|address| (address, user_agent))
    } else {
        None
    };
    let started = Instant::now();
    let response = next.run(request).await;
    usage.record(Hit {
        channel,
        route,
        bot,
        status: response.status().as_u16(),
        duration_ms: started.elapsed().as_secs_f64() * 1000.0,
        search_total: response.extensions().get::<SearchTotal>().map(|s| s.0),
        visitor,
        referrer,
    });
    response
}

#[derive(Deserialize)]
pub struct ReportParams {
    days: Option<i64>,
}

/// `GET /api/usage?days=<n>`: the operator's report (`Usage::report`) of the last 30 days, or of
/// `days` (1 to 3660). 404 while no token is set, 401 without the right one.
pub async fn report(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(params): Query<ReportParams>,
) -> Response {
    let Some(usage) = state.usage.as_ref().filter(|u| u.token.is_some()) else {
        return ApiError::not_found("Not Found").into_response();
    };
    if !usage.authorised(&headers) {
        let mut response = ApiError::unauthorized().into_response();
        response
            .headers_mut()
            .insert(header::WWW_AUTHENTICATE, HeaderValue::from_static("Bearer"));
        return response;
    }
    let days = params.days.unwrap_or(30).clamp(1, 3660);
    let mut report = match usage.report(days).await {
        Ok(report) => report,
        Err(error) => return ApiError::from(error).into_response(),
    };
    // the chatbot's questions and dollars per day and outcome
    if let Some(ask) = &state.ask {
        match ask.store.report(days).await {
            Ok(counts) => report["ask"] = counts,
            Err(error) => return ApiError::from(error).into_response(),
        }
    }
    ([(header::CACHE_CONTROL, "no-store")], Json(report)).into_response()
}

/// `GET /api/usage/questions?days=<n>`: the chatbot's questions and answers of the last 7 days, or
/// of `days` (1 to 90), newest first; for the operator, with the token of the usage report.
pub async fn questions(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(params): Query<ReportParams>,
) -> Response {
    let (Some(usage), Some(ask)) = (
        state.usage.as_ref().filter(|u| u.token.is_some()),
        state.ask.as_ref(),
    ) else {
        return ApiError::not_found("Not Found").into_response();
    };
    if !usage.authorised(&headers) {
        let mut response = ApiError::unauthorized().into_response();
        response
            .headers_mut()
            .insert(header::WWW_AUTHENTICATE, HeaderValue::from_static("Bearer"));
        return response;
    }
    match ask
        .store
        .questions(params.days.unwrap_or(7).clamp(1, 90))
        .await
    {
        Ok(rows) => ([(header::CACHE_CONTROL, "no-store")], Json(rows)).into_response(),
        Err(error) => ApiError::from(error).into_response(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pages_are_counted_by_template() {
        assert_eq!(page_template("/"), Some("/"));
        assert_eq!(page_template("/fr"), Some("/"));
        assert_eq!(page_template("/de/gemeinden/261"), Some("/gemeinden/{id}"));
        assert_eq!(page_template("/gemeinden/261"), Some("/gemeinden/{id}"));
        assert_eq!(page_template("/it/suche"), Some("/suche"));
        assert_eq!(page_template("/_app/immutable/x.js"), None);
        assert_eq!(page_template("/dex"), None);
        assert_eq!(page_template("/de/gemeinden/x"), None);
        assert_eq!(page_template("/fr/entscheide/dec-1a"), None);
        assert_eq!(page_template("/llms.txt"), Some("/llms.txt"));
        assert_eq!(page_template("/en"), Some("/"));
        assert_eq!(page_template("/en/gemeinden/261"), Some("/gemeinden/{id}"));
        assert_eq!(page_template("/en/lizenzen"), Some("/lizenzen"));
        assert_eq!(page_template("/fr/api"), Some("/api"));
        assert_eq!(page_template("/it/kantone/ti"), Some("/kantone/{code}"));
        assert_eq!(page_template("/de/kantone/ZH"), None);
        assert_eq!(page_template("/enx"), None);
    }

    #[test]
    fn forwarded_addresses_are_trusted_from_loopback_only() {
        let mut headers = HeaderMap::new();
        headers.insert(
            "x-forwarded-for",
            "203.0.113.9, 198.51.100.7".parse().unwrap(),
        );
        let local = Some("127.0.0.1:5000".parse().unwrap());
        let remote = Some("192.0.2.1:5000".parse().unwrap());
        assert_eq!(
            client_address(local, &headers).as_deref(),
            Some("198.51.100.7")
        );
        assert_eq!(
            client_address(remote, &headers).as_deref(),
            Some("192.0.2.1")
        );
    }

    #[test]
    fn bots_are_recognised() {
        assert!(is_bot(""));
        assert!(is_bot("curl/8.0"));
        assert!(is_bot("Googlebot/2.1"));
        assert!(!is_bot("Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0"));
    }

    #[test]
    fn bots_are_counted_by_name() {
        let firefox = "Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0";
        assert_eq!(bot_name(firefox), None);
        assert_eq!(bot_name(""), Some("none"));
        assert_eq!(
            bot_name("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"),
            Some("Googlebot")
        );
        assert_eq!(
            bot_name("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.2)"),
            Some("GPTBot")
        );
        assert_eq!(
            bot_name("Mozilla/5.0 (compatible; ClaudeBot/1.0; +claudebot@anthropic.com)"),
            Some("ClaudeBot")
        );
        assert_eq!(bot_name("python-requests/2.32"), Some("Python"));
        assert_eq!(bot_name("SomeNewCrawler/0.1"), Some("other"));
        // every name is reachable: no earlier marker hides a later one
        for (marker, name) in CRAWLERS {
            assert_eq!(bot_name(&format!("x {marker} bot")), Some(name), "{marker}");
        }
    }

    #[test]
    fn referrers_keep_the_domain_only() {
        let host = |r: &str| referrer_host(r);
        assert_eq!(
            host("https://www.google.com/").as_deref(),
            Some("www.google.com")
        );
        assert_eq!(
            host("https://User@News.Example.CH:8443/a/b?q=Name#x").as_deref(),
            Some("news.example.ch")
        );
        assert_eq!(
            host("android-app://com.linkedin.android/").as_deref(),
            Some("com.linkedin.android")
        );
        for none in [
            "",
            "google.com",
            "http://localhost:5173/de",
            "http://192.168.1.4/",
            "http://[2001:db8::1]/",
            "https://exa mple.com/",
        ] {
            assert_eq!(host(none), None, "{none}");
        }
    }
}
