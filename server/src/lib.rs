//! Tractanda web server: the JSON API, MCP over streamable HTTP, the static front end and the
//! pages crawlers read, all from the serving database the Python package builds, and full-text
//! search from its index of page text when there is one. See `API.md` and `CONVENTIONS.md`.

pub mod ask;
pub mod config;
pub mod db;
pub mod error;
pub mod mcp;
pub mod models;
pub mod routes;
pub mod site;
pub mod usage;

use std::path::Path;

use axum::Router;
use axum::extract::{MatchedPath, Request};
use axum::http::{HeaderName, HeaderValue, header};
use axum::middleware;
use sqlx::SqlitePool;
use tower_http::compression::CompressionLayer;
use tower_http::services::{ServeDir, ServeFile};
use tower_http::set_header::SetResponseHeaderLayer;
use tower_http::timeout::RequestBodyTimeoutLayer;
use tower_http::trace::{DefaultOnResponse, TraceLayer};
use tracing::Level;

use crate::site::Site;
use crate::usage::Usage;

#[derive(Clone)]
pub struct AppState {
    /// The serving database, read-only.
    pub db: SqlitePool,
    /// Whether every connection of `db` has the full-text index attached (schema `fulltext`).
    pub fulltext: bool,
    /// Usage statistics, when enabled.
    pub usage: Option<Usage>,
    /// The public address, the indexing switch and the front end's HTML for the crawled pages.
    pub site: Site,
    /// The chatbot, when a model is configured (`ASK_API_KEY`).
    pub ask: Option<std::sync::Arc<ask::Ask>>,
}

/// The whole site: API routes, `/mcp`, and the front end with `index.html` for every other path
/// (the single-page app routes on the client).
pub fn app(state: AppState, static_dir: &Path, mcp_allowed_hosts: &[String]) -> Router {
    let index = ServeFile::new(static_dir.join("index.html"));
    let assets = ServeDir::new(static_dir).fallback(index);
    // file names under _app/immutable carry a content hash
    let immutable = Router::new()
        .fallback_service(ServeDir::new(static_dir.join("_app/immutable")))
        .layer(SetResponseHeaderLayer::overriding(
            header::CACHE_CONTROL,
            HeaderValue::from_static("public, max-age=31536000, immutable"),
        ));
    let trace = TraceLayer::new_for_http()
        .make_span_with(|request: &Request| {
            // the route template only: no raw path, query string or address
            let route = request
                .extensions()
                .get::<MatchedPath>()
                .map_or("static", MatchedPath::as_str);
            tracing::info_span!("request", method = %request.method(), route)
        })
        .on_response(DefaultOnResponse::new().level(Level::DEBUG));
    let indexing = state.site.indexing;
    let router = Router::new()
        .merge(routes::router())
        .route_service("/mcp", mcp::service(state.clone(), mcp_allowed_hosts))
        .nest("/_app/immutable", immutable)
        .fallback_service(assets)
        .layer(middleware::from_fn_with_state(state.clone(), usage::count))
        .layer(middleware::from_fn_with_state(
            state.clone(),
            site::redirects,
        ))
        .layer(trace)
        .layer(CompressionLayer::new())
        // no other site may show these pages in a frame (and make its visitors ask the chatbot)
        .layer(SetResponseHeaderLayer::if_not_present(
            header::CONTENT_SECURITY_POLICY,
            HeaderValue::from_static("frame-ancestors 'self'"),
        ))
        .layer(SetResponseHeaderLayer::if_not_present(
            header::X_FRAME_OPTIONS,
            HeaderValue::from_static("SAMEORIGIN"),
        ))
        // a request body that trickles in does not hold a connection for long
        .layer(RequestBodyTimeoutLayer::new(
            std::time::Duration::from_secs(10),
        ));
    // until SEARCH_INDEXING is set, the site asks search engines not to index anything
    let router = if indexing {
        router
    } else {
        router.layer(SetResponseHeaderLayer::if_not_present(
            HeaderName::from_static("x-robots-tag"),
            HeaderValue::from_static("noindex, nofollow"),
        ))
    };
    router.with_state(state)
}
