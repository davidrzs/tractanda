//! Documents: the filtered list with its facets, one document, the runs, and where an original
//! lives.

use std::collections::HashMap;

use rmcp::schemars::JsonSchema;
use serde::Deserialize;
use sqlx::{Decode, QueryBuilder, Sqlite, SqlitePool, Type};

use super::contains_pattern;
use crate::models::{
    CommuneRef, Count, Document, DocumentDetail, DocumentPage, DocumentRow, Facets, Release,
    Releases, Run, RunRef, RunRow, Section, linkable_url,
};

pub const DEFAULT_LIMIT: i64 = 20;
pub const MAX_LIMIT: i64 = 100;

const DOCUMENT_SELECT: &str = "SELECT d.sha256, d.title, d.body_name, d.first_seen, d.listing_date, \
    d.date, d.year, d.years, d.page_count, d.pages_seen, d.run_id, d.body_kind, d.languages, \
    d.personal_data FROM documents d";

/// The parameters of `GET /api/documents` and of the MCP tool `search_documents`. The filters are
/// optional and combined with AND; a blank text filter is no filter.
#[derive(Deserialize, JsonSchema)]
#[schemars(crate = "rmcp::schemars")]
pub struct Search {
    /// A commune's BFS number (from list_communes): the documents it links.
    #[serde(default)]
    pub commune: Option<i64>,
    /// A section type: documents with at least one section of this type. One of minutes,
    /// decisions, vote_result, budget, accounts, invitation_agenda, proposal, member_request,
    /// regulation, report, notice, plan_or_map, news, other, unclear.
    #[serde(default)]
    pub section: Option<String>,
    /// A section family (deliberation, money, proposals, rules, information, other): documents
    /// with at least one section of it.
    #[serde(default)]
    pub family: Option<String>,
    /// A section family: documents whose first section is of it. A document has one first section,
    /// so these counts add up across the families to all documents.
    #[serde(default)]
    pub first_family: Option<String>,
    /// The kind of body behind the document. One of parliament, citizens_assembly, executive,
    /// commission, commune, other_public_body, private, unknown.
    #[serde(default)]
    pub body: Option<String>,
    /// The document's year: of its date, else the one year the classifier read, else of the
    /// commune's listing date.
    #[serde(default)]
    pub year: Option<i64>,
    /// Text the document's title contains, ignoring case; from 3 characters on also ignoring
    /// accents.
    #[serde(default)]
    pub q: Option<String>,
    /// Documents to skip.
    #[serde(default)]
    pub offset: i64,
    /// Documents to return, 0 to 100.
    #[serde(default = "default_limit")]
    pub limit: i64,
}

fn default_limit() -> i64 {
    DEFAULT_LIMIT
}

impl Default for Search {
    fn default() -> Self {
        Search {
            commune: None,
            section: None,
            family: None,
            first_family: None,
            body: None,
            year: None,
            q: None,
            offset: 0,
            limit: DEFAULT_LIMIT,
        }
    }
}

impl Search {
    /// Why the offset or the limit is refused, if it is.
    pub fn check(&self) -> Result<(), String> {
        if self.offset < 0 {
            return Err("offset must not be negative".into());
        }
        if !(0..=MAX_LIMIT).contains(&self.limit) {
            return Err(format!("limit must be between 0 and {MAX_LIMIT}"));
        }
        Ok(())
    }

    /// A first page with a query or a filter: a search in the usage statistics.
    pub fn is_search(&self) -> bool {
        let text_set = [
            &self.section,
            &self.family,
            &self.first_family,
            &self.body,
            &self.q,
        ]
        .into_iter()
        .any(|v| text(v).is_some());
        self.offset == 0 && (text_set || self.commune.is_some() || self.year.is_some())
    }
}

/// A text parameter that is set: present and not blank.
pub(crate) fn text(value: &Option<String>) -> Option<&str> {
    value.as_deref().map(str::trim).filter(|v| !v.is_empty())
}

/// What a facet counts.
#[derive(Clone, Copy, PartialEq)]
enum Facet {
    Years,
    Sections,
    Bodies,
}

impl Facet {
    /// The query up to its filters, and its grouping and order: by value, years newest first,
    /// NULL last.
    fn sql(self) -> (&'static str, &'static str) {
        match self {
            Facet::Years => (
                "SELECT d.year, count(*) FROM documents d WHERE 1",
                " GROUP BY d.year ORDER BY d.year IS NULL, d.year DESC",
            ),
            // documents, not sections: a document with two sections of a type counts once
            Facet::Sections => (
                "SELECT s.type, count(DISTINCT d.sha256) FROM documents d \
                 JOIN sections s ON s.sha256 = d.sha256 WHERE 1",
                " GROUP BY s.type ORDER BY s.type",
            ),
            Facet::Bodies => (
                "SELECT d.body_kind, count(*) FROM documents d WHERE 1",
                " GROUP BY d.body_kind ORDER BY d.body_kind IS NULL, d.body_kind",
            ),
        }
    }
}

/// ` AND ...` for every filter that is set, except the one the facet being counted selects.
fn push_filters(qb: &mut QueryBuilder<Sqlite>, s: &Search, counting: Option<Facet>) {
    let own = |facet| counting == Some(facet);
    if let Some(commune) = s.commune {
        qb.push(" AND d.sha256 IN (SELECT sha256 FROM document_communes WHERE bfs_id = ")
            .push_bind(commune)
            .push(")");
    }
    if let Some(section) = text(&s.section).filter(|_| !own(Facet::Sections)) {
        qb.push(" AND d.sha256 IN (SELECT sha256 FROM sections WHERE type = ")
            .push_bind(section.to_string())
            .push(")");
    }
    if let Some(family) = text(&s.family) {
        qb.push(" AND d.sha256 IN (SELECT sha256 FROM sections WHERE family = ")
            .push_bind(family.to_string())
            .push(")");
    }
    if let Some(family) = text(&s.first_family) {
        qb.push(" AND d.sha256 IN (SELECT sha256 FROM sections WHERE position = 1 AND family = ")
            .push_bind(family.to_string())
            .push(")");
    }
    if let Some(body) = text(&s.body).filter(|_| !own(Facet::Bodies)) {
        qb.push(" AND d.body_kind = ").push_bind(body.to_string());
    }
    if let Some(year) = s.year.filter(|_| !own(Facet::Years)) {
        qb.push(" AND d.year = ").push_bind(year);
    }
    if let Some(q) = text(&s.q) {
        if q.chars().count() >= 3 {
            // the title index (trigrams): a quoted string matches where the title contains it
            qb.push(" AND d.rowid IN (SELECT rowid FROM documents_fts WHERE documents_fts MATCH ")
                .push_bind(format!("title : \"{}\"", q.replace('"', "\"\"")))
                .push(")");
        } else {
            qb.push(" AND lower(d.title) LIKE lower(")
                .push_bind(contains_pattern(q))
                .push(") ESCAPE '/'");
        }
    }
}

/// The documents that match per value of `counted`, with every filter but its own.
async fn facet<T>(pool: &SqlitePool, s: &Search, counted: Facet) -> sqlx::Result<Vec<Count<T>>>
where
    T: for<'r> Decode<'r, Sqlite> + Type<Sqlite> + Send + Unpin,
{
    let (select, group) = counted.sql();
    let mut qb = QueryBuilder::new(select);
    push_filters(&mut qb, s, Some(counted));
    qb.push(group);
    let rows: Vec<(Option<T>, i64)> = qb.build_query_as().fetch_all(pool).await?;
    Ok(rows
        .into_iter()
        .map(|(value, count)| Count { value, count })
        .collect())
}

/// `GET /api/documents`: one page of the documents that match, newest first, and the facets.
/// The offset and limit are checked (`Search::check`); links start with `base`, the site's public
/// address.
pub async fn search(pool: &SqlitePool, s: &Search, base: &str) -> sqlx::Result<DocumentPage> {
    let facets = Facets {
        years: facet(pool, s, Facet::Years).await?,
        sections: facet(pool, s, Facet::Sections).await?,
        bodies: facet(pool, s, Facet::Bodies).await?,
    };
    let mut qb = QueryBuilder::new("SELECT count(*) FROM documents d WHERE 1");
    push_filters(&mut qb, s, None);
    let total: i64 = qb.build_query_scalar().fetch_one(pool).await?;
    let mut qb = QueryBuilder::new(DOCUMENT_SELECT);
    qb.push(" WHERE 1");
    push_filters(&mut qb, s, None);
    qb.push(
        " ORDER BY d.year DESC NULLS LAST, coalesce(d.date, d.listing_date) DESC, d.sha256 \
         LIMIT ",
    )
    .push_bind(s.limit)
    .push(" OFFSET ")
    .push_bind(s.offset);
    let rows: Vec<DocumentRow> = qb.build_query_as().fetch_all(pool).await?;
    Ok(DocumentPage {
        total,
        offset: s.offset,
        limit: s.limit,
        facets,
        items: complete(pool, rows, base).await?,
    })
}

/// `GET /api/documents/{sha256}`: one document with its run; links start with `base`.
pub async fn get(
    pool: &SqlitePool,
    sha256: &str,
    base: &str,
) -> sqlx::Result<Option<DocumentDetail>> {
    let mut qb = QueryBuilder::new(DOCUMENT_SELECT);
    qb.push(" WHERE d.sha256 = ").push_bind(sha256.to_string());
    let Some(row) = qb
        .build_query_as::<DocumentRow>()
        .fetch_optional(pool)
        .await?
    else {
        return Ok(None);
    };
    let run: Option<RunRef> =
        sqlx::query_as("SELECT id, stage, model, note FROM runs WHERE id = ?")
            .bind(&row.run_id)
            .fetch_optional(pool)
            .await?;
    Ok(complete(pool, vec![row], base)
        .await?
        .pop()
        .map(|document| DocumentDetail { document, run }))
}

/// The documents with these sha256, with their sections and communes, in no particular order;
/// links start with `base`.
pub(crate) async fn by_sha256(
    pool: &SqlitePool,
    shas: &[String],
    base: &str,
) -> sqlx::Result<Vec<Document>> {
    if shas.is_empty() {
        return Ok(Vec::new());
    }
    let mut qb = QueryBuilder::new(DOCUMENT_SELECT);
    qb.push(" WHERE d.sha256 IN (");
    let mut list = qb.separated(", ");
    for sha256 in shas {
        list.push_bind(sha256.clone());
    }
    qb.push(")");
    let rows: Vec<DocumentRow> = qb.build_query_as().fetch_all(pool).await?;
    complete(pool, rows, base).await
}

/// The documents of `rows` with their sections and communes; links start with `base`.
async fn complete(
    pool: &SqlitePool,
    rows: Vec<DocumentRow>,
    base: &str,
) -> sqlx::Result<Vec<Document>> {
    if rows.is_empty() {
        return Ok(Vec::new());
    }
    let for_rows = |select: &str, order: &str| {
        let mut qb = QueryBuilder::new(select);
        let mut list = qb.separated(", ");
        for r in &rows {
            list.push_bind(r.sha256.clone());
        }
        qb.push(order);
        qb
    };
    let mut qb = for_rows(
        "SELECT sha256, type AS section_type, family, first_page, last_page, partly_unseen \
         FROM sections WHERE sha256 IN (",
        ") ORDER BY sha256, position",
    );
    let mut sections: HashMap<String, Vec<Section>> = HashMap::new();
    for s in qb.build_query_as::<Section>().fetch_all(pool).await? {
        sections.entry(s.sha256.clone()).or_default().push(s);
    }
    let mut qb = for_rows(
        "SELECT dc.sha256, c.bfs_id, c.name, c.canton FROM document_communes dc \
         JOIN communes c ON c.bfs_id = dc.bfs_id WHERE dc.sha256 IN (",
        ") ORDER BY c.name, c.bfs_id",
    );
    let mut communes: HashMap<String, Vec<CommuneRef>> = HashMap::new();
    let linked: Vec<(String, i64, String, String)> = qb.build_query_as().fetch_all(pool).await?;
    for (sha256, bfs_id, name, canton) in linked {
        communes.entry(sha256).or_default().push(CommuneRef {
            bfs_id,
            name,
            canton,
        });
    }
    Ok(rows
        .into_iter()
        .map(|r| {
            let s = sections.remove(&r.sha256).unwrap_or_default();
            let c = communes.remove(&r.sha256).unwrap_or_default();
            Document::new(r, s, c, base)
        })
        .collect())
}

/// `GET /api/runs`: every run, by id.
pub async fn runs(pool: &SqlitePool) -> sqlx::Result<Vec<Run>> {
    let rows: Vec<RunRow> =
        sqlx::query_as("SELECT id, stage, model, settings, note, documents FROM runs ORDER BY id")
            .fetch_all(pool)
            .await?;
    Ok(rows.into_iter().map(Run::from).collect())
}

/// `GET /api/releases`: the versions of the ledger the serving database was built with, then the
/// one the full-text index was released as (`release.py`), and the files first downloaded each day.
pub async fn releases(pool: &SqlitePool, fulltext: bool) -> sqlx::Result<Releases> {
    let mut releases: Vec<Release> = sqlx::query_as(
        "SELECT version, date, documents, pages, communes, searchable_pages, ocr_pages, \
         classification_run, git_commit, note FROM releases ORDER BY rowid",
    )
    .fetch_all(pool)
    .await?;
    let mut current = None;
    if fulltext {
        let written: Option<String> =
            sqlx::query_scalar("SELECT value FROM fulltext.build WHERE key = 'release'")
                .fetch_optional(pool)
                .await?;
        // unreadable JSON counts as not released
        if let Some(release) = written.and_then(|j| serde_json::from_str::<Release>(&j).ok()) {
            current = Some(release.version.clone());
            if !releases.iter().any(|r| r.version == release.version) {
                releases.push(release);
            }
        }
    }
    let downloads = sqlx::query_as("SELECT day, files FROM downloads ORDER BY day")
        .fetch_all(pool)
        .await?;
    Ok(Releases {
        current,
        releases,
        downloads,
    })
}

/// `GET /original/{sha256}`: the document's URL at `commune`, else the first by BFS id; only
/// absolute http(s) URLs.
pub async fn original(
    pool: &SqlitePool,
    sha256: &str,
    commune: Option<i64>,
) -> sqlx::Result<Option<String>> {
    let urls: Vec<(i64, String)> = sqlx::query_as(
        "SELECT bfs_id, source_url FROM document_communes \
         WHERE sha256 = ? AND source_url IS NOT NULL ORDER BY bfs_id",
    )
    .bind(sha256)
    .fetch_all(pool)
    .await?;
    let linkable: Vec<(i64, String)> = urls
        .into_iter()
        .filter(|(_, url)| linkable_url(Some(url)).is_some())
        .collect();
    let chosen = linkable
        .iter()
        .position(|(bfs_id, _)| commune == Some(*bfs_id))
        .unwrap_or(0);
    Ok(linkable.into_iter().nth(chosen).map(|(_, url)| url))
}
