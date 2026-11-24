//! HTTP routes, one handler per endpoint, grouped by area. Handlers parse and validate
//! parameters, call one query function in `db` and return JSON.

mod ask;
mod communes;
mod documents;
mod fulltext;
mod openapi;
mod pages;

use axum::Router;
use axum::extract::{Path, State};
use axum::routing::{get, post};

use crate::AppState;
use crate::error::ApiError;
use crate::site::Lang;

pub fn router() -> Router<AppState> {
    let mut router = Router::new()
        .route("/api/communes", get(communes::list))
        .route("/api/communes/{bfs_id}", get(communes::detail))
        .route("/api/register", get(communes::register))
        .route("/api/documents", get(documents::list))
        .route("/api/documents/{sha256}", get(documents::get))
        .route("/api/documents/{sha256}/pages", get(fulltext::pages))
        .route("/api/pages", get(fulltext::search))
        .route("/api/runs", get(documents::runs))
        .route("/api/openapi.json", get(openapi::spec))
        // the operator's usage report: needs a token, not part of the public API
        .route("/api/usage", get(crate::usage::report))
        .route("/api/usage/questions", get(crate::usage::questions))
        // the chatbot
        .route("/api/ask", post(ask::ask))
        .route("/api/ask/challenge", get(ask::challenge))
        .route("/api/{*rest}", get(not_found))
        .route("/original/{sha256}", get(documents::original))
        .route("/robots.txt", get(pages::robots))
        .route("/sitemap.xml", get(pages::sitemap))
        .route("/llms.txt", get(pages::llms))
        .route("/", get(pages::root));
    // the pages crawlers read, in each language; the front end serves every other path
    for lang in Lang::ALL {
        let base = format!("/{}", lang.code());
        router = router
            .route(
                &base,
                get(move |State(s): State<AppState>| pages::home(s, lang)),
            )
            .route(
                &format!("{base}/gemeinden/{{id}}"),
                get(move |State(s): State<AppState>, Path(id): Path<String>| {
                    pages::commune(s, lang, id)
                }),
            )
            .route(
                &format!("{base}/ueber-uns"),
                get(move |State(s): State<AppState>| pages::about(s, lang)),
            )
            .route(
                &format!("{base}/datenschutz"),
                get(move |State(s): State<AppState>| pages::privacy(s, lang)),
            )
            .route(
                &format!("{base}/suche"),
                get(move |State(s): State<AppState>| pages::search(s, lang)),
            )
            .route(
                &format!("{base}/api"),
                get(move |State(s): State<AppState>| pages::api(s, lang)),
            )
            .route(
                &format!("{base}/lizenzen"),
                get(move |State(s): State<AppState>| pages::licences(s, lang)),
            )
            .route(
                &format!("{base}/{{*rest}}"),
                get(move |State(s): State<AppState>, Path(rest): Path<String>| {
                    pages::missing(s, lang, rest)
                }),
            );
    }
    router
}

async fn not_found() -> ApiError {
    ApiError::not_found("Not Found")
}
