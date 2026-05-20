#!/usr/bin/env python3
"""Incremental batched sync from Exchange EWS to local SQLite cache."""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from dotenv import load_dotenv
from exchangelib import Account, Configuration, Credentials, DELEGATE, EWSDateTime

from tzutil import apply_overlap, aware, ews_timezone, load_time, normalize_db_timestamps, now, store_time

load_dotenv(Path(__file__).parent / ".env")

DB_PATH = Path.home() / ".email_cache" / "mail.db"
DEFAULT_SYNC_FOLDERS = ["Входящие"]
LOG_FORMAT = "%(asctime)s %(levelname)s %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_ERROR = 2


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return int(raw)


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return float(raw)


SYNC_BATCH_SIZE = env_int("SYNC_BATCH_SIZE", 50)
SYNC_OVERLAP_MINUTES = env_float("SYNC_OVERLAP_MINUTES", 5.0)
SYNC_FIRST_SYNC_DAYS = env_int("SYNC_FIRST_SYNC_DAYS", 2)
SYNC_BODY_MAX = env_int("SYNC_BODY_MAX", 4000)


@dataclass(frozen=True)
class FolderCursor:
    received: datetime
    item_id: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {"received": self.received.isoformat(), "id": self.item_id},
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, raw: str) -> FolderCursor:
        data = json.loads(raw)
        received = load_time(data["received"])
        if received is None:
            raise ValueError("invalid cursor received")
        return cls(
            received=received,
            item_id=data.get("id") or "",
        )


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
    conn.executescript(
        """
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
        """
    )
    conn.commit()


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str, *, commit: bool = False) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO sync_state(key, value) VALUES (?, ?)",
        (key, value),
    )
    if commit:
        conn.commit()


def default_first_sync_since() -> datetime:
    return now() - timedelta(days=SYNC_FIRST_SYNC_DAYS)


def folder_path(folder: Any) -> str:
    return str(getattr(folder, "absolute", folder))


def cursor_key(folder: Any) -> str:
    return f"emails_sync_cursor:{folder_path(folder)}"


def last_sync_key(folder: Any) -> str:
    return f"emails_last_sync:{folder_path(folder)}"


def status_key(folder: Any) -> str:
    return f"emails_sync_status:{folder_path(folder)}"


def get_folder_cursor(
    conn: sqlite3.Connection,
    folder: Any,
    *,
    fallback_key: str | None = None,
) -> FolderCursor:
    raw = get_state(conn, cursor_key(folder))
    if raw:
        try:
            return FolderCursor.from_json(raw)
        except (json.JSONDecodeError, KeyError, ValueError):
            log.warning("Invalid cursor JSON for %s, resetting", folder_path(folder))

    legacy = get_state(conn, last_sync_key(folder))
    if legacy:
        parsed = load_time(legacy)
        if parsed:
            return FolderCursor(received=parsed)

    if fallback_key:
        fb = get_state(conn, fallback_key)
        if fb:
            parsed = load_time(fb)
            if parsed:
                return FolderCursor(received=parsed)

    return FolderCursor(received=default_first_sync_since())


def save_folder_cursor(
    conn: sqlite3.Connection,
    folder: Any,
    cursor: FolderCursor,
    *,
    commit: bool = False,
) -> None:
    set_state(conn, cursor_key(folder), cursor.to_json(), commit=commit)
    set_state(conn, last_sync_key(folder), cursor.received.isoformat(), commit=commit)


def set_folder_status(
    conn: sqlite3.Connection,
    folder: Any,
    status: str,
    *,
    commit: bool = False,
) -> None:
    set_state(conn, status_key(folder), status, commit=commit)


def to_aware_datetime(ews_dt: Any) -> datetime | None:
    return aware(ews_dt)


def cursor_after_item(cursor: FolderCursor, item: Any) -> bool:
    received = to_aware_datetime(item.datetime_received)
    if received is None:
        return False
    item_id = str(getattr(item, "id", "") or "")
    if received > cursor.received:
        return True
    if received == cursor.received and item_id > cursor.item_id:
        return True
    return False


def advance_cursor(cursor: FolderCursor, item: Any) -> FolderCursor:
    received = to_aware_datetime(item.datetime_received)
    if received is None:
        return cursor
    item_id = str(getattr(item, "id", "") or "")
    if received > cursor.received or (received == cursor.received and item_id > cursor.item_id):
        return FolderCursor(received=received, item_id=item_id)
    return cursor


def upsert_email(
    conn: sqlite3.Connection,
    folder_name: str,
    item: Any,
    *,
    body_max: int = SYNC_BODY_MAX,
) -> datetime | None:
    received_str = store_time(item.datetime_received)
    sender = ""
    if item.sender:
        sender = item.sender.email_address or item.sender.name or ""

    conn.execute(
        """INSERT OR REPLACE INTO emails
           (id, folder, sender, subject, body, received, unread, has_attachments)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            item.id,
            folder_name,
            sender,
            item.subject or "",
            (item.text_body or "")[:body_max],
            received_str,
            0 if item.is_read else 1,
            1 if item.has_attachments else 0,
        ),
    )
    return load_time(received_str)


def fetch_email_batch(
    folder: Any,
    cursor: FolderCursor,
    batch_size: int,
) -> list[Any]:
    """Fetch next batch after cursor (overlap applied to cursor before call)."""
    since_ews = EWSDateTime.from_datetime(cursor.received).astimezone(ews_timezone())
    page_size = batch_size + 10
    qs = (
        folder.all()
        .filter(datetime_received__gte=since_ews)
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
    batch: list[Any] = []
    offset = 0
    while len(batch) < batch_size:
        raw = list(qs[offset : offset + page_size])
        if not raw:
            break
        for item in raw:
            if not cursor_after_item(cursor, item):
                continue
            batch.append(item)
            if len(batch) >= batch_size:
                break
        if len(raw) < page_size:
            break
        offset += page_size
    return batch


def sync_folder_batched(
    conn: sqlite3.Connection,
    folder: Any,
    *,
    batch_size: int = SYNC_BATCH_SIZE,
    overlap_minutes: float = SYNC_OVERLAP_MINUTES,
    fallback_key: str | None = None,
) -> tuple[int, bool]:
    """
    Sync one folder in batches with per-batch checkpoint.
    Returns (emails_synced, completed_ok).
    """
    path = folder_path(folder)
    stored = get_folder_cursor(conn, folder, fallback_key=fallback_key)
    effective = apply_overlap(stored.received, overlap_minutes)
    # After moving the window back for overlap, re-walk from time only (idempotent upsert).
    resume_id = stored.item_id if effective >= stored.received else ""
    work_cursor = FolderCursor(received=effective, item_id=resume_id)

    set_folder_status(conn, folder, "partial", commit=True)
    log.info(
        "Folder %s: start cursor=%s overlap=%sm -> effective=%s",
        folder.name,
        stored.received.isoformat(),
        overlap_minutes,
        effective.isoformat(),
    )

    folder_count = 0
    batch_num = 0

    try:
        while True:
            batch = fetch_email_batch(folder, work_cursor, batch_size)
            if not batch:
                break

            batch_num += 1
            for item in batch:
                upsert_email(conn, folder.name, item)
                work_cursor = advance_cursor(work_cursor, item)
                folder_count += 1

            conn.commit()
            save_folder_cursor(conn, folder, work_cursor, commit=True)

            log.info(
                "Folder %s: batch %s +%s total=%s cursor=%s id_tail=%s",
                folder.name,
                batch_num,
                len(batch),
                folder_count,
                work_cursor.received.isoformat(),
                (work_cursor.item_id or "")[-12:],
            )

            if len(batch) < batch_size:
                break

        set_folder_status(conn, folder, "complete", commit=True)
        log.info("Folder %s: complete, synced %s emails", folder.name, folder_count)
        return folder_count, True

    except Exception:
        set_folder_status(conn, folder, "partial", commit=True)
        log.exception("Folder %s: failed after %s emails (status=partial)", folder.name, folder_count)
        raise


def parse_sync_folders() -> list[str]:
    raw = os.environ.get("EXCHANGE_SYNC_FOLDERS", "")
    if not raw.strip():
        return DEFAULT_SYNC_FOLDERS.copy()

    parts = [part.strip().lstrip("•*- ").strip() for part in re.split(r"[\n,;]+", raw)]
    folders = [part for part in parts if part]
    return folders or DEFAULT_SYNC_FOLDERS.copy()


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
            by_path.setdefault(path[len(mailbox_path) :], []).append(folder)

    resolved = []
    seen_paths: set[str] = set()
    for requested_name in requested:
        matches = by_name.get(requested_name) or by_path.get(requested_name) or []
        if not matches:
            raise RuntimeError(f"Exchange folder not found: {requested_name}")
        if len(matches) > 1:
            paths = ", ".join(sorted({folder_path(f) for f in matches}))
            raise RuntimeError(
                f"Exchange folder name is ambiguous: {requested_name}. Matches: {paths}"
            )
        folder = matches[0]
        path = folder_path(folder)
        if path not in seen_paths:
            resolved.append(folder)
            seen_paths.add(path)

    return resolved


def sync_emails(account: Account, conn: sqlite3.Connection) -> tuple[int, bool]:
    selected_folders = resolve_sync_folders(account)
    total_count = 0
    all_complete = True

    log.info(
        "Batched sync (size=%s overlap=%sm) folders: %s",
        SYNC_BATCH_SIZE,
        SYNC_OVERLAP_MINUTES,
        ", ".join(f.name for f in selected_folders),
    )

    for folder in selected_folders:
        fallback = "emails_last_sync" if folder.name == "Входящие" else None
        try:
            count, ok = sync_folder_batched(
                conn,
                folder,
                batch_size=SYNC_BATCH_SIZE,
                overlap_minutes=SYNC_OVERLAP_MINUTES,
                fallback_key=fallback,
            )
            total_count += count
            if not ok:
                all_complete = False
        except Exception:
            all_complete = False
            raise

    set_state(conn, "emails_last_sync", store_time(now()), commit=True)
    log.info("Synced %s new emails (all_complete=%s)", total_count, all_complete)
    return total_count, all_complete


def sync_events(account: Account, conn: sqlite3.Connection) -> int:
    log.info("Syncing calendar events")

    tz = ews_timezone()
    now_dt = now()
    start = EWSDateTime.from_datetime(now_dt - timedelta(days=1)).astimezone(tz)
    end = EWSDateTime.from_datetime(now_dt + timedelta(days=30)).astimezone(tz)

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
                store_time(item.start),
                store_time(item.end),
                item.location or "",
                attendees_str,
                (item.text_body or "")[:2000],
            ),
        )
        count += 1

    conn.commit()
    set_state(conn, "events_last_sync", store_time(now_dt), commit=True)
    log.info("Synced %s calendar events", count)
    return count


def any_folder_partial(conn: sqlite3.Connection) -> bool:
    rows = conn.execute(
        "SELECT value FROM sync_state WHERE key LIKE 'emails_sync_status:%'"
    ).fetchall()
    return any(row[0] == "partial" for row in rows)


def main() -> int:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    log.info("Opening DB at %s", DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    migrated = normalize_db_timestamps(conn)
    if migrated:
        log.info("Normalized %s timestamp field(s) to MSK", migrated)

    exit_code = EXIT_OK
    try:
        log.info("Connecting to Exchange...")
        account = get_account()

        sync_emails(account, conn)
        sync_events(account, conn)

        if any_folder_partial(conn):
            log.error("One or more folders have emails_sync_status=partial")
            exit_code = EXIT_PARTIAL

    except Exception:
        log.exception("Sync failed")
        exit_code = EXIT_ERROR
        if any_folder_partial(conn):
            exit_code = EXIT_PARTIAL

    conn.close()
    if exit_code == EXIT_OK:
        log.info("Sync complete")
    else:
        log.error("Sync finished with exit code %s", exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
