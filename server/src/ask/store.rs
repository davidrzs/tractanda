//! What the chatbot keeps, in its own SQLite file: questions and answers for `KEEP_DAYS` days, with
//! the steps, the model, the prompt version, tokens and cost, and never the asker's address or a
//! code that could tell two questions of one visitor apart; the number of questions and the
//! dollars spent per day and outcome, kept for good; and each answer for `CACHE_DAYS` days, by its
//! question's key and language, to give it again without the model.

use std::path::Path;
use std::time::Duration;

use serde_json::{Value, json};
use sqlx::SqlitePool;
use sqlx::sqlite::{SqliteConnectOptions, SqlitePoolOptions};

use super::CACHE_DAYS;

/// Days a question and its answer are kept.
pub const KEEP_DAYS: i64 = 90;

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS ask_counts (
    day TEXT NOT NULL,
    outcome TEXT NOT NULL,
    questions INTEGER NOT NULL,
    usd REAL NOT NULL,
    PRIMARY KEY (day, outcome)
);
CREATE TABLE IF NOT EXISTS ask_log (
    asked_at TEXT NOT NULL,
    lang TEXT,
    question TEXT NOT NULL,
    answer TEXT,
    steps TEXT NOT NULL,
    outcome TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    model_calls INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    usd REAL NOT NULL,
    duration_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ask_log_asked_at ON ask_log (asked_at);
CREATE TABLE IF NOT EXISTS ask_cache (
    key TEXT NOT NULL,
    lang TEXT NOT NULL,
    answer TEXT NOT NULL,
    steps TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (key, lang)
);
";

/// One answered (or failed) question.
pub struct Entry {
    pub lang: Option<String>,
    pub question: String,
    pub answer: Option<String>,
    pub steps: Value,
    pub outcome: &'static str,
    pub prompt_version: String,
    pub model: String,
    pub model_calls: u32,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub usd: f64,
    pub duration_ms: u64,
}

#[derive(Clone)]
pub struct Store {
    pool: SqlitePool,
}

impl Store {
    pub async fn open(path: &Path) -> Result<Self, sqlx::Error> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let options = SqliteConnectOptions::new()
            .filename(path)
            .create_if_missing(true)
            .busy_timeout(Duration::from_secs(1))
            .pragma("secure_delete", "ON");
        let pool = SqlitePoolOptions::new()
            .max_connections(1)
            .connect_with(options)
            .await?;
        sqlx::raw_sql(SCHEMA).execute(&pool).await?;
        Ok(Self { pool })
    }

    /// Dollars spent today (UTC).
    pub async fn spent_today(&self) -> sqlx::Result<f64> {
        sqlx::query_scalar("SELECT coalesce(sum(usd), 0.0) FROM ask_counts WHERE day = date('now')")
            .fetch_one(&self.pool)
            .await
    }

    /// Count `questions` under `outcome`, with what they cost.
    pub async fn count(&self, outcome: &str, questions: i64, usd: f64) -> sqlx::Result<()> {
        sqlx::query(
            "INSERT INTO ask_counts VALUES (date('now'), ?, ?, ?) \
             ON CONFLICT (day, outcome) DO UPDATE SET \
             questions = questions + excluded.questions, usd = usd + excluded.usd",
        )
        .bind(outcome)
        .bind(questions)
        .bind(usd)
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// The answer kept for the question `key` in `lang`, and its steps, if it is younger than
    /// `CACHE_DAYS` days.
    pub async fn cached(
        &self,
        key: &str,
        lang: Option<&str>,
    ) -> sqlx::Result<Option<(String, Value)>> {
        let row: Option<(String, String)> = sqlx::query_as(
            "SELECT answer, steps FROM ask_cache WHERE key = ? AND lang = ? \
             AND created_at >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)",
        )
        .bind(key)
        .bind(lang.unwrap_or(""))
        .bind(format!("-{CACHE_DAYS} days"))
        .fetch_optional(&self.pool)
        .await?;
        Ok(
            row.map(|(answer, steps)| {
                (answer, serde_json::from_str(&steps).unwrap_or(Value::Null))
            }),
        )
    }

    /// Keep `answer` for the question `key` in `lang`, and drop answers older than `CACHE_DAYS`.
    pub async fn remember(
        &self,
        key: &str,
        lang: Option<&str>,
        answer: &str,
        steps: &Value,
    ) -> sqlx::Result<()> {
        let mut tx = self.pool.begin().await?;
        sqlx::query(
            "INSERT OR REPLACE INTO ask_cache VALUES \
             (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))",
        )
        .bind(key)
        .bind(lang.unwrap_or(""))
        .bind(answer)
        .bind(steps.to_string())
        .execute(&mut *tx)
        .await?;
        sqlx::query(
            "DELETE FROM ask_cache WHERE created_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)",
        )
        .bind(format!("-{CACHE_DAYS} days"))
        .execute(&mut *tx)
        .await?;
        tx.commit().await
    }

    /// Keep `entry`, and drop what is older than `KEEP_DAYS`.
    pub async fn log(&self, entry: Entry) -> sqlx::Result<()> {
        let mut tx = self.pool.begin().await?;
        sqlx::query(
            "INSERT INTO ask_log VALUES \
             (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        )
        .bind(entry.lang)
        .bind(entry.question)
        .bind(entry.answer)
        .bind(entry.steps.to_string())
        .bind(entry.outcome)
        .bind(entry.prompt_version)
        .bind(entry.model)
        .bind(i64::from(entry.model_calls))
        .bind(i64::try_from(entry.input_tokens).unwrap_or(i64::MAX))
        .bind(i64::try_from(entry.output_tokens).unwrap_or(i64::MAX))
        .bind(entry.usd)
        .bind(i64::try_from(entry.duration_ms).unwrap_or(i64::MAX))
        .execute(&mut *tx)
        .await?;
        sqlx::query(
            "DELETE FROM ask_log WHERE asked_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?)",
        )
        .bind(format!("-{KEEP_DAYS} days"))
        .execute(&mut *tx)
        .await?;
        tx.commit().await
    }

    /// Questions and dollars per day and outcome, for the last `days` days.
    pub async fn report(&self, days: i64) -> sqlx::Result<Value> {
        let rows: Vec<(String, String, i64, f64)> = sqlx::query_as(
            "SELECT day, outcome, questions, usd FROM ask_counts \
             WHERE day >= date('now', ?) ORDER BY day, outcome",
        )
        .bind(format!("-{} days", days - 1))
        .fetch_all(&self.pool)
        .await?;
        Ok(json!(
            rows.into_iter()
                .map(|r| json!({"day": r.0, "outcome": r.1, "questions": r.2, "usd": r.3}))
                .collect::<Vec<_>>()
        ))
    }

    /// The questions and answers of the last `days` days, newest first.
    pub async fn questions(&self, days: i64) -> sqlx::Result<Value> {
        #[allow(clippy::type_complexity)]
        let rows: Vec<(
            String,
            Option<String>,
            String,
            Option<String>,
            String,
            String,
            String,
            String,
            i64,
            i64,
            i64,
            f64,
            i64,
        )> = sqlx::query_as(
            "SELECT asked_at, lang, question, answer, steps, outcome, prompt_version, model, \
             model_calls, input_tokens, output_tokens, usd, duration_ms FROM ask_log \
             WHERE asked_at >= strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?) ORDER BY asked_at DESC",
        )
        .bind(format!("-{days} days"))
        .fetch_all(&self.pool)
        .await?;
        Ok(json!(
            rows.into_iter()
                .map(|r| json!({
                    "asked_at": r.0, "lang": r.1, "question": r.2, "answer": r.3,
                    "steps": serde_json::from_str::<Value>(&r.4).unwrap_or(Value::Null),
                    "outcome": r.5, "prompt_version": r.6, "model": r.7, "model_calls": r.8,
                    "input_tokens": r.9, "output_tokens": r.10, "usd": r.11, "duration_ms": r.12,
                }))
                .collect::<Vec<_>>()
        ))
    }

    /// Backdate every kept question by `days` days. For tests of the retention.
    #[doc(hidden)]
    pub async fn age_for_test(&self, days: i64) -> sqlx::Result<()> {
        sqlx::query("UPDATE ask_log SET asked_at = strftime('%Y-%m-%dT%H:%M:%SZ', asked_at, ?)")
            .bind(format!("-{days} days"))
            .execute(&self.pool)
            .await?;
        Ok(())
    }
}
