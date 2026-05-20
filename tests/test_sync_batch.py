#!/usr/bin/env python3
"""Unit tests for batched sync helpers (no live EWS)."""

import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sync  # noqa: E402


def make_item(item_id: str, received: datetime, subject: str = "sub") -> SimpleNamespace:
    return SimpleNamespace(
        id=item_id,
        datetime_received=received,
        sender=SimpleNamespace(email_address="a@mts.ru", name="A"),
        subject=subject,
        text_body="body",
        is_read=False,
        has_attachments=False,
    )


class TestCursorHelpers(unittest.TestCase):
    def test_apply_overlap(self):
        base = datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc)
        from tzutil import apply_overlap

        out = apply_overlap(base, 5)
        self.assertEqual(out, base - timedelta(minutes=5))

    def test_cursor_json_roundtrip(self):
        c = sync.FolderCursor(
            received=datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc),
            item_id="abc",
        )
        restored = sync.FolderCursor.from_json(c.to_json())
        self.assertEqual(restored.received, c.received)
        self.assertEqual(restored.item_id, "abc")

    def test_advance_cursor_lexicographic(self):
        t = datetime(2026, 5, 19, 9, 0, tzinfo=timezone.utc)
        cur = sync.FolderCursor(received=t, item_id="a")
        item = make_item("b", t)
        advanced = sync.advance_cursor(cur, item)
        self.assertEqual(advanced.item_id, "b")

    def test_cursor_after_item(self):
        t = datetime(2026, 5, 19, 9, 0, tzinfo=timezone.utc)
        cur = sync.FolderCursor(received=t, item_id="m")
        self.assertFalse(sync.cursor_after_item(cur, make_item("a", t)))
        self.assertTrue(sync.cursor_after_item(cur, make_item("n", t)))

    def test_cursor_after_item_skips_none_received(self):
        t = datetime(2026, 5, 19, 9, 0, tzinfo=timezone.utc)
        cur = sync.FolderCursor(received=t, item_id="")
        bad = make_item("bad", t)
        bad.datetime_received = None
        self.assertFalse(sync.cursor_after_item(cur, bad))


class TestFetchEmailBatch(unittest.TestCase):
    def test_same_timestamp_fills_batch_via_pagination(self):
        t = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
        items = [make_item(f"id{i:03d}", t) for i in range(120)]
        cursor = sync.FolderCursor(received=t, item_id="id005")
        batch_size = 50

        class FakeQS:
            def __init__(self, rows):
                self._rows = rows

            def filter(self, **_kwargs):
                return self

            def order_by(self, *_args):
                return self

            def only(self, *_fields):
                return self

            def __getitem__(self, key):
                if isinstance(key, slice):
                    return self._rows[key]
                return self._rows[key]

        folder = SimpleNamespace()
        folder.all = lambda: FakeQS(items)

        batch = sync.fetch_email_batch(folder, cursor, batch_size)
        self.assertEqual(len(batch), batch_size)
        self.assertEqual(batch[0].id, "id006")
        self.assertEqual(batch[-1].id, "id055")

class TestSyncState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn = sqlite3.connect(self.tmp.name)
        sync.init_db(self.conn)

    def tearDown(self):
        self.conn.close()
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_checkpoint_saved_per_batch(self):
        folder = SimpleNamespace(name="Входящие", absolute="/inbox")
        t0 = datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc)
        items = [make_item(f"id{i}", t0 + timedelta(minutes=i)) for i in range(5)]

        batches = [items[:2], items[2:4], items[4:5], []]
        call_idx = {"n": 0}

        def fake_fetch(folder_arg, cursor, batch_size):
            batch = batches[call_idx["n"]]
            call_idx["n"] += 1
            return batch

        with patch.object(sync, "fetch_email_batch", side_effect=fake_fetch):
            count, ok = sync.sync_folder_batched(
                self.conn,
                folder,
                batch_size=2,
                overlap_minutes=0,
            )

        self.assertTrue(ok)
        self.assertEqual(count, 5)
        status = sync.get_state(self.conn, sync.status_key(folder))
        self.assertEqual(status, "complete")
        raw = sync.get_state(self.conn, sync.cursor_key(folder))
        cur = sync.FolderCursor.from_json(raw)
        self.assertEqual(cur.item_id, "id4")

    def test_resume_skips_already_synced_via_overlap(self):
        folder = SimpleNamespace(name="Согласования", absolute="/agree")
        t0 = datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc)
        sync.save_folder_cursor(
            self.conn,
            folder,
            sync.FolderCursor(received=t0, item_id="done"),
            commit=True,
        )
        sync.upsert_email(self.conn, folder.name, make_item("done", t0))
        self.conn.commit()

        def fake_fetch(folder_arg, cursor, batch_size):
            return [make_item("done", t0), make_item("new1", t0 + timedelta(hours=1))]

        with patch.object(sync, "fetch_email_batch", side_effect=fake_fetch):
            count, ok = sync.sync_folder_batched(
                self.conn,
                folder,
                batch_size=50,
                overlap_minutes=5,
            )

        self.assertTrue(ok)
        # Overlap refetches "done" (idempotent upsert) plus new1
        self.assertEqual(count, 2)
        rows = self.conn.execute("SELECT id FROM emails").fetchall()
        self.assertEqual({r[0] for r in rows}, {"done", "new1"})

class TestMainExitCode(unittest.TestCase):
    def test_partial_status_yields_partial_exit(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        conn = sqlite3.connect(db_path)
        sync.init_db(conn)
        sync.set_state(conn, "emails_sync_status:/x", "partial", commit=True)
        conn.close()
        conn2 = sqlite3.connect(db_path)
        sync.init_db(conn2)
        self.assertTrue(sync.any_folder_partial(conn2))
        conn2.close()
        Path(db_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
