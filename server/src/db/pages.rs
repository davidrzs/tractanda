//! What the crawler-facing pages read: the archive's totals, the communes and cantons with
//! documents, the sitemap's communes and the facts of a commune or canton page.

use sqlx::SqlitePool;

use crate::db::{communes, documents};
use crate::models::{BriefCommune, CommuneDetail, CommuneRef, DocumentPage, PageTotals};

/// Documents and the communes that link them.
pub async fn totals(pool: &SqlitePool) -> sqlx::Result<PageTotals> {
    sqlx::query_as(
        "SELECT (SELECT count(*) FROM documents) AS documents, \
         (SELECT count(DISTINCT bfs_id) FROM document_communes) AS communes",
    )
    .fetch_one(pool)
    .await
}

/// Communes with at least one document, by name.
pub async fn communes_with_documents(pool: &SqlitePool) -> sqlx::Result<Vec<CommuneRef>> {
    sqlx::query_as(
        "SELECT c.bfs_id, c.name, c.canton FROM communes c \
         WHERE EXISTS (SELECT 1 FROM document_communes dc WHERE dc.bfs_id = c.bfs_id) \
         ORDER BY c.name, c.bfs_id",
    )
    .fetch_all(pool)
    .await
}

/// The sitemap's communes: those of the archive with documents.
pub async fn sitemap(pool: &SqlitePool) -> sqlx::Result<Vec<i64>> {
    // communes with documents only: a page without any has nothing to find
    sqlx::query_scalar(
        "SELECT bfs_id FROM communes c WHERE EXISTS \
         (SELECT 1 FROM document_communes dc WHERE dc.bfs_id = c.bfs_id) ORDER BY bfs_id",
    )
    .fetch_all(pool)
    .await
}

/// The cantons with documents, by code.
pub async fn cantons_with_documents(pool: &SqlitePool) -> sqlx::Result<Vec<String>> {
    sqlx::query_scalar(
        "SELECT DISTINCT c.canton FROM communes c WHERE EXISTS \
         (SELECT 1 FROM document_communes dc WHERE dc.bfs_id = c.bfs_id) ORDER BY c.canton",
    )
    .fetch_all(pool)
    .await
}

/// A canton's communes by name, each with its documents.
pub async fn canton(pool: &SqlitePool, code: &str) -> sqlx::Result<Vec<BriefCommune>> {
    communes::brief(pool, Some(code), None).await
}

/// A commune with the first page of its documents, as `/api/documents?commune=` gives it; None
/// when the archive does not have the commune. Links start with `base`.
pub async fn commune(
    pool: &SqlitePool,
    bfs_id: i64,
    base: &str,
) -> sqlx::Result<Option<(CommuneDetail, DocumentPage)>> {
    let Some(commune) = communes::detail(pool, bfs_id).await? else {
        return Ok(None);
    };
    let search = documents::Search {
        commune: Some(bfs_id),
        ..Default::default()
    };
    let documents = documents::search(pool, &search, base).await?;
    Ok(Some((commune, documents)))
}
