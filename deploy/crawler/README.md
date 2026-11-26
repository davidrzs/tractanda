# Crawler services

systemd user units that run `deploy/crawl.sh` from this checkout. The crawler only discovers and
downloads documents; processing is a separate stage. Every job takes the same lock,
`$DATA_DIR/download-jobs.lock`, so one crawl runs at a time; a job that waits more than four hours
gives up and the next timer tries again. Run manual download commands only while the timers are
stopped, or under the same lock with `flock`.

1. `oc-crawl-backlog`: works through the discovered i-web, linked-archive and WordPress inventories
   in finite batches per commune, five minutes after boot and ten minutes after the previous pass
   ends. The batches keep the work fair between communes; checkpoints keep the rest for the next
   pass.
2. `oc-crawl-refresh`: daily at 03:00 (Europe/Zurich), rechecks the oldest access observations
   (`opencommune access-recheck`) and looks for new publications, least recently refreshed communes
   first. It rotates through the archive; it does not check every commune every day.
3. `oc-crawl-fetch`: Sundays at 05:00 (Europe/Zurich), one pass over the curated sources in
   `data/communes.yaml`.

Before each pass, `deploy/crawl.sh` builds the TLS issuer bundle (`deploy/tls-intermediates/`).
Before each stage it runs `opencommune storage-check` and ends the pass, keeping all checkpoints,
when the raw disk is below its reserve (`RAW_MIN_FREE_BYTES`). A failed stage is logged and the next
one runs.

The linked-archive and WordPress stages run with two workers (`OPENCOMMUNE_DOWNLOAD_WORKERS`, 1 to
8); the i-web stage runs with one. Workers share the per-host and per-network rate limits and the
robots.txt rules. A drained inventory means every discovered URL was handled, not that a commune's
whole website is archived.

The units expect the checkout at `~/tractanda`; change `WorkingDirectory` otherwise. The script
runs `.venv/bin/opencommune`, so run `uv sync` (or `make setup`) in the checkout first.

```sh
mkdir -p ~/.config/systemd/user
cp deploy/crawler/oc-crawl-*.service deploy/crawler/oc-crawl-*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now oc-crawl-backlog.timer oc-crawl-refresh.timer oc-crawl-fetch.timer
loginctl enable-linger "$USER"   # keep the timers running without an open session
```

Logs: `journalctl --user -u 'oc-crawl-*'`. Stop everything with
`systemctl --user disable --now oc-crawl-backlog.timer oc-crawl-refresh.timer oc-crawl-fetch.timer`.
