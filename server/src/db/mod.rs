//! Query functions, one per endpoint, grouped by area. Every query is read-only.

pub mod communes;
pub mod documents;
pub mod fulltext;
pub mod pages;

use std::path::Path;

use sqlx::Connection;
use sqlx::sqlite::{SqliteConnectOptions, SqliteConnection, SqlitePool, SqlitePoolOptions};

fn read_only(path: &Path) -> SqliteConnectOptions {
    SqliteConnectOptions::new()
        .filename(path)
        .read_only(true)
        .create_if_missing(false)
}

/// A read-only pool on the serving database the Python build writes. With `fulltext`, every
/// connection attaches that full-text index as the schema `fulltext`. The serving database is
/// opened read-only, and an attached file inherits that, so the index is read-only too.
pub async fn open_read_only(
    path: &Path,
    fulltext: Option<&Path>,
) -> Result<SqlitePool, sqlx::Error> {
    let mut pool = SqlitePoolOptions::new().max_connections(8);
    if let Some(index) = fulltext {
        let index = index.to_string_lossy().into_owned();
        pool = pool.after_connect(move |connection, _| {
            let index = index.clone();
            Box::pin(async move {
                sqlx::query("ATTACH DATABASE ? AS fulltext")
                    .bind(index)
                    .execute(connection)
                    .await
                    .map(drop)
            })
        });
    }
    pool.connect_with(read_only(path)).await
}

/// Fails unless every table of `serving.sql` is there, so that a database of another build stops
/// the server at start instead of failing each request.
pub async fn check_tables(pool: &SqlitePool) -> Result<(), sqlx::Error> {
    sqlx::query(
        "SELECT 1 FROM communes, runs, documents, documents_fts, document_communes, sections, \
         register, register_publications, access_observations, releases, downloads LIMIT 0",
    )
    .execute(pool)
    .await
    .map(drop)
}

/// Fails unless the file at `path` is a full-text index (`fulltext.py`): its tables are there.
pub async fn check_fulltext(path: &Path) -> Result<(), sqlx::Error> {
    let mut connection = SqliteConnection::connect_with(&read_only(path)).await?;
    let checked = sqlx::query("SELECT 1 FROM pages, page_fts, build LIMIT 0")
        .execute(&mut connection)
        .await
        .map(drop);
    connection.close().await?;
    checked
}

/// `lower(col) LIKE '%' || lower(?) || '%' ESCAPE '/'`: SQLAlchemy's `icontains(.., autoescape=True)`.
pub fn contains_pattern(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 2);
    out.push('%');
    for c in text.chars() {
        if matches!(c, '%' | '_' | '/') {
            out.push('/');
        }
        out.push(c);
    }
    out.push('%');
    out
}
