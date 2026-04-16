# Install exchange-mail skill

## 1. Clone the repository

```bash
git clone https://github.com/fundottz/ews-mcp.git ~/email-skill
cd ~/email-skill
```

## 2. Install runtime dependencies

Install `uv` with the official installer and make sure `~/.local/bin` is available for non-interactive processes (the systemd user unit below sets PATH explicitly, so shell startup files are not relied on at runtime):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Install `bws` (Bitwarden Secrets Manager CLI) so the sync job can inject `EXCHANGE_PASSWORD` without storing it in plain text inside the repo.

## 3. Create `.env`

Create `~/email-skill/.env`:

```env
EXCHANGE_EMAIL=
EXCHANGE_PASSWORD=
EXCHANGE_SERVER=
EXCHANGE_SYNC_FOLDERS=Входящие,00 Пишут мне,01 Follow-up,04 Meeting,Согласования
```

Notes:
- `EXCHANGE_PASSWORD` may stay empty when you run sync via `scripts/run_sync.sh`
- `scripts/run_sync.sh` expects the password in Bitwarden Secrets Manager under the secret key `OPENCLAW_EXCHANGE_PASSWORD`
- `BWS_ACCESS_TOKEN` is read either from the environment or from `~/.bws_token`
- `EXCHANGE_SYNC_FOLDERS` controls which mailbox folders are cached. Use a comma-separated list, for example: `Входящие,00 Пишут мне,01 Follow-up,04 Meeting,Согласования`

## 4. Register the MCP server in OpenClaw

Add this entry to the `mcpServers` section of `~/.openclaw/openclaw.json`:

```json
"exchange-mail": {
  "command": "uv",
  "args": ["--directory", "/home/openclaw/email-skill", "run", "mcp_exchange.py"]
}
```

## 5. Load the skill

Copy the repository into the OpenClaw skills directory:

```bash
cp -r ~/email-skill ~/.openclaw/skills/exchange-mail
```

## 6. Install the sync as a systemd user timer

Do not use cron for this repo. The supported path is a systemd user service + timer.

```bash
cd ~/email-skill
./scripts/install_systemd_user.sh --disable-legacy-cron
```

What this does:
- installs `exchange-mail-sync.service` and `exchange-mail-sync.timer` into `~/.config/systemd/user/`
- enables the timer
- starts one sync immediately
- removes old `sync_exchange_mail.sh` cron lines if they exist

Tracked unit templates live in:
- `assets/systemd/exchange-mail-sync.service.in`
- `assets/systemd/exchange-mail-sync.timer.in`

This keeps the deployable config in the repository, so the same setup can be reproduced on another machine without rebuilding the unit files by hand.

## 7. Verify

Check the timer and run status:

```bash
systemctl --user status exchange-mail-sync.timer
systemctl --user status exchange-mail-sync.service
systemctl --user list-timers --all | grep exchange-mail-sync
```

Run a manual sync if needed:

```bash
cd ~/email-skill
./scripts/run_sync.sh
```

Then verify OpenClaw sees fresh cache data:

```bash
openclaw mcp list
```

You should see `exchange-mail` in the output with tools: `list_emails`, `get_email`, `search_emails`, `list_folders`, `list_events`, `sync_status`.
