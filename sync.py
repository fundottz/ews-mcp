#!/usr/bin/env python3
"""Incremental sync from Exchange EWS to local SQLite cache."""

import os
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv
import zoneinfo
from exchangelib import Account, Credentials, Configuration, DELEGATE, EWSDateTime, EWSTimeZone

load_dotenv(Path(__file__).parent / ".env")

DB_PATH = Path.home() / ".email_cache" / "mail.db"
FIRST_SYNC_DAYS = 2
MOSCOW_TZ = zoneinfo.ZoneInfo("Europe/Moscow")
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger(__name__)


def get_account() -> Account:
    creds = Credentials(
        username=os.environ["EXCHANGE_EMAIL"],
        password=os.environ["EXCHANGE_PASSWORD"],
    )
    config = Configuration(server=os.environ["EXCHANGE_SERVER"], credentials=creds)
    return Account(
        primary_smtp_address=os.environ["EXCHANGE_EMAIL"],
        config=config,
        autodiscover=False,
        access_type=DELEGATE,
    )


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS emails (
            id          TEXT PRIMARY KEY,
            folder      TEXT NOT NULL,
            sender      TEXT,
            subject     TEXT,
            body        TEXT,
            received    TEXT NOT NULL,
            unread      INTEGER NOT NULL DEFAULT 1,
            has_attachments INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS events (
            id          TEXT PRIMARY KEY,
            subject     TEXT,
            start       TEXT NOT NULL,
            end         TEXT NOT NULL,
            location    TEXT,
            attendees   TEXT,
            body        TEXT
        );

        CREATE TABLE IF NOT EXISTS sync_state (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_emails_received ON emails(received);
        CREATE INDEX IF NOT EXISTS idx_emails_folder   ON emails(folder);
        CREATE INDEX IF NOT EXISTS idx_events_start    ON events(start);
    """)
    conn.commit()


def get_last_sync(conn: sqlite3.Connection, key: str) -> datetime:
    row = conn.execute(
        "SELECT value FROM sync_state WHERE key = ?", (key,)
    ).fetchone()
    if row:
        return datetime.fromisoformat(row[0])
    return datetime.now(timezone.utc) - timedelta(days=FIRST_SYNC_DAYS)


def set_last_sync(conn: sqlite3.Connection, key: str, dt: datetime) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sync_state(key, value) VALUES (?, ?)",
        (key, dt.isoformat()),
    )
    conn.commit()


def to_utc(ews_dt) -> str:
    """Convert EWSDateTime to UTC ISO string."""
    if ews_dt is None:
        return ""
    try:
        return datetime.fromtimestamp(ews_dt.timestamp(), tz=timezone.utc).isoformat()
    except (AttributeError, ValueError, OSError):
        return str(ews_dt)


def sync_emails(account: Account, conn: sqlite3.Connection) -> int:
    since = get_last_sync(conn, "emails_last_sync")
    log.info(f"Syncing emails since {since.isoformat()}")

    tz = EWSTimeZone.from_timezone(MOSCOW_TZ)
    since_ews = EWSDateTime.from_datetime(since).astimezone(tz)

    count = 0
    newest = since
    folder = account.inbox
    items = (
        folder.all()
        .filter(datetime_received__gt=since_ews)
        .order_by("datetime_received")
        .only("id", "sender", "subject", "text_body", "datetime_received",
              "is_read", "has_attachments")
    )
    for item in items:
        received_str = to_utc(item.datetime_received)
        sender = ""
        if item.sender:
            sender = item.sender.email_address or item.sender.name or ""

        conn.execute(
            """INSERT OR REPLACE INTO emails
               (id, folder, sender, subject, body, received, unread, has_attachments)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.id,
                folder.name,
                sender,
                item.subject or "",
                (item.text_body or "")[:4000],
                received_str,
                0 if item.is_read else 1,
                1 if item.has_attachments else 0,
            ),
        )
        count += 1

        received_aware = datetime.fromisoformat(received_str) if received_str else None
        if received_aware and received_aware > newest:
            newest = received_aware

    conn.commit()

    if count > 0:
        set_last_sync(conn, "emails_last_sync", newest)

    log.info(f"Synced {count} new emails")
    return count


def sync_events(account: Account, conn: sqlite3.Connection) -> int:
    # Always fetch a fixed window: yesterday to +30 days (calendar changes frequently)
    log.info("Syncing calendar events")

    tz = EWSTimeZone.from_timezone(MOSCOW_TZ)
    now = datetime.now(timezone.utc)
    start = EWSDateTime.from_datetime(now - timedelta(days=1)).astimezone(tz)
    end = EWSDateTime.from_datetime(now + timedelta(days=30)).astimezone(tz)

    count = 0
    for item in account.calendar.view(start=start, end=end):
        attendees_str = ""
        if item.required_attendees:
            attendees_str = ", ".join(
                a.mailbox.email_address or a.mailbox.name or ""
                for a in item.required_attendees
                if a.mailbox
            )

        conn.execute(
            """INSERT OR REPLACE INTO events
               (id, subject, start, end, location, attendees, body)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                item.id,
                item.subject or "",
                to_utc(item.start),
                to_utc(item.end),
                item.location or "",
                attendees_str,
                (item.text_body or "")[:2000],
            ),
        )
        count += 1

    conn.commit()
    set_last_sync(conn, "events_last_sync", now)
    log.info(f"Synced {count} calendar events")
    return count


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    log.info(f"Opening DB at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    log.info("Connecting to Exchange...")
    account = get_account()

    sync_emails(account, conn)
    sync_events(account, conn)

    conn.close()
    log.info("Sync complete")


if __name__ == "__main__":
    main()
