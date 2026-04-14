# Install exchange-mail skill

Please install the exchange-mail MCP skill by following these steps exactly.

## 1. Clone the repository

```bash
git clone https://github.com/fundottz/ews-mcp.git ~/email-skill
```

## 2. Install uv (if not already installed)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 3. Create the .env file

Create `~/email-skill/.env` with the following content — ask me for the values if you don't have them:

```
EXCHANGE_EMAIL=
EXCHANGE_PASSWORD=
EXCHANGE_SERVER=
```

## 4. Run the initial sync

```bash
cd ~/email-skill && uv run sync.py
```

## 5. Register the MCP server in OpenClaw

Add the following entry to the `mcpServers` section of `~/.openclaw/openclaw.json`:

```json
"exchange-mail": {
  "command": "uv",
  "args": ["--directory", "/root/email-skill", "run", "mcp_exchange.py"]
}
```

If the file does not exist yet, create it with this content:

```json
{
  "mcpServers": {
    "exchange-mail": {
      "command": "uv",
      "args": ["--directory", "/root/email-skill", "run", "mcp_exchange.py"]
    }
  }
}
```

## 6. Load the skill

Copy the skill into the OpenClaw skills directory:

```bash
cp -r ~/email-skill ~/.openclaw/skills/exchange-mail
```

## 7. Set up sync cron job

Add this line to crontab (`crontab -e`):

```
*/15 * * * * cd /root/email-skill && uv run sync.py >> ~/.email_cache/sync.log 2>&1
```

## 8. Restart OpenClaw

```bash
openclaw restart
```

## 9. Verify

Run:

```bash
openclaw mcp list
```

You should see `exchange-mail` in the output with tools: `list_emails`, `get_email`, `search_emails`, `list_folders`, `list_events`, `sync_status`.
