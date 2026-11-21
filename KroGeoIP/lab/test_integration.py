"""Run with python -m unittest lab.test_integration -v inside the loopback lab."""
import asyncio
import json
import os
import signal
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from threading import Thread
from unittest.mock import patch

import httpx

import main
from lab.server import BEARER, BOT, BOT_TOKEN, LabServer, make_certificate
from region_lab import evaluate


class LoopbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = cls.enterClassContext(tempfile.TemporaryDirectory())
        cls.root = Path(temp)
        cls.cert, key = make_certificate(cls.root)
        cls.tls = ssl.create_default_context(cafile=str(cls.cert))
        cls.server = LabServer(cls.cert, key)
        cls.addClassCleanup(cls.server.server_close)
        thread = Thread(target=cls.server.serve_forever, daemon=True)
        thread.start()
        cls.addClassCleanup(cls.server.shutdown)

    def test_provider_protocol_and_timeout(self):
        endpoint = self.server.origin + "/players/{player_id}"

        async def run():
            started = time.monotonic()
            env = {"PLAYER_LOOKUP_URL": endpoint, "PLAYER_LOOKUP_TOKEN": BEARER,
                   "SSL_CERT_FILE": str(self.cert), "REQUEST_TIMEOUT": "1"}
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(await main.lookup_player("10000"),
                                 {"status": "LOOKUP_SUCCESS", "nickname": "Synthetic player",
                                  "player_region": "AE"})
                self.assertIsNone((await main.lookup_player("10001"))["player_region"])
                self.assertEqual((await main.lookup_player("bad"))["status"], "INVALID_PLAYER_ID")
                async with httpx.AsyncClient(verify=self.tls, follow_redirects=True, trust_env=False) as client:
                    for player_id, expected in [
                        ("10002", "PROVIDER_UNAUTHORIZED"), ("10003", "PLAYER_NOT_FOUND"),
                        ("10004", "PROVIDER_FAILED"), ("10005", "INVALID_RESPONSE"),
                        ("10006", "PROVIDER_FAILED"), ("10007", "PROVIDER_REDIRECT_REJECTED"),
                        ("10009", "LOOKUP_SUCCESS"), ("10009", "RATE_LIMITED"),
                    ]:
                        with self.subTest(player_id=player_id, expected=expected):
                            actual = await main.lookup_player(player_id, client=client)
                            print("provider", player_id, actual["status"], flush=True)
                            self.assertEqual(actual["status"], expected)
                start_wait = time.monotonic()
                self.assertEqual((await main.lookup_player("10008"))["status"], "TIMEOUT")
                elapsed = time.monotonic() - start_wait
                print(f"actual HTTPS read deadline: {elapsed:.3f}s", flush=True)
                self.assertGreaterEqual(elapsed, 0.95)
                self.assertLess(elapsed, 2.5)
                with patch.dict(os.environ, {"PLAYER_LOOKUP_TOKEN": ""}):
                    self.assertEqual((await main.lookup_player("10000"))["status"], "PROVIDER_UNAUTHORIZED")
            print(f"provider scenario elapsed: {time.monotonic() - started:.3f}s", flush=True)

        asyncio.run(run())
        with httpx.Client(verify=self.tls, trust_env=False) as client:
            bad = client.get(self.server.origin + "/players/abc",
                             headers={"Authorization": f"Bearer {BEARER}", "Accept": "application/json"})
            self.assertEqual(bad.status_code, 400)
            quota = client.get(self.server.origin + "/players/10009",
                               headers={"Authorization": f"Bearer {BEARER}", "Accept": "application/json"})
            self.assertEqual((quota.status_code, quota.headers["Retry-After"]), (429, "30"))
        with self.server.lock:
            self.assertFalse(any(x["path"] in {"/redirect-target", "/players/bad"} for x in self.server.trace))
            print("HTTPS provider trace", json.dumps([x for x in self.server.trace
                  if not x["path"].startswith("/bot")]), flush=True)

    def test_storefront_sessions(self):
        with httpx.Client(verify=self.tls, trust_env=False) as session_a, \
             httpx.Client(verify=self.tls, trust_env=False) as session_b:
            url = self.server.origin + "/catalog"
            path = "/DEMO_UC60"

            def observe(client, route, phase, session, independent):
                response = client.get(url + route)
                response.raise_for_status()
                row = response.json()
                self.assertEqual(row.pop("sku"), "DEMO_UC60")
                return dict(row, phase=phase, session=session, independently_started=independent)

            a_before = observe(session_a, path, "before", "A", False)
            a_after = observe(session_a, "/TR" + path, "after", "A", False)
            self.assertEqual(observe(session_a, path, "after", "A", False), a_after)
            self.assertFalse(session_b.cookies)
            b_after = observe(session_b, path, "after", "B", True)
            self.assertFalse(session_b.cookies)
            for rows, classification in [
                ([a_before, a_after, b_after], "SESSION_SPECIFIC_RESULT"),
                ([a_before, a_after, observe(session_b, "/TR" + path, "after", "B", True)],
                 "CROSS_SESSION_DISPLAY_OBSERVED"),
            ]:
                data = {"sku": "DEMO_UC60", "observations": rows}
                result = evaluate(data)
                print("synthetic storefront", json.dumps(data), result["classification"], flush=True)
                self.assertEqual(result["classification"], classification)
                for key in ("user_region_mutation_verified", "turkish_purchase_eligibility_verified",
                            "operation_performed"):
                    self.assertFalse(result[key])
            self.assertEqual((a_before["shop_country"], a_before["currency"], a_before["price"]),
                             ("AE", "AED", "3.99"))
            self.assertEqual((a_after["shop_country"], a_after["currency"], a_after["price"]),
                             ("TR", "TRY", "49.99"))

    @contextmanager
    def worker(self, db_path):
        env = {"PATH": os.environ["PATH"], "KRO_LAB_ORIGIN": self.server.origin,
               "SSL_CERT_FILE": str(self.cert), "DB_PATH": str(db_path), "LOG_LEVEL": "INFO"}
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-m", "lab.worker"], env=env,
                                       stdout=log, stderr=subprocess.STDOUT)
            try:
                yield process
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                if process.poll() is None:
                    try:
                        process.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                log.seek(0)
                output = log.read()
                print("worker log:\n" + output.replace(BOT_TOKEN, "[lab bot]"), flush=True)
                self.assertNotIn(BOT_TOKEN, output)
                self.assertEqual(process.returncode, 0, output)

    def send_text(self, user_id, text):
        message = {"message_id": 10, "date": int(time.time()), "text": text,
                   "chat": {"id": user_id, "type": "private"},
                   "from": {"id": user_id, "first_name": "Synthetic", "is_bot": False}}
        if text.startswith("/"):
            message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text)}]
        self.server.queue(message=message)

    def send_callback(self, user_id, callback):
        self.server.queue(callback_query={
            "id": str(user_id), "chat_instance": str(user_id), "data": callback,
            "from": {"id": user_id, "first_name": "Synthetic", "is_bot": False},
            "message": {"message_id": 42, "date": int(time.time()), "from": BOT,
                        "chat": {"id": user_id, "type": "private"}, "text": "Menu"},
        })

    def wait_for_text(self, process, user_id, text, since=0):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and process.poll() is None:
            with self.server.lock:
                for index, (_, fields) in enumerate(self.server.actions[since:], since):
                    if fields["chat_id"] == str(user_id) and text in fields["text"]:
                        return index + 1, fields
            time.sleep(0.02)
        self.fail(f"Missing Bot API text for {user_id}: {text}")

    def test_telegram_polling_sqlite_and_restart(self):
        db_path = self.root / "volume" / "kro_geoip.db"
        a, b = 201, 202
        player, other_player = "20000", "20001"
        with self.worker(db_path) as process:
            self.send_text(a, "/start")
            _, reply = self.wait_for_text(process, a, "Kro GeoIP")
            self.assertIn(main.TURKEY_SHOP_URL, reply["reply_markup"])
            self.send_text(a, "/shop")
            self.wait_for_text(process, a, main.TURKEY_SHOP_URL)
            self.send_callback(a, "eligibility")
            self.wait_for_text(process, a, "حسابك مؤهل")
            self.send_callback(a, "lookup")
            self.wait_for_text(process, a, "أرسل Player ID")
            self.send_text(b, player)
            self.wait_for_text(process, b, "ابدأ بالأمر")
            self.send_text(a, "abc")
            self.wait_for_text(process, a, "غير صالح")
            self.send_text(a, player)
            _, result = self.wait_for_text(process, a, "LOOKUP_SUCCESS")
            self.assertIn("تغيير المنطقة: لم يُنفّذ", result["text"])
            self.assertIn("منطقة متجر Midasbuy الخاصة بك: غير متحقق منها", result["text"])
            self.send_callback(a, "lookup")
            self.send_text(a, player)
            self.wait_for_text(process, a, "طلب متكرر")
            self.send_text(a, "/cancel")
            after_cancel, _ = self.wait_for_text(process, a, "تم إلغاء")
            self.send_text(a, player)
            self.wait_for_text(process, a, "ابدأ بالأمر", since=after_cancel)
            self.send_callback(b, "lookup")
            self.send_text(b, other_player)
            self.wait_for_text(process, b, "LOOKUP_SUCCESS")
            self.send_text(a, "/history")
            _, history = self.wait_for_text(process, a, "NOT_PERFORMED/UNVERIFIED")
            self.assertIn(player, history["text"])
            self.assertNotIn(other_player, history["text"])

        with closing(sqlite3.connect(db_path)) as conn:
            rows = conn.execute("""SELECT l.user_id, l.player_id, l.status, l.shop_region,
                o.operation_status, o.verification_status, o.evidence_source
                FROM lookups l JOIN operations o ON o.lookup_id = l.id ORDER BY l.id""").fetchall()
            expected = [(user, pid, "LOOKUP_SUCCESS", None, "NOT_PERFORMED", "UNVERIFIED", None)
                        for user, pid in [(a, player), (b, other_player)]]
            self.assertEqual(rows, expected)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone(), ("wal",))
            print("SQLite linked audit rows after SIGTERM", rows, flush=True)
        with self.server.lock:
            self.assertEqual([self.server.provider_hits[pid] for pid in (player, other_player)], [1, 1])
            self.server.updates.clear()
            last_action = len(self.server.actions)
        with self.worker(db_path) as process:
            self.send_text(a, "/history")
            _, history = self.wait_for_text(process, a, player, since=last_action)
            self.assertNotIn(other_player, history["text"])
            print("history after restart", history["text"].splitlines()[1], flush=True)


if __name__ == "__main__":
    unittest.main()
