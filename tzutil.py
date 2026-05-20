"""Single timezone module for mail cache (Europe/Moscow).

Contract: all timestamps in mail.db are MSK ISO strings with +03:00 offset.

Public API:
  store_time / load_time  — write/read SQLite
  now / apply_overlap     — sync logic
  sql_cutoff_*            — SQL comparison bounds
  display_*               — MCP human output
  ews_timezone            — exchangelib only
  normalize_db_timestamps — one-time UTC→MSK migration
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import zoneinfo
from exchangelib import EWSTimeZone

TZ = zoneinfo.ZoneInfo("Europe/Moscow")
TZ_LABEL = "MSK"
TIMEZONE_LINE = f"Timezone: Europe/Moscow ({TZ_LABEL})"


# ── Write path (sync only) ────────────────────────────────────────────────────


def store_time(dt: Any) -> str:
    """Serialize any datetime/EWS value for SQLite storage."""
    aware = _to_aware(dt)
    if aware is None:
        return ""
    return aware.isoformat(timespec="seconds")


# ── Read path (sync logic) ────────────────────────────────────────────────────


def load_time(value: str) -> datetime | None:
    """Parse a value from SQLite (legacy UTC or MSK)."""
    if not value or value == "never":
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=TZ)
    return parsed.astimezone(TZ)


def now() -> datetime:
    return datetime.now(TZ)


def aware(dt: Any) -> datetime | None:
    """Normalize EWS/Python datetime to aware MSK (in-memory, not for SQLite)."""
    return _to_aware(dt)


def apply_overlap(since: datetime, overlap_minutes: float) -> datetime:
    if since.tzinfo is None:
        since = since.replace(tzinfo=TZ)
    return since.astimezone(TZ) - timedelta(minutes=overlap_minutes)


def ews_timezone() -> EWSTimeZone:
    return EWSTimeZone.from_timezone(TZ)


# ── SQL bounds (MCP + sync) ───────────────────────────────────────────────────


def sql_cutoff_hours_ago(hours: float) -> str:
    return store_time(now() - timedelta(hours=hours))


def sql_cutoff_hours_from_now(hours: float) -> str:
    return store_time(now() + timedelta(hours=hours))


# ── Display (MCP only) ────────────────────────────────────────────────────────


def display_short(value: str | datetime) -> str:
    """19.05 09:04 MSK"""
    dt = load_time(value) if isinstance(value, str) else _to_aware(value)
    if dt is None:
        return str(value)[:16]
    return f"{dt:%d.%m %H:%M} {TZ_LABEL}"


def display_long(value: str) -> str:
    """19.05.2026 09:30:32 MSK"""
    dt = load_time(value)
    if dt is None:
        return value
    return f"{dt:%d.%m.%Y %H:%M:%S} {TZ_LABEL}"


def display_event_range(start: str, end: str) -> str:
    s, e = load_time(start), load_time(end)
    if s and e:
        return f"{s:%H:%M}–{e:%H:%M} {TZ_LABEL}"
    return f"{start[11:16]}–{end[11:16]}"


# ── Migration ─────────────────────────────────────────────────────────────────


def normalize_db_timestamps(conn) -> int:
    """Convert legacy UTC ISO strings in cache to MSK (+03:00)."""
    updated = 0

    for row_id, received in conn.execute("SELECT id, received FROM emails").fetchall():
        new_val = store_time(load_time(received))
        if new_val and new_val != received:
            conn.execute("UPDATE emails SET received = ? WHERE id = ?", (new_val, row_id))
            updated += 1

    for row_id, start, end in conn.execute("SELECT id, start, end FROM events").fetchall():
        new_start = store_time(load_time(start))
        new_end = store_time(load_time(end))
        if new_start != start or new_end != end:
            conn.execute(
                "UPDATE events SET start = ?, end = ? WHERE id = ?",
                (new_start, new_end, row_id),
            )
            updated += 1

    for key, value in conn.execute("SELECT key, value FROM sync_state").fetchall():
        if "sync" not in key.lower() and "cursor" not in key:
            continue
        if key.endswith(":partial") or key.endswith(":complete"):
            continue
        parsed = load_time(value)
        if parsed is None:
            continue
        new_val = store_time(parsed)
        if new_val != value:
            conn.execute(
                "INSERT OR REPLACE INTO sync_state(key, value) VALUES (?, ?)",
                (key, new_val),
            )
            updated += 1

    if updated:
        conn.commit()
    return updated


# ── Internal ──────────────────────────────────────────────────────────────────


def _to_aware(dt: Any) -> datetime | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ)
    try:
        return datetime.fromtimestamp(dt.timestamp(), tz=TZ)
    except (AttributeError, ValueError, OSError, TypeError):
        return None
