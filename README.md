# ews-mcp (exchange-mail)

Exchange EWS → `~/.email_cache/mail.db` + MCP tools.

**Config:** `EXCHANGE_*` from environment. Put values in `.env`, or use `scripts/run_sync.sh` to inject from Bitwarden (`OPENCLAW_EXCHANGE_PASSWORD`) before `sync.py`.

**macOS + corp VPN:** `~/.hermes/scripts/mail_sync.sh` uses `certs/combined-ca-bundle.pem` (certifi + `mts-extra.pem`). Re-export: `scripts/export_mts_cas.sh`.

Setup: [INSTALL.md](INSTALL.md).
