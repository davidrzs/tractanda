//! MCP server over streamable HTTP at `/mcp`: the commune list, one commune, the document search,
//! one document, the full-text search and the page reader, on the same queries as the REST API.
//! The searches and the commune list answer in a shorter form than the API, for a model to read.
//! Stateless: every request is answered on its own, no session is kept.

use std::sync::Arc;
use std::time::Instant;

use rig_core::tool::DynamicTool;
use rig_rmcp::McpConnection;
use rmcp::handler::server::wrapper::Parameters;
use rmcp::model::{CallToolResult, ContentBlock};
use rmcp::schemars::JsonSchema;
use rmcp::transport::streamable_http_server::session::local::LocalSessionManager;
use rmcp::transport::{StreamableHttpServerConfig, StreamableHttpService};
use rmcp::{ErrorData, ServerHandler, ServiceExt, tool, tool_handler, tool_router};
use serde::Deserialize;
use serde_json::{Value, json};

use crate::AppState;
use crate::db::communes;
use crate::db::documents::{self, Search, text};
use crate::db::fulltext::{self, PageRange, TextSearch, UNAVAILABLE};
use crate::models::{BriefDocumentPage, BriefTextPage};
use crate::usage::Hit;

const LIST_COMMUNES_DESCRIPTION: &str = "Find communes and their BFS numbers, which the other tools \
take. Without a filter it gives only the number of communes and of documents per canton (2,000 \
and more communes would be a long list). `canton` (a code such as ZH) lists the communes of a \
canton; `q` lists the communes whose name contains it, ignoring case and accents (zurich and \
zuerich find Zürich); both together combine. The answer is a table: a header line with the \
fields, then one commune per line: bfs_id, name, canton, language, documents (in the archive) \
and register_status: configured (collected), candidate (a listing was seen, not collected yet), \
not_published (the commune publishes no minutes or decisions online), not_found, blocked_robots, \
blocked_js, blocked_waf, blocked_network (its website cannot be collected), needs_feature (the \
crawler cannot read its listing yet) or not_examined. get_commune gives one commune's details.";

const GET_COMMUNE_DESCRIPTION: &str = "One commune by its BFS number (from list_communes): name, \
canton, language, website and the software behind it (vendor), coordinates, the number of \
documents, `register` (the coverage register's entry: status, notes, and per body what the \
commune publishes and whether the archive collects it), `access` and `holdings`: documents and \
pages per year and section family. `access` lists problems the crawler met on the commune's \
websites (rate limits, robots.txt rules, technical errors), the latest per website. A problem \
stays listed until the same address answers again, which an address that changes (one with the \
date in it) never does: `stale` is true when the problem was last seen a week ago or more, and \
it may well be gone. It does not say when the commune was last crawled. The holdings come from an automatic classification by a language \
model that no person has checked yet.";

const SEARCH_DESCRIPTION: &str = "Search the archive's documents, newest first. Every filter is \
optional; filters combine with AND. `commune`: a BFS number (list_communes finds it). `section`: \
documents with at least one section of this type (minutes, decisions, vote_result, budget, \
accounts, invitation_agenda, proposal, member_request, regulation, report, notice, plan_or_map, \
news, other, unclear). `family`: documents with at least one section of this family \
(deliberation, money, proposals, rules, information, other). `body`: parliament, \
citizens_assembly, executive, commission, commune, other_public_body, private or unknown. `year`: \
the year of the document's date, else the one year the classifier read, else the year of the \
commune's listing date. `q`: text the document's title contains, ignoring case (and accents from \
3 characters on); search_text searches the text of the pages. `limit` 0 to 100 (default 20) \
and `offset` page through the results. Returns `total`, `facets` (documents per \
year, section type and body, each counted with every filter except its own) and `items`: title \
and body as printed (private names appear as [name], in ASCII brackets; the page text uses \
full-width ［name］), dates, pages, sections as their type and \
pages (as minutes 2-11, with (partly unseen) when the classifier saw only some of their pages), \
the communes that link the document, and `original_url`, a full link on this site that redirects \
to the file on the commune's website (add `?page=N` to open it at a page). get_document gives \
every field. Titles, bodies, dates and sections come from an automatic classification by a \
language model that no person has checked yet.";

const GET_DESCRIPTION: &str = "Fetch one document by its sha256 (from search_documents) with \
every field and `run`: the classification run that gave its title, body, dates and sections, with \
the model and a note on what the run's settings do to the data. Titles, bodies, dates and \
sections come from an automatic classification by a language model that no person has checked \
yet.";

const SEARCH_TEXT_DESCRIPTION: &str = "Search the text of the pages, best match first. The search \
covers the pages inside sections of these types: minutes, decisions, vote_result, budget, \
accounts, proposal, invitation_agenda and member_request; pages without a text layer come in as \
OCR reaches them. Each page is searched on its own: `q`: every word must occur on the page \
(anywhere on it, so words on two pages or a phrase across a page break do not match); \"a \
phrase\" in double quotes must occur as written; a word ending in * matches the words that begin with it (at least 3 \
letters before the *). There is no stemming: German compounds and inflected forms need the *, so \
Kunstrasen* also finds Kunstrasenplatz and Kunstrasens, which Kunstrasen misses. OR between words, \
phrases or groups: either may occur; AND (the default) binds more tightly, so a b OR c means (a b) \
OR c, and parentheses group: (Kunstrasen* OR Naturrasen*) Kredit*. NEAR(a b, 20): the words at \
most 20 words apart (default 10). OR, AND and NEAR are operators in capitals only. The documents \
are in German, French, Italian and Romansh and there is no translation: to cover every language, \
join each language's words with OR, as Kunstrasen* OR \"gazon synthétique\" OR \"campo \
sintetico\". Case and accents do not matter. Refused with an error: a * other than at the end of \
a word (words that end in a word cannot be searched), -word and NOT. Filters, all optional, \
combine with AND: `commune` (a BFS number; list_communes finds it), `canton` (such as ZH), `year`, \
`from` and `to` (the document's date, YYYY-MM-DD, inclusive), `language` (de, fr, it, rm; \
comma-separated for several), `section` and `family` (comma-separated: only pages inside a \
section of one of these types or families match), `exclude_section` and `exclude_family` \
(comma-separated: pages inside a section of one of these do not match, such as \
exclude_section=accounts,budget), `body`, `document` (a sha256: search inside that one \
document). `sort`: relevance (default), newest or oldest (by the document's date). `limit` 0 to \
50 (default 10) and `offset` page through the documents; `pages_per_document` 1 to 20 (default \
3) sets how many matching pages each document shows; `commune_facets` 0 to 3000 (default 20) how \
many communes the commune facet lists. Returns `total` (documents), `facets` (documents per year, \
section type, canton, and the communes with the most), and `items`: documents as in \
search_documents, each with `matching_pages` and `pages`, its best pages: `page`, \
`section_types` and `snippet` (plain text: one or two passages that show as many of the matched \
words as fit). The document's `original_url` with `?page=N` opens the file at a page. The \
search takes at most the \
20,000 first matching pages (the best, or the newest or oldest with `sort`); when more match, \
`capped` is true and the total and the facets count only the documents of those pages. The same \
text can appear twice: a proposal as its own file and inside an assembly booklet. Next, read the \
pages around a hit with read_pages and cite the document and the page. The page text is \
machine-extracted (the PDF's text layer, or OCR) and no person has checked yet whether it is \
complete or right; titles, dates and sections come from an automatic classification. Names, \
street addresses and e-mail addresses in the text are replaced by the marks ［name］, ［address］ \
and ［email］ (［secret］ for passwords and keys), dates of birth and mobile numbers by ［date］ and \
［phone］. The marks are in full-width brackets, U+FF3B ［ and U+FF3D ］, not ASCII [ and ]: match \
them as written. Titles write private names as [name], in ASCII brackets.";

const READ_PAGES_DESCRIPTION: &str = "Read the text of up to 10 pages of one document: `sha256` \
(from search_text or search_documents), `from` (default 1) and `to` (default from + 9). Returns \
`page_count` and per page `page`, `section_types`, `source` (layer: the PDF's text layer; ocr: \
text recognition on the page image), `text` and `original_url` (a full link that opens the file \
at that page). A page outside the search has no text and a `reason` instead. The text is machine-extracted: \
reading order, tables and hyphenation can be off, and no person has checked yet whether it is \
complete or right. Names, street addresses and e-mail addresses are replaced by the marks ［name］, \
［address］ and ［email］, dates of birth and mobile numbers by ［date］ and ［phone］, in full-width \
brackets (U+FF3B ［ and U+FF3D ］, not ASCII [ and ]). Cite the page and its original_url.";

#[derive(Deserialize, JsonSchema)]
#[schemars(crate = "rmcp::schemars")]
pub struct ReadPagesArgs {
    /// The document's sha256, from search_text or search_documents.
    sha256: String,
    /// The first page to read; 1, the default, is the first page of the file.
    #[serde(default)]
    from: Option<i64>,
    /// The last page to read; at most 10 pages from `from` on. Default: from + 9.
    #[serde(default)]
    to: Option<i64>,
}

#[derive(Deserialize, JsonSchema)]
#[schemars(crate = "rmcp::schemars")]
pub struct CommuneFilter {
    /// A canton's code, such as ZH: its communes.
    #[serde(default)]
    canton: Option<String>,
    /// Part of a commune's name, ignoring case and accents: the communes whose name contains it.
    #[serde(default)]
    q: Option<String>,
}

#[derive(Deserialize, JsonSchema)]
#[schemars(crate = "rmcp::schemars")]
pub struct CommuneArgs {
    /// The commune's BFS number, from list_communes.
    bfs_id: i64,
}

#[derive(Deserialize, JsonSchema)]
#[schemars(crate = "rmcp::schemars")]
pub struct DocumentArgs {
    /// The document's sha256, from search_documents.
    sha256: String,
}

#[derive(Clone)]
pub struct Archive {
    state: AppState,
    /// The usage channel its tool calls are counted under: `mcp`, or `ask` for the chatbot.
    channel: &'static str,
}

impl Archive {
    /// A result as its channel needs it: `/mcp` the structured content with its text, the chatbot
    /// the text alone, so that its model does not read the same rows twice.
    fn structured(&self, value: Value) -> CallToolResult {
        if self.channel == "ask" {
            CallToolResult::success(vec![ContentBlock::text(value.to_string())])
        } else {
            CallToolResult::structured(value)
        }
    }

    fn with_text(&self, text: String, value: Value) -> CallToolResult {
        if self.channel == "ask" {
            CallToolResult::success(vec![ContentBlock::text(text)])
        } else {
            with_text(text, value)
        }
    }

    fn count(&self, tool: &str, started: Instant, status: u16, search_total: Option<i64>) {
        if let Some(usage) = &self.state.usage {
            usage.record(Hit {
                channel: self.channel,
                route: tool.to_string(),
                bot: None,
                status,
                duration_ms: started.elapsed().as_secs_f64() * 1000.0,
                search_total,
                visitor: None,
                referrer: None,
                commune: None,
            });
        }
    }
}

fn internal(error: sqlx::Error) -> ErrorData {
    tracing::error!(%error, "database query failed");
    ErrorData::internal_error("database query failed", None)
}

/// A result whose text, the part a model reads, is `text` rather than the JSON of `structured`.
fn with_text(text: String, structured: Value) -> CallToolResult {
    let mut result = CallToolResult::structured(structured);
    result.content = vec![ContentBlock::text(text)];
    result
}

/// Rows as a TOON table: `name[N]{fields}:`, then one row per line, its values separated by
/// commas. A string that could be read as another value or that holds a comma or a quote is
/// written as a JSON string.
fn table(name: &str, fields: &[&str], rows: &[Vec<Value>]) -> String {
    let mut out = format!("{name}[{}]{{{}}}:", rows.len(), fields.join(","));
    for row in rows {
        let cells: Vec<String> = row
            .iter()
            .map(|value| match value {
                Value::String(s)
                    if !(s.is_empty()
                        || s.trim() != s
                        || s.contains([',', '"', '\n'])
                        || matches!(s.as_str(), "null" | "true" | "false")
                        || s.parse::<f64>().is_ok()) =>
                {
                    s.clone()
                }
                other => other.to_string(),
            })
            .collect();
        out.push_str("\n  ");
        out.push_str(&cells.join(","));
    }
    out
}

/// A tool error the agent can read and act on.
fn refused(tool: &str, reason: &str) -> CallToolResult {
    CallToolResult::error(vec![ContentBlock::text(format!(
        "Error executing tool {tool}: {reason}"
    ))])
}

#[tool_router]
impl Archive {
    #[tool(
        title = "Search documents",
        description = SEARCH_DESCRIPTION,
        annotations(
            title = "Search documents",
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn search_documents(
        &self,
        Parameters(search): Parameters<Search>,
    ) -> Result<CallToolResult, ErrorData> {
        let started = Instant::now();
        if let Err(reason) = search.check() {
            self.count("search_documents", started, 400, None);
            return Ok(refused("search_documents", &reason));
        }
        let page = documents::search(&self.state.db, &search, &self.state.site.public_url)
            .await
            .map_err(internal)?;
        let total = search.is_search().then_some(page.total);
        self.count("search_documents", started, 200, total);
        Ok(self
            .structured(serde_json::to_value(BriefDocumentPage::from(page)).unwrap_or(Value::Null)))
    }

    #[tool(
        title = "Get a document",
        description = GET_DESCRIPTION,
        annotations(
            title = "Get a document",
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn get_document(
        &self,
        Parameters(args): Parameters<DocumentArgs>,
    ) -> Result<CallToolResult, ErrorData> {
        let started = Instant::now();
        let document = documents::get(&self.state.db, &args.sha256, &self.state.site.public_url)
            .await
            .map_err(internal)?;
        let Some(document) = document else {
            self.count("get_document", started, 404, None);
            return Ok(refused(
                "get_document",
                "no document has this sha256; search_documents and search_text find documents",
            ));
        };
        self.count("get_document", started, 200, None);
        Ok(self.structured(serde_json::to_value(document).unwrap_or(Value::Null)))
    }

    #[tool(
        title = "Search the text of pages",
        description = SEARCH_TEXT_DESCRIPTION,
        annotations(
            title = "Search the text of pages",
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn search_text(
        &self,
        Parameters(search): Parameters<TextSearch>,
    ) -> Result<CallToolResult, ErrorData> {
        let started = Instant::now();
        if !self.state.fulltext {
            self.count("search_text", started, 503, None);
            return Ok(refused("search_text", UNAVAILABLE));
        }
        let query = match search.check() {
            Ok(query) => query,
            Err(reason) => {
                self.count("search_text", started, 400, None);
                return Ok(refused("search_text", &reason));
            }
        };
        let page = fulltext::search(&self.state.db, &search, &query, &self.state.site.public_url)
            .await
            .map_err(internal)?;
        let total = (search.offset == 0).then_some(page.total);
        self.count("search_text", started, 200, total);
        Ok(self.structured(serde_json::to_value(BriefTextPage::from(page)).unwrap_or(Value::Null)))
    }

    #[tool(
        title = "Read pages",
        description = READ_PAGES_DESCRIPTION,
        annotations(
            title = "Read pages",
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn read_pages(
        &self,
        Parameters(args): Parameters<ReadPagesArgs>,
    ) -> Result<CallToolResult, ErrorData> {
        let started = Instant::now();
        if !self.state.fulltext {
            self.count("read_pages", started, 503, None);
            return Ok(refused("read_pages", UNAVAILABLE));
        }
        let range = PageRange {
            from: args.from,
            to: args.to,
        };
        let (from, to) = match range.check() {
            Ok(range) => range,
            Err(reason) => {
                self.count("read_pages", started, 400, None);
                return Ok(refused("read_pages", &reason));
            }
        };
        let pages = fulltext::read(
            &self.state.db,
            &args.sha256,
            from,
            to,
            &self.state.site.public_url,
        )
        .await
        .map_err(internal)?;
        let Some(pages) = pages else {
            self.count("read_pages", started, 404, None);
            return Ok(refused("read_pages", "no document has this sha256"));
        };
        self.count("read_pages", started, 200, None);
        Ok(self.structured(serde_json::to_value(pages).unwrap_or(Value::Null)))
    }

    #[tool(
        title = "List communes",
        description = LIST_COMMUNES_DESCRIPTION,
        annotations(
            title = "List communes",
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn list_communes(
        &self,
        Parameters(filter): Parameters<CommuneFilter>,
    ) -> Result<CallToolResult, ErrorData> {
        let started = Instant::now();
        let (canton, q) = (text(&filter.canton), text(&filter.q));
        let result = if canton.is_none() && q.is_none() {
            let cantons = communes::per_canton(&self.state.db)
                .await
                .map_err(internal)?;
            let total: i64 = cantons.iter().map(|c| c.communes).sum();
            let note = format!(
                "The archive has {total} communes in {} cantons. Pass canton (such as ZH) or q \
                 (part of a name) to list communes with their BFS numbers.",
                cantons.len()
            );
            let rows: Vec<Vec<Value>> = cantons
                .iter()
                .map(|c| vec![json!(c.canton), json!(c.communes), json!(c.documents)])
                .collect();
            let text = format!(
                "{note}\n{}",
                table("cantons", &["canton", "communes", "documents"], &rows)
            );
            self.with_text(text, json!({"note": note, "cantons": cantons}))
        } else {
            let list = communes::brief(&self.state.db, canton, q)
                .await
                .map_err(internal)?;
            let rows: Vec<Vec<Value>> = list
                .iter()
                .map(|c| {
                    vec![
                        json!(c.bfs_id),
                        json!(c.name),
                        json!(c.canton),
                        json!(c.language),
                        json!(c.documents),
                        json!(c.register_status),
                    ]
                })
                .collect();
            let fields = [
                "bfs_id",
                "name",
                "canton",
                "language",
                "documents",
                "register_status",
            ];
            let mut text = table("communes", &fields, &rows);
            if list.is_empty() {
                text = format!(
                    "No commune matches. canton takes a code such as ZH, q part of a name.\n{text}"
                );
            }
            self.with_text(text, json!({"communes": list}))
        };
        self.count("list_communes", started, 200, None);
        Ok(result)
    }

    #[tool(
        title = "Get a commune",
        description = GET_COMMUNE_DESCRIPTION,
        annotations(
            title = "Get a commune",
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn get_commune(
        &self,
        Parameters(args): Parameters<CommuneArgs>,
    ) -> Result<CallToolResult, ErrorData> {
        let started = Instant::now();
        let commune = communes::detail(&self.state.db, args.bfs_id)
            .await
            .map_err(internal)?;
        let Some(commune) = commune else {
            self.count("get_commune", started, 404, None);
            return Ok(refused(
                "get_commune",
                "the archive has no commune with this BFS number; list_communes finds it",
            ));
        };
        self.count("get_commune", started, 200, None);
        Ok(self.structured(serde_json::to_value(commune).unwrap_or(Value::Null)))
    }
}

#[tool_handler(
    name = "tractanda",
    instructions = "Tractanda is an open archive of the documents Swiss communes publish on their websites: minutes, decisions, budgets, accounts, vote results and other papers of assemblies, parliaments and councils. Each file is listed once, with a fixed label and a link to the original on the commune's website. Use list_communes with a canton or part of a name to find a commune's BFS number (without either it gives only counts per canton), get_commune for one commune's coverage, search_documents to filter documents by commune, section type, kind, body, year and label, and get_document for one document. To find what the pages say, use search_text, then read the pages around a hit with read_pages, then cite the document and the page: each page has an original_url, a full link that opens the file at that page. The documents are in German, French, Italian and Romansh and are not translated: to search every language, join each language's words with OR in search_text. The page text is machine-extracted (the PDF's text layer, or OCR) and no person has checked it: quote it with care and point to the page. Names, street addresses and e-mail addresses in the page text are replaced by ［name］, ［address］ and ［email］, in full-width brackets (U+FF3B and U+FF3D, not ASCII [ ]); titles write private names as [name] in ASCII brackets. Kinds, bodies, dates and sections come from an automatic classification by a language model that no person has checked yet. Coverage is partial and growing: no result does not mean that no document exists."
)]
impl ServerHandler for Archive {}

/// The `/mcp` endpoint.
pub fn service(
    state: AppState,
    allowed_hosts: &[String],
) -> StreamableHttpService<Archive, LocalSessionManager> {
    let config = StreamableHttpServerConfig::default()
        .with_legacy_session_mode(false)
        .with_json_response(true)
        .with_allowed_hosts(allowed_hosts.to_vec());
    StreamableHttpService::new(
        move || Ok(Archive::new(state.clone())),
        Arc::new(LocalSessionManager::default()),
        config,
    )
}

impl Archive {
    fn new(state: AppState) -> Self {
        Archive {
            state,
            channel: "mcp",
        }
    }
}

/// The tools of `/mcp` for the chatbot (`ask`): the same server, connected in-process over an
/// in-memory pipe, its calls counted under the channel `ask`. The tools keep the connection open.
pub async fn chat_tools(state: AppState) -> Result<Vec<DynamicTool>, String> {
    let (server_io, client_io) = tokio::io::duplex(256 * 1024);
    let archive = Archive {
        state,
        channel: "ask",
    };
    tokio::spawn(async move {
        match archive.serve(server_io).await {
            Ok(running) => {
                let _ = running.waiting().await;
            }
            Err(error) => tracing::error!(%error, "ask: the in-process MCP server did not start"),
        }
    });
    let client = rig_rmcp::client_config("tractanda-ask", env!("CARGO_PKG_VERSION"))
        .serve(client_io)
        .await
        .map_err(|e| format!("in-process MCP client: {e}"))?;
    let connection = McpConnection::new(client);
    connection
        .tools()
        .await
        .map_err(|e| format!("in-process MCP tools: {e}"))?
        .into_iter()
        .map(|tool| DynamicTool::try_from(tool).map_err(|e| format!("MCP tool: {e}")))
        .collect()
}
