//! "Ask the archive": a visitor's question, answered by a language model that searches the archive
//! with the MCP server's own tools, served in-process. The model is a setting: Anthropic's Messages
//! API or any OpenAI-compatible endpoint. Design and abuse controls: `docs/CHATBOT.md`.
//!
//! Every question passes, in order: the off switch, the origin and client checks, the length
//! limits, a proof of work (`challenge`), the limits per visitor, address and hour (`limits`), the
//! daily dollar cap and a cap on questions running at once. Inside a run, `run` limits model calls,
//! tool calls, pages read and input tokens, and stops when the visitor leaves.

pub mod challenge;
pub mod limits;
pub mod run;
pub mod store;

use std::collections::HashMap;
use std::fmt;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use rig_agent::{Agent, AgentBuilder};
use rig_core::DynModel;
use rig_core::completion::{CacheRetention, GenerationOptions, Reasoning};
use rig_core::operation::Completion;
use rig_core::providers::anthropic::AnthropicConfig;
use rig_core::providers::openai::OpenAIConfig;
use rig_core::tool::DynamicTool;
use sha2::{Digest, Sha256};
use tokio::sync::Semaphore;

use crate::AppState;
use challenge::Challenges;
use limits::{CallRate, Limits};
use store::Store;

/// The system prompt. Each answer is stored with the version of the prompt, model and limits
/// that produced it.
const PROMPT: &str = include_str!("prompt.md");
/// Added to the prompt with the site's language, for input whose language is unclear (a keyword).
const LANGUAGE_HINT: &str = "The visitor uses the site in {language}. When the question's \
language is unclear, for example a single keyword, answer in {language}.";
/// Output tokens per model call, thinking included.
const MAX_TOKENS: u64 = 4000;
/// Questions answered at once; the next one is told to try again shortly.
const RUNNING: usize = 2;
/// Above this many input tokens a call is billed at the long-context prices (Claude Haiku 5.5).
const LONG_CONTEXT: u64 = 100_000;
/// Cache reads and writes, as a share of the input price (Anthropic's prompt caching).
const CACHE_READ: f64 = 0.1;
const CACHE_WRITE: f64 = 1.25;
/// What a call whose usage is unknown is counted as: a full request and a full answer.
const WORST_CALL_INPUT: u64 = 150_000;
/// Days an answer is given again to the same question in the same language, without the model.
pub const CACHE_DAYS: i64 = 7;
/// The searches the start page offers as examples (`frontend/src/lib/copy/start.ts`): the only
/// ones whose answers are kept and given again. Kept answers to anything a visitor typed would
/// let anyone find out whether someone asked a given question, a name in it included.
pub const EXAMPLES: [&str; 16] = [
    "Tempo 30",
    "Photovoltaik",
    "Schulhaus",
    "Hallenbad",
    "Welche Gemeinden im Kanton Bern haben über Tempo 30 diskutiert?",
    "Zone 30",
    "Photovoltaïque",
    "Collège",
    "Piscine",
    "Quelles communes vaudoises ont discuté d’éteindre l’éclairage public la nuit ?",
    "Zona 30",
    "Fotovoltaico",
    "Scuola",
    "Piscina",
    "Qual è il moltiplicatore d’imposta di Bellinzona per il 2026?",
    "Which municipalities in canton Bern discussed Tempo 30?",
];
/// A call cut off before it answered is counted with this many output tokens, and as its first
/// call with this many input tokens (the prompt, the tools and the question).
const CUT_OUTPUT: u64 = 1_000;
pub const FIRST_CALL_INPUT: u64 = 8_000;
/// How often the counts of refused and cached questions are written.
const FLUSH: Duration = Duration::from_secs(60);
/// How long answers pause when the provider refuses for want of credit or a spend limit; then the
/// next question tries again, so a top-up takes effect without a restart.
pub const PAUSE: Duration = Duration::from_secs(15 * 60);

/// The API a model is reached through.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Provider {
    /// Anthropic's Messages API.
    Anthropic,
    /// An OpenAI-compatible chat-completions endpoint (for example the CSCS inference platform).
    OpenAi,
}

/// The model and its key, from the environment (`config.rs`).
#[derive(Clone)]
pub struct AskConfig {
    pub provider: Provider,
    pub api_key: String,
    /// The API root, when not the provider's own.
    pub base_url: Option<String>,
    pub reasoning: Reasoning,
    pub settings: Settings,
}

impl fmt::Debug for AskConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("AskConfig")
            .field("provider", &self.provider)
            .field("api_key", &"…")
            .field("base_url", &self.base_url)
            .field("reasoning", &self.reasoning)
            .field("settings", &self.settings)
            .finish()
    }
}

/// Everything but the model's address and key.
#[derive(Clone, Debug)]
pub struct Settings {
    pub model: String,
    /// The reasoning setting, as configured, for the prompt version.
    pub effort: String,
    /// Dollars per million input and output tokens, and above `LONG_CONTEXT` input tokens.
    pub price_input: f64,
    pub price_output: f64,
    pub price_input_long: f64,
    pub price_output_long: f64,
    /// Dollars the chatbot may spend in a day (UTC).
    pub daily_usd: f64,
    /// Model calls a minute, for a provider with a request limit.
    pub model_calls_per_minute: Option<u32>,
    /// While this file exists, the chatbot is off.
    pub off_file: Option<PathBuf>,
    /// The SQLite file of questions and daily counts.
    pub path: PathBuf,
}

pub struct Ask {
    agent: Agent,
    settings: Settings,
    prompt_version: String,
    pub challenges: Challenges,
    pub limits: Limits,
    call_rate: CallRate,
    pub store: Store,
    /// Today's day (UTC) and the dollars spent on it.
    spent: Mutex<(String, f64)>,
    pub running: Arc<Semaphore>,
    /// Questions refused or answered from the cache since the last write, by outcome: counted in
    /// memory so that a flood of them costs no write each.
    counts: Arc<Mutex<HashMap<String, i64>>>,
    /// Until when answers pause because the provider refused for want of money.
    paused_until: Mutex<Option<std::time::Instant>>,
}

impl Ask {
    /// The chatbot on the configured model, with the archive's tools in-process.
    pub async fn start(state: AppState, config: AskConfig) -> Result<Arc<Self>, String> {
        let tools = crate::mcp::chat_tools(state).await?;
        let model = &config.settings.model;
        let model: DynModel<Completion> = match config.provider {
            Provider::Anthropic => {
                let mut provider = AnthropicConfig::new(config.api_key.clone());
                if let Some(url) = &config.base_url {
                    provider = provider.with_base_url(url);
                }
                provider.client().completion(model.clone()).into()
            }
            Provider::OpenAi => {
                let mut provider = OpenAIConfig::new(config.api_key.clone());
                if let Some(url) = &config.base_url {
                    provider = provider.with_base_url(url.clone());
                }
                provider.client().chat(model.clone()).into()
            }
        };
        let mut options = GenerationOptions::new().reasoning(config.reasoning);
        // each round resends what came before: cached, it costs a tenth (Anthropic)
        if config.provider == Provider::Anthropic {
            options = options.cache(CacheRetention::Short);
        }
        Self::open(model, tools, options, config.settings).await
    }

    /// The chatbot on `model` with `tools`; `start` for a configured provider, a mock in tests.
    pub async fn open(
        model: impl Into<DynModel<Completion>>,
        tools: Vec<DynamicTool>,
        options: GenerationOptions,
        settings: Settings,
    ) -> Result<Arc<Self>, String> {
        let agent = AgentBuilder::new(model)
            .preamble(PROMPT)
            .max_tokens(MAX_TOKENS)
            .options(options)
            .dynamic_tools(tools)
            .build();
        let store = Store::open(&settings.path)
            .await
            .map_err(|e| format!("{}: {e}", settings.path.display()))?;
        let spent = store
            .spent_today()
            .await
            .map_err(|e| format!("{}: {e}", settings.path.display()))?;
        let prompt_version = prompt_version(&settings);
        let counts: Arc<Mutex<HashMap<String, i64>>> = Arc::default();
        {
            let (store, counts) = (store.clone(), counts.clone());
            tokio::spawn(async move {
                loop {
                    tokio::time::sleep(FLUSH).await;
                    flush(&store, &counts).await;
                }
            });
        }
        Ok(Arc::new(Self {
            agent,
            call_rate: CallRate::new(settings.model_calls_per_minute),
            settings,
            prompt_version,
            challenges: Challenges::new(),
            limits: Limits::new(),
            store,
            spent: Mutex::new((today(), spent)),
            running: Arc::new(Semaphore::new(RUNNING)),
            counts,
            paused_until: Mutex::new(None),
        }))
    }

    /// The key an answer to `question` is kept under: its `cache_key` with the prompt version, so
    /// that a new prompt, model or limit gives new answers rather than old ones.
    pub fn kept_key(&self, question: &str) -> String {
        format!("{}:{}", self.prompt_version, cache_key(question))
    }

    /// Pause answers for `PAUSE`: the provider refused for want of credit or a spend limit.
    pub fn pause(&self) {
        *self.paused_until.lock().expect("not poisoned") = Some(std::time::Instant::now() + PAUSE);
    }

    /// Whether answers pause (`pause`).
    pub fn paused(&self) -> bool {
        self.paused_until
            .lock()
            .expect("not poisoned")
            .is_some_and(|until| std::time::Instant::now() < until)
    }

    /// Count a question that was refused or answered from the cache (`outcome`), in memory.
    pub fn count(&self, outcome: &str) {
        *self
            .counts
            .lock()
            .expect("not poisoned")
            .entry(outcome.to_string())
            .or_default() += 1;
    }

    /// Write the counts kept in memory now (they are written every minute).
    pub async fn flush(&self) {
        flush(&self.store, &self.counts).await;
    }

    /// Whether the chatbot takes questions: not while the off file exists, nor when it cannot be
    /// told whether it exists (a folder the service may not read): off is the safe side.
    pub fn is_on(&self) -> bool {
        match &self.settings.off_file {
            None => true,
            Some(file) => matches!(
                std::fs::metadata(file),
                Err(e) if e.kind() == std::io::ErrorKind::NotFound
            ),
        }
    }

    /// Whether `question` is one of the start page's examples (`EXAMPLES`).
    pub fn is_example(question: &str) -> bool {
        let key = cache_key(question);
        EXAMPLES.iter().any(|example| cache_key(example) == key)
    }

    /// Dollars spent today.
    pub fn spent(&self) -> f64 {
        let mut spent = self.spent.lock().expect("not poisoned");
        let day = today();
        if spent.0 != day {
            *spent = (day, 0.0);
        }
        spent.1
    }

    fn add_spent(&self, usd: f64) {
        self.spent();
        self.spent.lock().expect("not poisoned").1 += usd;
    }

    /// Whether today's dollars are spent.
    pub fn used_up(&self) -> bool {
        self.spent() >= self.settings.daily_usd
    }

    /// Dollars for one call: `input` tokens in all, of them `read` from the cache and `written` to
    /// it, and `output` tokens; at the long-context prices above `LONG_CONTEXT` input tokens.
    fn cost(&self, input: u64, read: u64, written: u64, output: u64) -> f64 {
        let s = &self.settings;
        let (price_in, price_out) = if input > LONG_CONTEXT {
            (s.price_input_long, s.price_output_long)
        } else {
            (s.price_input, s.price_output)
        };
        let uncached = input.saturating_sub(read.saturating_add(written));
        (uncached as f64 * price_in
            + read as f64 * price_in * CACHE_READ
            + written as f64 * price_in * CACHE_WRITE
            + output as f64 * price_out)
            / 1e6
    }

    /// What a call whose usage is unknown is counted as.
    fn worst_call(&self) -> f64 {
        self.cost(WORST_CALL_INPUT, 0, 0, MAX_TOKENS)
    }

    /// What a call cut off by the timeout or the visitor leaving is counted as: the `input` tokens
    /// it was sent (estimated by `run`) and some output.
    fn cut_call(&self, input: u64) -> f64 {
        self.cost(input, 0, 0, CUT_OUTPUT)
    }
}

/// The version an answer is stored with: the prompt, the model, its reasoning and the limits.
fn prompt_version(settings: &Settings) -> String {
    let mut hash = Sha256::new();
    for part in [
        PROMPT,
        LANGUAGE_HINT,
        &settings.model,
        &settings.effort,
        &MAX_TOKENS.to_string(),
        &run::limits_label(),
    ] {
        hash.update(part.as_bytes());
        hash.update(b"\0");
    }
    challenge::hex(&hash.finalize())[..12].to_string()
}

/// Whether a provider's refusal means the money is used up: the credit balance, a spend or usage
/// limit, a quota or a budget (Anthropic: 400 "credit balance is too low", 400 "reached your
/// specified API usage limits"; other providers: 402, or 403/429 naming a quota or budget). Rate
/// limits are not: they pass within a minute.
pub fn out_of_money(status: Option<u16>, body: &str) -> bool {
    let body = body.to_lowercase();
    if body.contains("rate_limit") || body.contains("rate limit") {
        return false;
    }
    status == Some(402)
        || (matches!(status, Some(400 | 403 | 429))
            && [
                "credit balance",
                "usage limit",
                "spend limit",
                "billing",
                "insufficient",
                "quota",
                "budget",
            ]
            .iter()
            .any(|word| body.contains(word)))
}

/// Write the counts of refused and cached questions kept in memory.
async fn flush(store: &Store, counts: &Mutex<HashMap<String, i64>>) {
    let taken = std::mem::take(&mut *counts.lock().expect("not poisoned"));
    for (outcome, n) in taken {
        if let Err(error) = store.count(&outcome, n, 0.0).await {
            tracing::warn!(%error, "ask: counts could not be stored");
        }
    }
}

/// The key under which an answer is kept for the same question: lower case, single spaces, without
/// the closing punctuation.
pub fn cache_key(question: &str) -> String {
    question
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase()
        .trim_end_matches(['?', '!', '.', '…', ' '])
        .to_string()
}

/// The system prompt for a question asked on the site in `lang` (de, fr, it or en).
fn prompt_for(lang: Option<&str>) -> String {
    let language = match lang {
        Some("de") => "German",
        Some("fr") => "French",
        Some("it") => "Italian",
        Some("en") => "English",
        _ => return PROMPT.to_string(),
    };
    format!(
        "{PROMPT}\n{}\n",
        LANGUAGE_HINT.replace("{language}", language)
    )
}

/// Seconds since the Unix epoch.
pub fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_secs())
}

/// Today's date (UTC), as SQLite's `date('now')` writes it.
pub fn today() -> String {
    utc_day(now())
}

/// The UTC date of a Unix time, `YYYY-MM-DD`.
fn utc_day(unix: u64) -> String {
    // days to civil date (H. Hinnant)
    let z = (unix / 86_400) as i64 + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!("{year:04}-{month:02}-{day:02}")
}

#[cfg(test)]
mod tests {
    use super::utc_day;

    #[test]
    fn refusals_for_want_of_money_are_told_from_others() {
        use super::out_of_money;
        let anthropic = r#"{"type":"error","error":{"type":"invalid_request_error","message":"You have reached your specified API usage limits. You will regain access on 2026-12-01 at 00:00 UTC."}}"#;
        assert!(out_of_money(Some(400), anthropic));
        assert!(out_of_money(
            Some(400),
            "Your credit balance is too low to access the Anthropic API."
        ));
        assert!(out_of_money(Some(402), ""));
        assert!(out_of_money(Some(429), "budget exceeded for this key"));
        assert!(!out_of_money(
            Some(429),
            r#"{"error":{"type":"rate_limit_error"}}"#
        ));
        assert!(!out_of_money(
            Some(400),
            "messages: text content blocks must be non-empty"
        ));
        assert!(!out_of_money(Some(500), "internal error"));
        assert!(!out_of_money(None, "usage limit"));
    }

    #[test]
    fn the_examples_are_the_start_pages() {
        // each example search of the start page, in every language, is one the server keeps
        let start = include_str!("../../../frontend/src/lib/copy/start.ts");
        let mut offered = Vec::new();
        for line in start.lines().map(str::trim) {
            if let Some(list) = line.strip_prefix("exampleQueries: [") {
                offered.extend(
                    list.trim_end_matches("],")
                        .split(", ")
                        .map(|q| q.trim_matches('\'').to_string()),
                );
            } else if let Some(question) = line.strip_prefix("askExample: '") {
                offered.push(question.trim_end_matches("',").to_string());
            }
        }
        assert_eq!(offered.len(), 20);
        for example in &offered {
            assert!(super::Ask::is_example(example), "{example}");
        }
        assert!(super::Ask::is_example("tempo 30"));
        assert!(!super::Ask::is_example("Tempo 30 in Bern"));
    }

    #[test]
    fn the_same_question_has_the_same_key() {
        use super::cache_key;
        assert_eq!(
            cache_key("  Welche Gemeinden  haben über Tempo 30 diskutiert? "),
            cache_key("welche gemeinden haben über tempo 30 diskutiert")
        );
        assert_ne!(cache_key("Tempo 30"), cache_key("Tempo 40"));
    }

    #[test]
    fn the_site_language_is_the_fallback_for_an_unclear_question() {
        assert_eq!(super::prompt_for(None), super::PROMPT);
        assert_eq!(super::prompt_for(Some("xx")), super::PROMPT);
        let french = super::prompt_for(Some("fr"));
        assert!(french.starts_with(super::PROMPT));
        assert!(french.contains("uses the site in French"));
    }

    #[test]
    fn days_are_utc_dates() {
        assert_eq!(utc_day(0), "1970-01-01");
        assert_eq!(utc_day(951_782_400), "2000-02-29");
        assert_eq!(utc_day(1_795_996_799), "2026-11-29");
        assert_eq!(utc_day(1_795_996_800), "2026-11-30");
    }
}
