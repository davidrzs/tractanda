# Web server deployment

One Rust binary serves the website, the JSON API and MCP from a read-only SQLite database; Caddy
in front terminates HTTPS. Nothing else runs on the server: no Python, no Node.

Layout:

1. `/opt/opencommune/opencommune-server`: the release binary (`make server`).
2. `/opt/opencommune/site/`: the static front end (`make frontend`, the contents of `frontend/build`).
3. `/opt/opencommune/data/opencommune.db`: a copy of the serving database that `opencommune build`
   writes to `DATA_DIR/serving.db` (out of WAL mode, so the read-only server needs no `-shm` file).
4. `/var/lib/opencommune/usage.sqlite3`: usage statistics, written by the server.

Install:

```sh
cp opencommune.service /etc/systemd/system/
cp Caddyfile /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable --now opencommune
systemctl reload caddy
```

Usage report: `GET /api/usage` (`server/API.md`) needs a token. Make one and keep a copy in the
password manager:

```sh
install -d -m 700 /etc/opencommune
printf 'USAGE_TOKEN=%s\n' "$(openssl rand -hex 32)" > /etc/opencommune/secrets.env
chmod 600 /etc/opencommune/secrets.env
```

The answer above the search results (the chatbot) is on once its model key is in the same file:
add a line `ASK_API_KEY=...` (a key of its own, on prepaid credits without automatic reload) and
restart. `touch /var/lib/opencommune/ask-off` turns it off without a restart; removing the file
turns it on again. (The service cannot see into `/etc/opencommune`, so the file lives in its state
folder; a path it cannot check counts as off.) Its other settings (provider, model, daily dollars): `server/API.md`.

When the key's credit or spend limit runs out, the provider refuses; the question ends with "answers
are not available right now; the search keeps working", and answers pause for 15 minutes, then
the next question tries again. Answers kept from the last 7 days are still given. The log says so
(`journalctl -u opencommune | grep no_credit`, an error line) and the usage report counts
`no_credit`. Top up or raise the limit: answers come back within 15 minutes, without a restart.

To update, replace the binary, the site or the database and run `systemctl restart opencommune`.
Logs: `journalctl -u opencommune`. Settings: `server/API.md`.

Search engines: with `SEARCH_INDEXING=true` the pages may be indexed and `robots.txt` lists the
sitemap; with `false` every response says `noindex`. The commune and canton pages stay `noindex`
and out of the sitemap until `SEARCH_INDEX_COMMUNES=true`. After a change: `systemctl daemon-reload` and a
restart.

MCP registry: `server.json` at the repository root describes the public MCP server for the official
registry (registry.modelcontextprotocol.io), from which many clients and directories read. Publish
or update it with the registry's `mcp-publisher` tool: `mcp-publisher login github`, then
`mcp-publisher publish` from the repository root.
