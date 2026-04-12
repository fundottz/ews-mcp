#!/usr/bin/env python3
"""MCP server for Exchange email/calendar — reads local SQLite cache only."""

import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from mcp.server.fastmcp import FastMCP

DB_PATH = Path.home() / ".email_cache" / "mail.db"

mcp = FastMCP("exchange-mail")


def get_db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise RuntimeError(
            f"Cache DB not found at {DB_PATH}. Run sync.py first."
        )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def hours_ago(h: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat()


def hours_from_now(h: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=h)).isoformat()


def _fmt_email_row(r) -> str:
    unread_mark = "●" if r["unread"] else "○"
    attach = " 📎" if r["has_attachments"] else ""
    return f"{unread_mark} [{r['received'][:16]}] {r['sender']}: {r['subject']}{attach}\n  id: {r['id']}"


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def list_emails(
    folder: str = "",
    limit: int = 20,
    unread_only: bool = False,
    since_hours: float = 48,
) -> str:
    """List recent emails from the local cache.

    Args:
        folder: Mailbox folder name (default: all folders)
        limit: Max number of emails to return (default: 20)
        unread_only: Return only unread emails (default: False)
        since_hours: Only emails newer than this many hours (default: 48)
    """
    conn = get_db()
    since = hours_ago(since_hours)
    query = "SELECT id, sender, subject, received, unread, has_attachments FROM emails WHERE received >= ?"
    params: list = [since]
    if folder:
        query += " AND folder = ?"
        params.append(folder)
    if unread_only:
        query += " AND unread = 1"
    query += " ORDER BY received DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    if not rows:
        return "No emails found."

    return "\n\n".join(_fmt_email_row(r) for r in rows)


@mcp.tool()
def get_email(email_id: str) -> str:
    """Get full content of an email by its ID.

    Args:
        email_id: The email ID returned by list_emails or search_emails
    """
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM emails WHERE id = ?", (email_id,)
    ).fetchone()
    conn.close()

    if not row:
        return f"Email with id '{email_id}' not found in cache."

    return (
        f"From: {row['sender']}\n"
        f"Subject: {row['subject']}\n"
        f"Received: {row['received']}\n"
        f"Folder: {row['folder']}\n"
        f"Unread: {'Yes' if row['unread'] else 'No'}\n"
        f"Has attachments: {'Yes' if row['has_attachments'] else 'No'}\n"
        f"\n---\n{row['body']}"
    )


@mcp.tool()
def search_emails(
    query: str,
    folder: str = "",
    limit: int = 10,
    since_days: int = 14,
) -> str:
    """Search emails by keyword (matches subject, sender, or body).

    Args:
        query: Search term
        folder: Mailbox folder to search (default: all folders)
        limit: Max results (default: 10)
        since_days: Search only within last N days (default: 14)
    """
    conn = get_db()
    since = hours_ago(since_days * 24)
    like = f"%{query}%"
    sql = """SELECT id, sender, subject, received, unread
           FROM emails
           WHERE received >= ?
             AND (subject LIKE ? OR sender LIKE ? OR body LIKE ?)"""
    params: list = [since, like, like, like]
    if folder:
        sql += " AND folder = ?"
        params.append(folder)
    sql += " ORDER BY received DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()

    if not rows:
        return f"No emails matching '{query}' in the last {since_days} days."

    return f"Found {len(rows)} email(s) for '{query}':\n\n" + "\n\n".join(_fmt_email_row(r) for r in rows)


@mcp.tool()
def list_folders() -> str:
    """List all folders that have emails in the local cache."""
    conn = get_db()
    rows = conn.execute(
        "SELECT folder, COUNT(*) as cnt, SUM(unread) as unread_cnt FROM emails GROUP BY folder ORDER BY cnt DESC"
    ).fetchall()
    conn.close()

    if not rows:
        return "No folders found. Run sync.py first."

    lines = [f"{r['folder']}: {r['cnt']} emails, {r['unread_cnt'] or 0} unread" for r in rows]
    return "\n".join(lines)


@mcp.tool()
def list_events(
    since_hours: float = 0,
    next_hours: float = 24,
) -> str:
    """List calendar events from the local cache.

    Args:
        since_hours: Include events starting this many hours in the past (default: 0 = now)
        next_hours: Include events starting within this many hours from now (default: 24)
    """
    conn = get_db()
    since = hours_ago(since_hours)
    until = hours_from_now(next_hours)

    rows = conn.execute(
        """SELECT subject, start, end, location, attendees, body
           FROM events
           WHERE start >= ? AND start <= ?
           ORDER BY start""",
        [since, until],
    ).fetchall()
    conn.close()

    if not rows:
        return f"No events in the next {next_hours} hours."

    lines = []
    for r in rows:
        attendees = r['attendees'] or ''
        attendee_list = [a.strip() for a in attendees.split(',') if a.strip()]
        if len(attendee_list) > 5:
            attendees_str = ', '.join(attendee_list[:5]) + f' +{len(attendee_list) - 5} more'
        else:
            attendees_str = ', '.join(attendee_list) or '—'
        lines.append(
            f"📅 {r['start'][11:16]}–{r['end'][11:16]}  {r['subject']}\n"
            f"   Location: {r['location'] or '—'}\n"
            f"   Attendees: {attendees_str}"
        )
    return "\n\n".join(lines)


@mcp.tool()
def sync_status() -> str:
    """Show last sync timestamps and cache statistics."""
    conn = get_db()
    states = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM sync_state").fetchall()}
    email_count = conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()

    return (
        f"Emails in cache: {email_count}\n"
        f"Events in cache: {event_count}\n"
        f"Last email sync: {states.get('emails_last_sync', 'never')}\n"
        f"Last event sync: {states.get('events_last_sync', 'never')}"
    )


if __name__ == "__main__":
    mcp.run()
