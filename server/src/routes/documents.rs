//! `/api/documents`, `/api/documents/{sha256}`, `/api/runs`, `/api/releases` and the
//! `/original/{sha256}` redirect.

use axum::Json;
use axum::extract::State;
use axum::http::{HeaderValue, StatusCode, header};
use axum::response::{IntoResponse, Response};
use rmcp::schemars::JsonSchema;
use serde::Deserialize;

use crate::AppState;
use crate::db::documents::{self, Search};
use crate::error::{ApiError, ApiResult, Path, Query};
use crate::models::{DocumentDetail, Releases, Run};
use crate::usage::SearchTotal;

pub async fn list(
    State(state): State<AppState>,
    Query(search): Query<Search>,
) -> Result<Response, ApiError> {
    search.check().map_err(ApiError::invalid)?;
    let page = documents::search(&state.db, &search, &state.site.public_url).await?;
    let total = page.total;
    let mut response = Json(page).into_response();
    if search.is_search() {
        response.extensions_mut().insert(SearchTotal(total));
    }
    Ok(response)
}

pub async fn get(
    State(state): State<AppState>,
    Path(sha256): Path<String>,
) -> ApiResult<DocumentDetail> {
    documents::get(&state.db, &sha256, &state.site.public_url)
        .await?
        .map(Json)
        .ok_or_else(|| ApiError::not_found("document not found"))
}

pub async fn runs(State(state): State<AppState>) -> ApiResult<Vec<Run>> {
    Ok(Json(documents::runs(&state.db).await?))
}

pub async fn releases(State(state): State<AppState>) -> ApiResult<Releases> {
    Ok(Json(documents::releases(&state.db, state.fulltext).await?))
}

#[derive(Deserialize, JsonSchema)]
#[schemars(crate = "rmcp::schemars")]
pub struct OriginalParams {
    /// A commune's BFS number: that commune's link, when it has one.
    commune: Option<i64>,
    /// Open the file at this page (`#page=`, which PDF viewers follow).
    page: Option<u32>,
}

/// Redirect to a document on a commune's site.
pub async fn original(
    State(state): State<AppState>,
    Path(sha256): Path<String>,
    Query(p): Query<OriginalParams>,
) -> Result<Response, ApiError> {
    let url = documents::original(&state.db, &sha256, p.commune)
        .await?
        .ok_or_else(|| ApiError::not_found("document not found"))?;
    let url = match p.page.filter(|page| *page > 0) {
        Some(page) => format!("{}#page={page}", url.split('#').next().unwrap_or(&url)),
        None => url,
    };
    let location = HeaderValue::from_str(&quote(&url))
        .map_err(|_| ApiError::not_found("document not found"))?;
    Ok((
        StatusCode::FOUND,
        [
            (header::LOCATION, location),
            (
                header::HeaderName::from_static("x-robots-tag"),
                HeaderValue::from_static("noindex, nofollow"),
            ),
        ],
    )
        .into_response())
}

/// Percent-encodes what a `Location` header cannot carry, keeping URL syntax characters
/// (`:/%#?=@[]!$&'()*+,;`) as they are.
fn quote(url: &str) -> String {
    const SAFE: &[u8] = b":/%#?=@[]!$&'()*+,;_.-~";
    let mut out = String::with_capacity(url.len());
    for &b in url.as_bytes() {
        if b.is_ascii_alphanumeric() || SAFE.contains(&b) {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::quote;

    #[test]
    fn locations_are_percent_encoded() {
        assert_eq!(
            quote("https://a.ch/Gemeinderat Protokoll ä.pdf?x=1&y=2#p"),
            "https://a.ch/Gemeinderat%20Protokoll%20%C3%A4.pdf?x=1&y=2#p"
        );
    }
}
