//! The chatbot's endpoints: `GET /api/ask/challenge` hands out a proof of work, `POST /api/ask`
//! takes a question with its solution and answers with server-sent events (`step`, `answer`,
//! `end`). Refusals are JSON `{"detail", "reason"}` with the reason as a code the page translates.
//!
//! The start page's example searches (`ask::EXAMPLES`) are answered once and then given again for
//! `ask::CACHE_DAYS` days without the model: after the proof of work, against no limit.

use std::convert::Infallible;
use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::Json;
use axum::body::to_bytes;
use axum::extract::{ConnectInfo, Request, State};
use axum::http::{HeaderMap, HeaderValue, StatusCode, header};
use axum::response::sse::{Event as SseEvent, KeepAlive, Sse};
use axum::response::{IntoResponse, Response};
use serde::Deserialize;
use serde_json::{Value, json};
use tokio::sync::mpsc;
use tokio_stream::StreamExt;
use tokio_stream::wrappers::ReceiverStream;

use crate::AppState;
use crate::ask::challenge::BITS;
use crate::ask::run::{self, Event};
use crate::ask::{self, Ask};
use crate::usage::{bot_name, client_address};

/// Characters a question may have.
const MIN_CHARS: usize = 3;
const MAX_CHARS: usize = 500;
/// Bytes a request body may have, and how long it may take to arrive.
const MAX_BODY: usize = 8 * 1024;
const BODY_TIME: Duration = Duration::from_secs(5);

#[derive(Deserialize)]
struct Question {
    question: String,
    #[serde(default)]
    lang: Option<String>,
    #[serde(default)]
    challenge: String,
    #[serde(default)]
    solution: String,
}

fn refuse(status: StatusCode, reason: &'static str, detail: &str) -> Response {
    let mut response =
        (status, Json(json!({ "detail": detail, "reason": reason }))).into_response();
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response
}

/// The chatbot when it is configured and on.
fn on(state: &AppState) -> Option<Arc<Ask>> {
    state.ask.clone().filter(|ask| ask.is_on())
}

fn off() -> Response {
    refuse(StatusCode::SERVICE_UNAVAILABLE, "off", "the chatbot is off")
}

fn user_agent(headers: &HeaderMap) -> &str {
    headers
        .get(header::USER_AGENT)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
}

fn address(peer: Option<&ConnectInfo<std::net::SocketAddr>>, headers: &HeaderMap) -> String {
    client_address(peer.map(|c| c.0), headers).unwrap_or_default()
}

/// Whether `origin` (`scheme://host[:port]`) is this site, a former name of it, or a local
/// development server.
fn own_origin(state: &AppState, origin: &str) -> bool {
    let Some((_, rest)) = origin.split_once("://") else {
        return false;
    };
    let host = rest.split([':', '/']).next().unwrap_or("");
    state.site.is_own_host(host) || matches!(host, "localhost" | "127.0.0.1" | "[::1]")
}

/// `GET /api/ask/challenge`
pub async fn challenge(State(state): State<AppState>, request: Request) -> Response {
    let Some(ask) = on(&state) else {
        return off();
    };
    let headers = request.headers();
    if bot_name(user_agent(headers)).is_some() {
        return refuse(
            StatusCode::FORBIDDEN,
            "client",
            "automated clients cannot ask",
        );
    }
    let address = address(request.extensions().get(), headers);
    let mut response = Json(json!({
        "challenge": ask.challenges.issue(&address, ask::now()),
        "bits": BITS,
    }))
    .into_response();
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response
}

/// `POST /api/ask`
pub async fn ask(State(state): State<AppState>, request: Request) -> Response {
    let Some(ask) = on(&state) else {
        return off();
    };
    let (parts, body) = request.into_parts();
    // refusals are counted in memory and written once a minute
    let refused = |status, reason: &'static str, detail: &str| {
        ask.count(&format!("refused:{reason}"));
        refuse(status, reason, detail)
    };
    let origin = parts
        .headers
        .get(header::ORIGIN)
        .and_then(|v| v.to_str().ok());
    if !origin.is_some_and(|o| own_origin(&state, o)) {
        return refused(
            StatusCode::FORBIDDEN,
            "origin",
            "questions come from the site only",
        );
    }
    let user_agent = user_agent(&parts.headers).to_string();
    if bot_name(&user_agent).is_some() {
        return refused(
            StatusCode::FORBIDDEN,
            "client",
            "automated clients cannot ask",
        );
    }
    let bytes = match tokio::time::timeout(BODY_TIME, to_bytes(body, MAX_BODY)).await {
        Ok(Ok(bytes)) => bytes,
        Ok(Err(_)) => {
            return refused(
                StatusCode::PAYLOAD_TOO_LARGE,
                "body",
                "the request is too large",
            );
        }
        Err(_) => {
            return refused(
                StatusCode::REQUEST_TIMEOUT,
                "slow",
                "the request was too slow",
            );
        }
    };
    let Ok(question) = serde_json::from_slice::<Question>(&bytes) else {
        return refused(StatusCode::BAD_REQUEST, "body", "not a question");
    };
    let text = question.question.trim().to_string();
    let chars = text.chars().count();
    if chars < MIN_CHARS {
        return refused(
            StatusCode::BAD_REQUEST,
            "short",
            "the question is too short",
        );
    }
    if chars > MAX_CHARS {
        return refused(StatusCode::BAD_REQUEST, "long", "the question is too long");
    }
    let lang = question
        .lang
        .filter(|l| matches!(l.as_str(), "de" | "fr" | "it" | "en"));
    let address = address(parts.extensions.get(), &parts.headers);
    if ask
        .challenges
        .accept(
            &question.challenge,
            &question.solution,
            &address,
            ask::now(),
        )
        .is_err()
    {
        return refused(
            StatusCode::FORBIDDEN,
            "challenge",
            "the proof of work is missing",
        );
    }
    // an example search answered lately: given again, free, counted in memory only
    if Ask::is_example(&text) {
        match ask
            .store
            .cached(&ask.kept_key(&text), lang.as_deref())
            .await
        {
            Ok(Some((answer, steps))) => {
                ask.count("cached");
                return replay(answer, steps);
            }
            Ok(None) => {}
            Err(error) => tracing::warn!(%error, "ask: the answer cache could not be read"),
        }
    }
    // the provider refused for want of money a short while ago: no question goes out
    if ask.paused() {
        return refused(
            StatusCode::TOO_MANY_REQUESTS,
            "no_credit",
            "answers are not available right now",
        );
    }
    if ask.used_up() {
        return refused(
            StatusCode::TOO_MANY_REQUESTS,
            "used_up",
            "today's questions are used up",
        );
    }
    let Ok(permit) = ask.running.clone().try_acquire_owned() else {
        return refused(
            StatusCode::SERVICE_UNAVAILABLE,
            "busy",
            "try again in a minute",
        );
    };
    // only a question that will run takes a place in the limits
    if let Err(limit) = ask
        .limits
        .take(&ask::today(), &address, &user_agent, Instant::now())
    {
        return refused(
            StatusCode::TOO_MANY_REQUESTS,
            limit.code(),
            "too many questions",
        );
    }
    let (events, receiver) = mpsc::channel(32);
    tokio::spawn(async move {
        let _permit = permit;
        run::answer(ask, text, lang, events).await;
    });
    stream(ReceiverStream::new(receiver))
}

/// A kept answer as the events of a run: its steps, the answer, the end.
fn replay(answer: String, steps: Value) -> Response {
    let mut events: Vec<Event> = steps
        .as_array()
        .into_iter()
        .flatten()
        .map(|step| Event::Step {
            tool: step["tool"].as_str().unwrap_or("").to_string(),
            args: step["args"].clone(),
        })
        .collect();
    events.push(Event::Answer(answer));
    events.push(Event::End("answered"));
    stream(tokio_stream::iter(events))
}

fn stream(events: impl tokio_stream::Stream<Item = Event> + Send + 'static) -> Response {
    let stream = events.map(|event| Ok::<_, Infallible>(sse(event)));
    let mut response = Sse::new(stream)
        .keep_alive(KeepAlive::default())
        .into_response();
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    // tell a reverse proxy not to hold the events back
    response
        .headers_mut()
        .insert("x-accel-buffering", HeaderValue::from_static("no"));
    response
}

fn sse(event: Event) -> SseEvent {
    match event {
        Event::Step { tool, args } => SseEvent::default()
            .event("step")
            .data(json!({"tool": tool, "args": args}).to_string()),
        Event::Answer(text) => SseEvent::default()
            .event("answer")
            .data(json!({"text": text}).to_string()),
        Event::End(outcome) => SseEvent::default()
            .event("end")
            .data(json!({"outcome": outcome}).to_string()),
    }
}
