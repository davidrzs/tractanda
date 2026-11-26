//! The OpenAPI 3.1 description of the JSON API, `GET /api/openapi.json`. The schemas come from the
//! very types the handlers read and return (schemars, the library the MCP tools use), so they cannot
//! drift from the API. The paths are listed here; a test checks that every `/api` route of the
//! router is among them.

use axum::Json;
use axum::extract::State;
use rmcp::schemars::JsonSchema;
use rmcp::schemars::generate::{SchemaGenerator, SchemaSettings};
use serde_json::{Map, Value, json};

use super::documents::OriginalParams;
use crate::AppState;
use crate::db::documents::Search;
use crate::db::fulltext::{PageRange, TextSearch};
use crate::models::{
    Commune, CommuneDetail, DocumentDetail, DocumentPage, DocumentPages, RegisterEntry, Releases,
    Run, TextPage,
};

/// `GET /api/openapi.json`.
pub async fn spec(State(state): State<AppState>) -> Json<Value> {
    Json(build(&state.site.public_url))
}

/// Shared types land in `#/components/schemas`, where OpenAPI looks for them.
fn settings() -> SchemaSettings {
    SchemaSettings::draft2020_12().with(|s| s.definitions_path = "/components/schemas".into())
}

/// The description, for a server at `public_url`.
pub fn build(public_url: &str) -> Value {
    // responses as the server writes them, parameters as it reads them
    let mut out = settings().for_serialize().into_generator();
    let mut input = settings().for_deserialize().into_generator();
    let sha256 = path_parameter(
        "sha256",
        "string",
        "The file's SHA-256, from a document list.",
    );
    let bfs_id = path_parameter("bfs_id", "integer", "The commune's BFS number.");

    let paths = json!({
        "/api/communes": {"get": operation(
            "listCommunes", "Communes",
            "Every commune of the archive, by canton and name, with its number of documents.",
            vec![], ok::<Vec<Commune>>(&mut out, "The communes."), &[],
        )},
        "/api/communes/{bfs_id}": {"get": operation(
            "getCommune", "Communes",
            "One commune: its register entry, access problems of its website, and its documents per \
             year and section family.",
            vec![bfs_id], ok::<CommuneDetail>(&mut out, "The commune."), &[404],
        )},
        "/api/register": {"get": operation(
            "listRegister", "Communes",
            "The coverage register: for every Swiss commune, what it publishes and whether the \
             archive collects it.",
            vec![], ok::<Vec<RegisterEntry>>(&mut out, "The register entries."), &[],
        )},
        "/api/documents": {"get": operation(
            "searchDocuments", "Documents",
            "The documents that match, newest first, with counts per year, section type and body. \
             Each count applies every filter except its own.",
            query::<Search>(&mut input), ok::<DocumentPage>(&mut out, "One page of documents."),
            &[400],
        )},
        "/api/documents/{sha256}": {"get": operation(
            "getDocument", "Documents",
            "One document with the classification run behind its fields.",
            vec![sha256.clone()], ok::<DocumentDetail>(&mut out, "The document."), &[404],
        )},
        "/api/pages": {"get": operation(
            "searchText", "Full text",
            "The documents whose pages contain the words, best match first or by date (`sort`), \
             each with its best pages (three, or `pages_per_document`): a plain-text snippet, the \
             matches in it and a link to the file at that page. `q` takes words, \"phrases\", \
             prefixes (`word*`), `OR`, `(groups)` and `NEAR(a b, 10)`. `document` searches inside \
             one document. Counts per year, section type, canton and commune. The search takes \
             at most the first 20,000 matching pages; `capped` says when more match. The text is machine-extracted and \
             unchecked; names, street addresses and e-mail addresses are replaced by marks such \
             as ［name］.",
            query::<TextSearch>(&mut input),
            ok::<TextPage>(&mut out, "One page of documents with their matching pages."),
            &[400, 503],
        )},
        "/api/documents/{sha256}/pages": {"get": operation(
            "readPages", "Full text",
            "The text of up to 10 pages of a document. A page outside the search has no text and \
             a reason instead. The text is machine-extracted and unchecked; names, street \
             addresses and e-mail addresses are replaced by marks such as ［name］.",
            [vec![sha256.clone()], query::<PageRange>(&mut input)].concat(),
            ok::<DocumentPages>(&mut out, "The pages."),
            &[400, 404, 503],
        )},
        "/api/runs": {"get": operation(
            "listRuns", "Documents",
            "The classification runs, with every setting that changes what they saw and a note on \
             what those settings do to the data.",
            vec![], ok::<Vec<Run>>(&mut out, "The runs."), &[],
        )},
        "/api/releases": {"get": operation(
            "listReleases", "Documents",
            "The released versions of the data with their counts, the version the site serves, and \
             the files first downloaded each day, also those not classified yet.",
            vec![], ok::<Releases>(&mut out, "The versions."), &[],
        )},
        "/api/openapi.json": {"get": operation(
            "getOpenApi", "API",
            "This description.",
            vec![],
            json!({"200": {"description": "The OpenAPI document.",
                           "content": {"application/json": {"schema": {"type": "object"}}}}}),
            &[],
        )},
        "/original/{sha256}": {"get": operation(
            "openOriginal", "Documents",
            "Redirects to the file on the commune's website.",
            [vec![sha256], query::<OriginalParams>(&mut input)].concat(),
            json!({"302": {"description": "The file's address on the commune's website.",
                           "headers": {"Location": {"schema": {"type": "string"}}}}}),
            &[404],
        )},
    });

    let mut schemas = out.take_definitions(true);
    schemas.extend(input.take_definitions(true));
    schemas.insert(
        "Error".into(),
        json!({"type": "object", "properties": {"detail": {"type": "string"}},
               "required": ["detail"]}),
    );
    json!({
        "openapi": "3.1.0",
        "info": {
            "title": "Tractanda API",
            "version": env!("CARGO_PKG_VERSION"),
            "description": format!(
                "Read-only JSON API of Tractanda, the open archive of the documents Swiss \
                 communes publish on their websites. No key, no account. The data is free to use \
                 under CC0 1.0; a citation is appreciated.\n\n\
                 Titles, bodies, dates and sections come from an automatic classification by a \
                 language model that no person has checked yet. Private people's names appear \
                 as [name].\n\n\
                 AI agents can use the same data over MCP: `{public_url}/mcp` (streamable HTTP, \
                 tools `search_documents`, `get_document`, `list_communes`, `search_text`, \
                 `read_pages`)."
            ),
            "license": {"name": "CC0 1.0 (the data)", "identifier": "CC0-1.0"},
            "contact": {"url": public_url},
        },
        "servers": [{"url": public_url}],
        "tags": [
            {"name": "Documents", "description": "Files, their sections and where they are."},
            {"name": "Communes", "description": "The communes and the coverage register."},
            {"name": "Full text", "description": "Search and read the text of the pages."},
            {"name": "API", "description": "This description."},
        ],
        "paths": paths,
        "components": {"schemas": schemas},
    })
}

fn operation(
    id: &str,
    tag: &str,
    description: &str,
    parameters: Vec<Value>,
    mut responses: Value,
    errors: &[u16],
) -> Value {
    for status in errors {
        let description = match status {
            400 => "An invalid parameter.",
            503 => "The server has no full-text index.",
            _ => "Not in the archive.",
        };
        responses[status.to_string()] = json!({
            "description": description,
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}},
        });
    }
    json!({
        "operationId": id,
        "tags": [tag],
        "summary": description.split(". ").next().unwrap_or(description).trim_end_matches('.'),
        "description": description,
        "parameters": parameters,
        "responses": responses,
    })
}

fn ok<T: JsonSchema>(generator: &mut SchemaGenerator, description: &str) -> Value {
    json!({"200": {
        "description": description,
        "content": {"application/json": {"schema": generator.subschema_for::<T>()}},
    }})
}

fn path_parameter(name: &str, kind: &str, description: &str) -> Value {
    json!({"name": name, "in": "path", "required": true, "description": description,
           "schema": {"type": kind}})
}

/// The fields of a query type as query parameters, each with its description.
fn query<T: JsonSchema>(generator: &mut SchemaGenerator) -> Vec<Value> {
    let root = generator.root_schema_for::<T>().to_value();
    let required: Vec<&str> = root
        .get("required")
        .and_then(Value::as_array)
        .map(|r| r.iter().filter_map(Value::as_str).collect())
        .unwrap_or_default();
    let empty = Map::new();
    let properties = root
        .get("properties")
        .and_then(Value::as_object)
        .unwrap_or(&empty);
    properties
        .iter()
        .map(|(name, schema)| {
            let mut schema = schema.clone();
            let description = schema
                .as_object_mut()
                .and_then(|s| s.remove("description"))
                .unwrap_or(Value::Null);
            json!({"name": name, "in": "query", "required": required.contains(&name.as_str()),
                   "description": description, "schema": schema})
        })
        .collect()
}
