import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import main
from region_lab import evaluate


class ConfigTests(unittest.TestCase):
    def test_lookup_host_is_fixed_and_request_has_valid_port(self):
        for url in ("https://{player_id}.example.org/profile",
                    "https://example.org:bad/{player_id}",
                    "https://example.org/#{player_id}",
                    "https://@example.org/{player_id}"):
            with self.subTest(url=url), patch.dict(os.environ, {"PLAYER_LOOKUP_URL": url}):
                with self.assertRaises(ValueError):
                    main.provider_config()
        url = "https://example.org:8443/player?id={player_id}"
        with patch.dict(os.environ, {"PLAYER_LOOKUP_URL": url}):
            self.assertEqual(main.provider_config(), url)

    def test_timeout_is_finite_before_clamping(self):
        for seconds in ("NaN", "Infinity", "-Infinity", "bad"):
            with self.subTest(seconds=seconds), patch.dict(os.environ, {"REQUEST_TIMEOUT": seconds}):
                with self.assertRaises(ValueError):
                    main.request_timeout()
        for seconds, expected in (("0.1", 1), ("20", 20), ("900", 60)):
            with self.subTest(seconds=seconds), patch.dict(os.environ, {"REQUEST_TIMEOUT": seconds}):
                self.assertEqual(main.request_timeout(), expected)


class EvidenceTests(unittest.TestCase):
    BASE = {"session": "A", "independently_started": False,
            "shop_country": "TR", "currency": "TRY"}

    def evaluate(self, *observations):
        return evaluate({"sku": "DEMO", "observations":
                         [dict(self.BASE, **observation) for observation in observations]})

    def test_malformed_metadata_is_reported_as_invalid_input(self):
        for fields in ({"phase": []}, {"phase": "after", "eligibility": {}}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.evaluate(fields)

    def test_missing_before_price_does_not_imply_a_change(self):
        report = self.evaluate({"phase": "before"}, {"phase": "after", "price": "9.00"})
        self.assertEqual(report["classification"], "NO_DEMONSTRATED_CHANGE")
        self.assertNotIn("DISPLAY_OR_PRICE_CHANGED_WITHIN_SESSION", report["evidence"])

    def test_same_currency_price_discrepancy_across_sessions(self):
        report = self.evaluate(
            {"phase": "before", "price": "9.00"},
            {"phase": "after", "price": "9.00"},
            {"phase": "after", "session": "B", "independently_started": True, "price": "10.00"},
        )
        self.assertEqual(report["classification"], "SESSION_SPECIFIC_RESULT")
        self.assertIn("SAME_CURRENCY_PRICE_DIFFERS_ACROSS_SESSIONS", report["evidence"])
        self.assertFalse(report["user_region_mutation_verified"])

    def test_two_independent_sessions_can_disagree_on_price(self):
        report = self.evaluate(
            {"phase": "before", "price": "9.00"},
            {"phase": "after", "session": "B", "independently_started": True, "price": "9.00"},
            {"phase": "after", "session": "C", "independently_started": True, "price": "10.00"},
        )
        self.assertEqual(report["classification"], "SESSION_SPECIFIC_RESULT")
        self.assertIn("SAME_CURRENCY_PRICE_DIFFERS_ACROSS_SESSIONS", report["evidence"])


class SQLiteTests(unittest.TestCase):
    def test_audit_insert_error_rolls_back_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(main, "DB_PATH", str(Path(directory) / "rollback.db")):
                with closing(main.connect()) as conn, conn:
                    conn.execute("""CREATE TRIGGER reject_operation BEFORE INSERT ON operations
                                 BEGIN SELECT RAISE(ABORT, 'synthetic audit failure'); END""")
                with self.assertRaises(sqlite3.IntegrityError):
                    main.record(1, "12345", {"status": "LOOKUP_SUCCESS"})
                with closing(main.connect()) as conn:
                    self.assertEqual(conn.execute("SELECT count(*) FROM lookups").fetchone(), (0,))
                    self.assertEqual(conn.execute("SELECT count(*) FROM operations").fetchone(), (0,))

    def test_parallel_first_writes_create_complete_audit_pairs(self):
        with tempfile.TemporaryDirectory() as directory, ThreadPoolExecutor(max_workers=12) as pool:
            for trial in range(8):
                with self.subTest(trial=trial), patch.object(main, "DB_PATH", str(Path(directory) / f"{trial}.db")):
                    first_write = threading.Barrier(12)

                    def write(index):
                        first_write.wait(timeout=5)
                        main.record(index, "12345", {"status": "LOOKUP_SUCCESS"})

                    list(pool.map(write, range(12)))
                    with closing(main.connect()) as conn:
                        pairs = conn.execute("""SELECT l.user_id
                            FROM lookups l JOIN operations o ON l.id = o.lookup_id AND l.user_id = o.user_id
                            ORDER BY l.user_id""").fetchall()
                    self.assertEqual(pairs, [(index,) for index in range(12)])
