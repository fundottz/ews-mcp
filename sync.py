#!/usr/bin/env python3
"""Incremental sync from Exchange EWS to local SQLite cache."""

import os
import re
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
DEFAULT_SYNC_FOLDERS = ["Входящие"]
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


def get_last_sync(conn: sqlite3.Connection, key: str, fallback_key: str | None = None) -> datetime:
    row = conn.execute(
        "SELECT value FROM sync_state WHERE key = ?", (key,)
    ).fetchone()
    if row:
        return datetime.fromisoformat(row[0])
    if fallback_key:
        fallback_row = conn.execute(
            "SELECT value FROM sync_state WHERE key = ?", (fallback_key,)
        ).fetchone()
        if fallback_row:
            return datetime.fromisoformat(fallback_row[0])
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


def parse_sync_folders() -> list[str]:
    raw = os.environ.get("EXCHANGE_SYNC_FOLDERS", "")
    if not raw.strip():
        return DEFAULT_SYNC_FOLDERS.copy()

    parts = [
        part.strip().lstrip("•*- ").strip()
        for part in re.split(r"[\n,;]+", raw)
    ]
    folders = [part for part in parts if part]
    return folders or DEFAULT_SYNC_FOLDERS.copy()


def folder_path(folder) -> str:
    return str(getattr(folder, "absolute", folder))


def folder_state_key(folder) -> str:
    return f"emails_last_sync:{folder_path(folder)}"


def resolve_sync_folders(account: Account) -> list:
    requested = parse_sync_folders()
    by_name: dict[str, list] = {}
    by_path: dict[str, list] = {}

    for folder in account.root.walk():
        name = getattr(folder, "name", "")
        path = folder_path(folder)
        if name:
            by_name.setdefault(name, []).append(folder)
        by_path.setdefault(path, []).append(folder)
        mailbox_path = "/root/Корневой уровень хранилища/"
        if path.startswith(mailbox_path):
            by_path.setdefault(path[len(mailbox_path):], []).append(folder)

    resolved = []
    seen_paths = set()
    for requested_name in requested:
        matches = by_name.get(requested_name) or by_path.get(requested_name) or []
        if not matches:
            raise RuntimeError(f"Exchange folder not found: {requested_name}")
        if len(matches) > 1:
            paths = ", ".join(sorted({folder_path(folder) for folder in matches}))
            raise RuntimeError(
                f"Exchange folder name is ambiguous: {requested_name}. Matches: {paths}"
            )
        folder = matches[0]
        path = folder_path(folder)
        if path not in seen_paths:
            resolved.append(folder)
            seen_paths.add(path)

    return resolved


def sync_emails(account: Account, conn: sqlite3.Connection) -> int:
    tz = EWSTimeZone.from_timezone(MOSCOW_TZ)
    selected_folders = resolve_sync_folders(account)
    run_started = datetime.now(timezone.utc)
    total_count = 0

    log.info(
        "Syncing emails for folders: %s",
        ", ".join(folder.name for folder in selected_folders),
    )

    for folder in selected_folders:
        fallback_key = "emails_last_sync" if folder.name == "Входящие" else None
        since = get_last_sync(conn, folder_state_key(folder), fallback_key=fallback_key)
        since_ews = EWSDateTime.from_datetime(since).astimezone(tz)
        folder_count = 0
        newest = since

        log.info("Syncing folder %s since %s", folder.name, since.isoformat())

        items = (
            folder.all()
            .filter(datetime_received__gt=since_ews)
            .order_by("datetime_received")
            .only(
                "id",
                "sender",
                "subject",
                "text_body",
                "datetime_received",
                "is_read",
                "has_attachments",
            )
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
            folder_count += 1
            total_count += 1

            received_aware = datetime.fromisoformat(received_str) if received_str else None
            if received_aware and received_aware > newest:
                newest = received_aware

        conn.commit()
        set_last_sync(conn, folder_state_key(folder), newest if folder_count > 0 else run_started)
        log.info("Synced %s new emails from %s", folder_count, folder.name)

    set_last_sync(conn, "emails_last_sync", run_started)
    log.info(f"Synced {total_count} new emails")
    return total_count


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
