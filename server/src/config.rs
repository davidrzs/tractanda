//! Configuration from the environment, with defaults for a checkout of the repository.

use std::env;
use std::net::SocketAddr;
use std::path::PathBuf;

use rig_core::completion::{Effort, Reasoning};

use crate::ask::{AskConfig, Provider, Settings};

#[derive(Clone, Debug)]
pub struct Config {
    /// `DATABASE_PATH`: the serving database the Python build writes; opened read-only.
    pub database_path: PathBuf,
    /// `FULLTEXT_PATH`: the full-text index of page text the Python build writes; attached
    /// read-only when the file exists. Defaults to `fulltext.db` next to the serving database.
    pub fulltext_path: PathBuf,
    /// `STATIC_DIR`: the built front end (`frontend/build`).
    pub static_dir: PathBuf,
    /// `BIND`: the address to listen on.
    pub bind: SocketAddr,
    /// `USAGE_PATH`: the usage statistics SQLite; `None` when `USAGE_ENABLED=false`.
    pub usage_path: Option<PathBuf>,
    /// `USAGE_VISITORS`: estimate daily visitors.
    pub usage_visitors: bool,
    /// `USAGE_TOKEN`: the token `GET /api/usage` asks for (at least 32 characters); without one
    /// the report is off.
    pub usage_token: Option<String>,
    /// `MCP_ALLOWED_HOSTS`: comma-separated `Host` values `/mcp` accepts (DNS rebinding guard).
    pub mcp_allowed_hosts: Vec<String>,
    /// `PUBLIC_URL`: the site's address in canonical links, the sitemap and `llms.txt`.
    pub public_url: String,
    /// `FORMER_HOSTS`: comma-separated former names of the site; their requests are sent on to
    /// `PUBLIC_URL`.
    pub former_hosts: Vec<String>,
    /// `SEARCH_INDEXING`: let search engines index the pages; off, every response says `noindex`.
    pub search_indexing: bool,
    /// `SEARCH_INDEX_COMMUNES`: let them index the commune pages too; off, those pages say
    /// `noindex` and stay out of the sitemap.
    pub search_index_communes: bool,
    /// The chatbot's model and limits (`ask_config`); `None` without `ASK_API_KEY`.
    pub ask: Option<AskConfig>,
}

impl Config {
    pub fn from_env() -> Result<Self, String> {
        let bind = var("BIND")
            .unwrap_or_else(|| "127.0.0.1:8000".into())
            .parse()
            .map_err(|e| format!("BIND: {e}"))?;
        let usage_enabled = flag("USAGE_ENABLED", true)?;
        let usage_token = var("USAGE_TOKEN").map(|t| t.trim().to_string());
        if usage_token.as_ref().is_some_and(|t| t.len() < 32) {
            return Err("USAGE_TOKEN: shorter than 32 characters".into());
        }
        let database_path: PathBuf = var("DATABASE_PATH")
            .unwrap_or_else(|| "data/opencommune.db".into())
            .into();
        Ok(Self {
            fulltext_path: var("FULLTEXT_PATH").map_or_else(
                || database_path.with_file_name("fulltext.db"),
                PathBuf::from,
            ),
            database_path,
            static_dir: var("STATIC_DIR")
                .unwrap_or_else(|| "frontend/build".into())
                .into(),
            bind,
            usage_path: usage_enabled.then(|| {
                var("USAGE_PATH")
                    .unwrap_or_else(|| "data/logs/usage.sqlite3".into())
                    .into()
            }),
            usage_visitors: flag("USAGE_VISITORS", true)?,
            usage_token,
            mcp_allowed_hosts: list("MCP_ALLOWED_HOSTS", "localhost,127.0.0.1,::1"),
            public_url: var("PUBLIC_URL").unwrap_or_else(|| format!("http://{bind}")),
            former_hosts: list("FORMER_HOSTS", ""),
            search_indexing: flag("SEARCH_INDEXING", false)?,
            search_index_communes: flag("SEARCH_INDEX_COMMUNES", false)?,
            ask: ask_config()?,
        })
    }
}

/// The chatbot, on when `ASK_API_KEY` is set:
/// - `ASK_PROVIDER`: `anthropic` (the default) or `openai` (any OpenAI-compatible endpoint);
/// - `ASK_MODEL`: defaults to `claude-haiku-5-5` for Anthropic, required otherwise;
/// - `ASK_BASE_URL`: the API root, when not the provider's own;
/// - `ASK_EFFORT`: reasoning `off`, `low` (the default), `medium` or `high`;
/// - `ASK_PRICE_INPUT`, `ASK_PRICE_OUTPUT`: dollars per million tokens (defaults 0.10 and 0.50,
///   Claude Haiku 5.5's prices), and `ASK_PRICE_INPUT_LONG`, `ASK_PRICE_OUTPUT_LONG` for calls
///   above 100,000 input tokens (defaults 0.50 and 2.50);
/// - `ASK_DAILY_USD`: dollars a day (UTC) before it stops for the day (default 1);
/// - `ASK_MODEL_CALLS_PER_MINUTE`: for a provider with a request limit (none by default);
/// - `ASK_OFF_FILE`: while this file exists, the chatbot is off;
/// - `ASK_PATH`: its SQLite file of questions and daily counts (default `data/logs/ask.sqlite3`).
fn ask_config() -> Result<Option<AskConfig>, String> {
    let Some(api_key) = var("ASK_API_KEY") else {
        return Ok(None);
    };
    let provider = match var("ASK_PROVIDER").as_deref().unwrap_or("anthropic") {
        "anthropic" => Provider::Anthropic,
        "openai" => Provider::OpenAi,
        other => {
            return Err(format!(
                "ASK_PROVIDER: {other:?} is neither anthropic nor openai"
            ));
        }
    };
    let model = match (var("ASK_MODEL"), provider) {
        (Some(model), _) => model,
        (None, Provider::Anthropic) => "claude-haiku-5-5".to_string(),
        (None, Provider::OpenAi) => {
            return Err("ASK_MODEL: required with ASK_PROVIDER=openai".into());
        }
    };
    let base_url = var("ASK_BASE_URL");
    let effort = var("ASK_EFFORT").unwrap_or_else(|| "low".into());
    let reasoning = match effort.as_str() {
        "off" => Reasoning::Off,
        "low" => Reasoning::Effort(Effort::Low),
        "medium" => Reasoning::Effort(Effort::Medium),
        "high" => Reasoning::Effort(Effort::High),
        other => {
            return Err(format!(
                "ASK_EFFORT: {other:?} is not off, low, medium or high"
            ));
        }
    };
    let model_calls_per_minute = match var("ASK_MODEL_CALLS_PER_MINUTE") {
        None => None,
        Some(n) => Some(
            n.parse::<u32>()
                .ok()
                .filter(|n| *n > 0)
                .ok_or("ASK_MODEL_CALLS_PER_MINUTE: not a positive whole number")?,
        ),
    };
    Ok(Some(AskConfig {
        provider,
        api_key,
        base_url,
        reasoning,
        settings: Settings {
            model,
            effort,
            price_input: dollars("ASK_PRICE_INPUT", 0.10)?,
            price_output: dollars("ASK_PRICE_OUTPUT", 0.50)?,
            price_input_long: dollars("ASK_PRICE_INPUT_LONG", 0.50)?,
            price_output_long: dollars("ASK_PRICE_OUTPUT_LONG", 2.50)?,
            daily_usd: dollars("ASK_DAILY_USD", 1.0)?,
            model_calls_per_minute,
            off_file: var("ASK_OFF_FILE").map(PathBuf::from),
            path: var("ASK_PATH")
                .unwrap_or_else(|| "data/logs/ask.sqlite3".into())
                .into(),
        },
    }))
}

/// A dollar amount, zero or more.
fn dollars(name: &str, default: f64) -> Result<f64, String> {
    match var(name) {
        None => Ok(default),
        Some(v) => v
            .trim()
            .parse::<f64>()
            .ok()
            .filter(|d| d.is_finite() && *d >= 0.0)
            .ok_or_else(|| format!("{name}: {v:?} is not an amount of dollars")),
    }
}

fn var(name: &str) -> Option<String> {
    env::var(name).ok().filter(|v| !v.trim().is_empty())
}

/// A comma-separated list.
fn list(name: &str, default: &str) -> Vec<String> {
    var(name)
        .unwrap_or_else(|| default.into())
        .split(',')
        .map(|h| h.trim().to_string())
        .filter(|h| !h.is_empty())
        .collect()
}

fn flag(name: &str, default: bool) -> Result<bool, String> {
    match var(name).map(|v| v.trim().to_ascii_lowercase()).as_deref() {
        None => Ok(default),
        Some("1" | "true" | "yes" | "on") => Ok(true),
        Some("0" | "false" | "no" | "off") => Ok(false),
        Some(other) => Err(format!("{name}: {other:?} is not a boolean")),
    }
}
