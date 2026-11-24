use std::io::IsTerminal;
use std::net::SocketAddr;
use std::path::Path;
use std::process::ExitCode;

use opencommune_server::ask::Ask;
use opencommune_server::config::Config;
use opencommune_server::site::Site;
use opencommune_server::usage::Usage;
use opencommune_server::{AppState, app, db};
use tokio::net::TcpListener;
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> ExitCode {
    tracing_subscriber::fmt()
        .with_ansi(std::io::stdout().is_terminal())
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| "info,rmcp=warn".into()),
        )
        .init();
    match run().await {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            tracing::error!(%error, "server stopped");
            ExitCode::FAILURE
        }
    }
}

async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let config = Config::from_env()?;
    let fulltext = fulltext_index(&config.fulltext_path).await;
    let db = db::open_read_only(&config.database_path, fulltext)
        .await
        .map_err(|e| format!("{}: {e}", config.database_path.display()))?;
    db::check_tables(&db).await.map_err(|e| {
        format!(
            "{}: not a serving database: {e}",
            config.database_path.display()
        )
    })?;
    let usage = match &config.usage_path {
        Some(path) => match Usage::open(path, config.usage_visitors).await {
            Ok(usage) => Some(usage.with_token(config.usage_token.as_deref())),
            Err(error) => {
                tracing::warn!(%error, path = %path.display(), "usage statistics disabled");
                None
            }
        },
        None => None,
    };
    if !config.static_dir.join("index.html").is_file() {
        tracing::warn!(path = %config.static_dir.display(), "no index.html: the front end is not built");
    }
    let site = Site::new(
        &config.public_url,
        config.search_indexing,
        config.search_index_communes,
        &config.static_dir,
    )
    .with_former_hosts(&config.former_hosts);
    let mut state = AppState {
        db,
        fulltext: fulltext.is_some(),
        usage,
        site,
        ask: None,
    };
    if let Some(ask) = config.ask.clone() {
        let model = ask.settings.model.clone();
        match Ask::start(state.clone(), ask).await {
            Ok(ask) => {
                tracing::info!(%model, "the chatbot is on");
                state.ask = Some(ask);
            }
            Err(error) => tracing::warn!(%error, "the chatbot is off: it did not start"),
        }
    }
    let router = app(state, &config.static_dir, &config.mcp_allowed_hosts);
    let listener = TcpListener::bind(config.bind).await?;
    tracing::info!(
        bind = %config.bind,
        database = %config.database_path.display(),
        "listening"
    );
    axum::serve(
        listener,
        router.into_make_service_with_connect_info::<SocketAddr>(),
    )
    .with_graceful_shutdown(shutdown())
    .await?;
    Ok(())
}

/// The full-text index to attach: the file when it exists and holds an index. Without one the
/// server runs, and full-text search answers that it is not available.
async fn fulltext_index(path: &Path) -> Option<&Path> {
    if !path.is_file() {
        tracing::info!(path = %path.display(), "no full-text index: full-text search is off");
        return None;
    }
    match db::check_fulltext(path).await {
        Ok(()) => {
            tracing::info!(path = %path.display(), "full-text index attached");
            Some(path)
        }
        Err(error) => {
            tracing::warn!(
                %error,
                path = %path.display(),
                "not a full-text index: full-text search is off"
            );
            None
        }
    }
}

async fn shutdown() {
    let terminate = async {
        match tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()) {
            Ok(mut signal) => {
                signal.recv().await;
            }
            Err(_) => std::future::pending().await,
        }
    };
    tokio::select! {
        _ = tokio::signal::ctrl_c() => {},
        () = terminate => {},
    }
    tracing::info!("shutting down");
}
