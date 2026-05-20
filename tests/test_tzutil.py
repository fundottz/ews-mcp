#!/usr/bin/env python3
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tzutil import (  # noqa: E402
    TZ,
    apply_overlap,
    display_long,
    display_short,
    load_time,
    sql_cutoff_hours_ago,
    store_time,
)


class TestTzutil(unittest.TestCase):
    def test_store_and_load_utc_legacy(self):
        utc = datetime(2026, 5, 19, 6, 4, tzinfo=timezone.utc)
        stored = store_time(utc)
        self.assertIn("+03:00", stored)
        loaded = load_time(stored)
        self.assertEqual(loaded.hour, 9)

    def test_load_legacy_utc_string(self):
        dt = load_time("2026-05-19T06:04:00+00:00")
        self.assertEqual(dt.hour, 9)
        self.assertEqual(dt.tzinfo, TZ)

    def test_display_short(self):
        self.assertEqual(display_short("2026-05-19T09:04:00+03:00"), "19.05 09:04 MSK")

    def test_sql_cutoff_msk(self):
        self.assertIn("+03:00", sql_cutoff_hours_ago(1))

    def test_display_long_from_utc(self):
        line = display_long("2026-05-19T07:00:25.095166+00:00")
        self.assertIn("MSK", line)
        self.assertIn("10:00:25", line)

    def test_apply_overlap(self):
        base = datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc)
        out = apply_overlap(base, 5)
        self.assertEqual(out.utcoffset().total_seconds(), 3 * 3600)


if __name__ == "__main__":
    unittest.main()
