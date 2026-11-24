//! One question: the model run with its limits, the steps sent to the page as they happen, the
//! answer, and what is kept of it.

use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use rig_agent::agent::{
    AgentHook, CompletionCallAction, CompletionCallEvent, DispatchAction, DispatchEvent,
    HookContext, OutcomeAction, OutcomeEvent,
};
use rig_agent::completion::PromptError;
use rig_core::effect::EffectKind;
use serde_json::{Map, Value, json};
use tokio::sync::mpsc;

use super::Ask;
use super::store::Entry;

/// Model calls per question.
pub const MAX_TURNS: usize = 6;
/// Tool calls per question; later ones are skipped and the model answers with what it has.
pub const MAX_TOOL_CALLS: u32 = 8;
/// Pages `read_pages` may read per question, and per call.
pub const MAX_PAGES: u64 = 30;
const MAX_PAGES_PER_CALL: u64 = 5;
/// Input tokens per question, summed over its model calls.
pub const MAX_INPUT_TOKENS: u64 = 300_000;
/// Characters of one tool result the model reads; the rest is cut with a note.
pub const MAX_RESULT_CHARS: usize = 40_000;
/// Characters of all tool results of a question: about three large searches. It keeps each model
/// call well below 100,000 input tokens, where the long-context prices begin.
pub const MAX_RESULTS_CHARS: u64 = 150_000;
/// The longest a question may take.
pub const TIMEOUT: Duration = Duration::from_secs(120);
/// The longest a model call waits for the provider's request limit.
const PATIENCE: Duration = Duration::from_secs(30);

/// The limits as they enter the prompt version.
pub fn limits_label() -> String {
    format!(
        "{MAX_TURNS}/{MAX_TOOL_CALLS}/{MAX_PAGES}/{MAX_PAGES_PER_CALL}/{MAX_INPUT_TOKENS}/{MAX_RESULT_CHARS}/{MAX_RESULTS_CHARS}"
    )
}

/// What the page receives, in order: steps, then the answer if there is one, then the end.
#[derive(Debug, Clone, PartialEq)]
pub enum Event {
    /// A tool call, with its arguments as they ran.
    Step { tool: String, args: Value },
    /// The answer's text (Markdown links, bold and lists).
    Answer(String),
    /// How the question ended: `answered`, `too_broad`, `used_up`, `budget`, `busy`, `timeout`,
    /// `cancelled`, `empty`, `no_credit` (the provider refused for want of money: answers pause)
    /// or `failed`.
    End(&'static str),
}

#[derive(Default, Clone)]
struct Tally {
    tool_calls: u32,
    pages: u64,
    model_calls: u32,
    input: u64,
    output: u64,
    usd: f64,
    steps: Vec<Value>,
    /// Why a check in the run stopped it.
    stop: Option<&'static str>,
    /// A model call was sent and has not answered: if the run ends now, it is billed anyway.
    in_flight: bool,
    /// The input tokens of the last model call, and the characters of tool results since, for
    /// estimating a call that is cut off.
    last_input: u64,
    pending_chars: u64,
    /// Characters of all tool results the model has read in this question.
    result_chars: u64,
}

/// The run's checks: before each model call, after each response, before each tool call.
struct Guard {
    ask: Arc<Ask>,
    tally: Arc<Mutex<Tally>>,
    events: mpsc::Sender<Event>,
}

impl Guard {
    fn stop(&self, reason: &'static str) -> CompletionCallAction {
        self.tally.lock().expect("not poisoned").stop = Some(reason);
        CompletionCallAction::stop(reason)
    }
}

/// Lower `key` of `args` to `max` when it is above it; whether it changed.
fn cap(args: &mut Map<String, Value>, key: &str, max: u64) -> bool {
    match args.get(key).and_then(Value::as_u64) {
        Some(n) if n > max => {
            args.insert(key.to_string(), json!(max));
            true
        }
        _ => false,
    }
}

/// A tool call's arguments with the chatbot's smaller maxima, when they differ: a search may ask
/// for at most 30 documents and 2 pages each (about 45,000 characters), a page reading for at most
/// 5 pages. A whole question stays at a few cents even when each of its searches is that large.
fn smaller(tool: &str, args: &Value) -> Option<Value> {
    let mut args = args.clone();
    let map = args.as_object_mut()?;
    let changed = match tool {
        "search_text" => {
            cap(map, "limit", 30)
                | cap(map, "pages_per_document", 2)
                | cap(map, "commune_facets", 50)
                | cap(map, "offset", 200)
        }
        "search_documents" => cap(map, "limit", 30) | cap(map, "offset", 200),
        "read_pages" => {
            let from = map.get("from").and_then(Value::as_u64).unwrap_or(1);
            let last = from.saturating_add(MAX_PAGES_PER_CALL - 1);
            // without `to` the tool reads ten pages
            if map.get("to").and_then(Value::as_u64).is_none() {
                map.insert("to".to_string(), json!(last));
                true
            } else {
                cap(map, "to", last)
            }
        }
        _ => false,
    };
    changed.then_some(args)
}

/// Pages a `read_pages` call reads (the tool reads 10 from `from` without a `to`).
fn pages(args: &Value) -> u64 {
    let from = args.get("from").and_then(Value::as_u64).unwrap_or(1);
    let to = args
        .get("to")
        .and_then(Value::as_u64)
        .unwrap_or(from.saturating_add(9));
    to.saturating_sub(from).saturating_add(1)
}

impl AgentHook for Guard {
    async fn on_completion_call(
        &self,
        _: &HookContext,
        _: CompletionCallEvent<'_>,
    ) -> CompletionCallAction {
        // a visitor who left pays nothing more
        if self.events.is_closed() {
            return self.stop("cancelled");
        }
        if self.ask.used_up() {
            return self.stop("used_up");
        }
        if self.tally.lock().expect("not poisoned").input >= MAX_INPUT_TOKENS {
            return self.stop("budget");
        }
        if !self.ask.call_rate.wait(PATIENCE).await {
            return self.stop("busy");
        }
        self.tally.lock().expect("not poisoned").in_flight = true;
        CompletionCallAction::continue_run()
    }

    async fn on_outcome(&self, _: &HookContext, event: OutcomeEvent<'_>) -> OutcomeAction {
        if let Some(response) = event.completion() {
            let usage = &response.usage;
            // a call without usage counts as the most a call can cost: the cap must not fail open
            let usd = match usage.input_tokens {
                Some(input) => self.ask.cost(
                    input,
                    usage.cached_input_tokens.unwrap_or(0),
                    usage.cache_creation_input_tokens.unwrap_or(0),
                    usage.output_tokens.unwrap_or(0),
                ),
                None => self.ask.worst_call(),
            };
            self.ask.add_spent(usd);
            let mut tally = self.tally.lock().expect("not poisoned");
            tally.in_flight = false;
            tally.last_input = usage.input_tokens.unwrap_or(0);
            tally.pending_chars = 0;
            tally.model_calls += 1;
            tally.input += usage.input_tokens.unwrap_or(0);
            tally.output += usage.output_tokens.unwrap_or(0);
            tally.usd += usd;
            return OutcomeAction::proceed();
        }
        if let Some(result) = event.tool_result() {
            let text: String = result
                .output()
                .as_content()
                .iter()
                .map(|c| c.to_text("[image]"))
                .collect::<Vec<_>>()
                .join("\n");
            let chars = text.chars().count();
            let read = chars.min(MAX_RESULT_CHARS) as u64;
            {
                let mut tally = self.tally.lock().expect("not poisoned");
                if tally.result_chars + read > MAX_RESULTS_CHARS {
                    return OutcomeAction::rewrite_tool_result(
                        &event,
                        "The limit of search results for one question is reached: answer with what you have.",
                    );
                }
                tally.result_chars += read;
                tally.pending_chars += read;
            }
            if chars > MAX_RESULT_CHARS {
                let mut cut: String = text.chars().take(MAX_RESULT_CHARS).collect();
                cut.push_str(
                    "\n[The result is cut here: narrow the search with a commune, a canton or a year.]",
                );
                return OutcomeAction::rewrite_tool_result(&event, cut);
            }
        }
        OutcomeAction::proceed()
    }

    async fn on_dispatch(&self, _: &HookContext, event: DispatchEvent<'_>) -> DispatchAction {
        let EffectKind::ToolCall { name, args } = event.kind else {
            return DispatchAction::proceed();
        };
        let asked: Value = serde_json::from_str(args).unwrap_or(Value::Null);
        let smaller = smaller(name, &asked);
        let args = smaller.clone().unwrap_or(asked);
        {
            let mut tally = self.tally.lock().expect("not poisoned");
            tally.tool_calls += 1;
            if tally.tool_calls > MAX_TOOL_CALLS {
                return DispatchAction::skip(
                    "The limit of searches for one question is reached: answer with what you have.",
                );
            }
            if name == "read_pages" {
                let pages = tally.pages.saturating_add(pages(&args));
                if pages > MAX_PAGES {
                    return DispatchAction::skip(
                        "The limit of pages for one question is reached: answer with what you have.",
                    );
                }
                tally.pages = pages;
            }
            tally.steps.push(json!({"tool": name, "args": args}));
        }
        let _ = self.events.try_send(Event::Step {
            tool: name.clone(),
            args: args.clone(),
        });
        match smaller {
            Some(args) => DispatchAction::rewrite_tool_args(event.kind, args),
            None => DispatchAction::proceed(),
        }
    }
}

/// The input tokens of a call cut off before it answered: the first call's prompt, tools and
/// question, or the last call's input with the tool results since (about 3 characters a token).
fn cut_input(tally: &Tally) -> u64 {
    if tally.model_calls == 0 {
        super::FIRST_CALL_INPUT
    } else {
        tally.last_input + tally.pending_chars / 3
    }
}

/// Answer `question`, sending the steps, the answer and the end to `events`, then keep it. The
/// run stops as soon as the visitor leaves, even in the middle of a model call.
pub async fn answer(
    ask: Arc<Ask>,
    question: String,
    lang: Option<String>,
    events: mpsc::Sender<Event>,
) {
    let tally = Arc::new(Mutex::new(Tally::default()));
    let guard = Guard {
        ask: ask.clone(),
        tally: tally.clone(),
        events: events.clone(),
    };
    let started = Instant::now();
    let run = ask
        .agent
        .prompt(question.clone())
        .preamble(super::prompt_for(lang.as_deref()))
        .add_hook(guard)
        .max_turns(MAX_TURNS);
    let result = tokio::select! {
        result = tokio::time::timeout(TIMEOUT, run) => Some(result),
        () = events.closed() => None,
    };
    let mut tally = tally.lock().expect("not poisoned").clone();
    // a call cut off by the timeout or the visitor leaving is billed all the same, by its size
    if tally.in_flight {
        let usd = ask.cut_call(cut_input(&tally));
        ask.add_spent(usd);
        tally.usd += usd;
    }
    let (outcome, answer) = match result {
        None => ("cancelled", None),
        Some(Err(_)) => ("timeout", None),
        Some(Ok(Ok(response))) => {
            let text = response.output().trim().to_string();
            if text.is_empty() {
                ("empty", None)
            } else {
                ("answered", Some(text))
            }
        }
        Some(Ok(Err(PromptError::MaxTurns { .. }))) => ("too_broad", None),
        Some(Ok(Err(error))) => match tally.stop {
            Some(reason) => (reason, None),
            None if super::out_of_money(
                error.provider_response_status().map(|s| s.as_u16()),
                error.provider_response_body().unwrap_or(""),
            ) =>
            {
                ask.pause();
                tracing::error!(
                    %error,
                    "ask: the model provider refused for want of credit or a spend limit; \
                     answers pause for 15 minutes, then the next question tries again"
                );
                ("no_credit", None)
            }
            None => {
                tracing::warn!(%error, "ask: the model run failed");
                ("failed", None)
            }
        },
    };
    if let Some(text) = &answer {
        let _ = events.send(Event::Answer(text.clone())).await;
    }
    let _ = events.send(Event::End(outcome)).await;
    let steps = Value::Array(tally.steps);
    if let Some(text) = &answer
        && Ask::is_example(&question)
        && let Err(error) = ask
            .store
            .remember(&ask.kept_key(&question), lang.as_deref(), text, &steps)
            .await
    {
        tracing::warn!(%error, "ask: the answer could not be kept for others");
    }
    if let Err(error) = ask.store.count(outcome, 1, tally.usd).await {
        tracing::warn!(%error, "ask: the daily count could not be stored");
    }
    let entry = Entry {
        lang,
        question,
        answer,
        steps,
        outcome,
        prompt_version: ask.prompt_version.clone(),
        model: ask.settings.model.clone(),
        model_calls: tally.model_calls,
        input_tokens: tally.input,
        output_tokens: tally.output,
        usd: tally.usd,
        duration_ms: u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX),
    };
    if let Err(error) = ask.store.log(entry).await {
        tracing::warn!(%error, "ask: the question could not be stored");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_cut_off_call_counts_by_the_size_it_was_sent() {
        let first = Tally::default();
        assert_eq!(cut_input(&first), crate::ask::FIRST_CALL_INPUT);
        let later = Tally {
            model_calls: 2,
            last_input: 20_000,
            pending_chars: 30_000,
            ..Tally::default()
        };
        assert_eq!(cut_input(&later), 30_000);
    }

    #[test]
    fn searches_and_readings_are_made_smaller_and_pages_never_overflow() {
        let args = json!({"q": "x", "limit": 50, "pages_per_document": 20, "commune_facets": 3000});
        assert_eq!(
            smaller("search_text", &args),
            Some(json!({"q": "x", "limit": 30, "pages_per_document": 2, "commune_facets": 50}))
        );
        assert_eq!(
            smaller("search_text", &json!({"q": "x", "limit": 20})),
            None
        );
        assert_eq!(
            smaller("read_pages", &json!({"sha256": "a", "from": 3, "to": 40})),
            Some(json!({"sha256": "a", "from": 3, "to": 7}))
        );
        assert_eq!(
            smaller("read_pages", &json!({"sha256": "a", "from": 3})),
            Some(json!({"sha256": "a", "from": 3, "to": 7}))
        );
        assert_eq!(pages(&json!({"from": 3, "to": 7})), 5);
        assert_eq!(pages(&json!({"from": 9, "to": 2})), 1);
        assert_eq!(pages(&json!({"from": 1})), 10);
        assert_eq!(pages(&json!({"from": 1, "to": u64::MAX})), u64::MAX);
        assert_eq!(pages(&json!({"from": u64::MAX})), 1);
    }
}
