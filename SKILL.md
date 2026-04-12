---
name: exchange-mail
description: Read emails and calendar events from Microsoft Exchange via local SQLite cache
version: 1.0.0
env:
  - EXCHANGE_EMAIL
  - EXCHANGE_PASSWORD
  - EXCHANGE_SERVER
---

# exchange-mail

Gives access to the user's Exchange mailbox and calendar. Data is read from a local SQLite cache (`~/.email_cache/mail.db`) synced by `sync.py`.

## When to use

- User asks about their emails, inbox, messages from a person, or unread mail
- User asks about upcoming meetings, calendar events, or schedule
- User asks to search for an email by keyword or sender

## Available tools

| Tool | What it does |
|------|-------------|
| `list_emails` | Recent emails; filter by folder, unread, time range |
| `get_email` | Full email body by ID |
| `search_emails` | Search subject/sender/body by keyword |
| `list_folders` | All folders with email counts |
| `list_events` | Calendar events in a time window |
| `sync_status` | Last sync time and cache stats |

## Notes

- Email IDs come from `list_emails` or `search_emails` — pass them to `get_email`
- Cache reflects last sync; if data seems stale, report `sync_status` to the user
- Calendar events default to the next 24 hours; adjust `next_hours` for longer windows
