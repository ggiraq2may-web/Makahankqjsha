"""Loopback HTTPS fixtures for the Kro provider, storefront and Telegram Bot API."""
import json
import ssl
import subprocess
import time
from collections import Counter
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from urllib.parse import parse_qs, urlsplit

BEARER = "synthetic-provider-token"
BOT_TOKEN = "123456:synthetic-telegram-token"
BOT = {"id": 123456, "is_bot": True, "first_name": "Lab", "username": "krolab_bot"}
CATALOG = {"AE": ("AED", "3.99"), "TR": ("TRY", "49.99")}


def make_certificate(directory):
    cert = directory / "loopback.pem"
    key = directory / "loopback.key"
    subprocess.run(
        ["openssl", "req", "-new", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(cert), "-days", "1",
         "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"],
        check=True, capture_output=True,
    )
    return cert, key


class LabServer(ThreadingHTTPServer):
    def __init__(self, cert, key):
        super().__init__(("127.0.0.1", 0), LabHandler)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        self.socket = tls.wrap_socket(self.socket, server_side=True)
        self.origin = f"https://127.0.0.1:{self.server_port}"
        self.trace = []
        self.actions = []
        self.updates = []
        self.provider_hits = Counter()
        self.lock = Lock()

    def queue(self, **update):
        with self.lock:
            self.updates.append({"update_id": len(self.updates) + 1, **update})


class LabHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def reply(self, status, body=None, headers=None):
        path = urlsplit(self.path).path.replace(BOT_TOKEN, "[lab bot]")
        with self.server.lock:
            self.server.trace.append({"method": self.command, "path": path, "status": status})
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        try:
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            pass  # Timeout and worker shutdown can close the TLS socket before the reply.

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/catalog/DEMO_UC60" or path in ("/catalog/AE/DEMO_UC60", "/catalog/TR/DEMO_UC60"):
            return self.catalog(path)
        if path.startswith("/players/"):
            return self.player(path.rsplit("/", 1)[-1])
        if path == "/redirect-target":
            return self.reply(200, {"name": "Redirect must not be followed"})
        return self.reply(404)

    def player(self, player_id):
        if self.headers.get("Authorization") != f"Bearer {BEARER}":
            return self.reply(401)
        if self.headers.get("Accept") != "application/json":
            return self.reply(406)
        if len(player_id) not in range(5, 21) or not player_id.isascii() or not player_id.isdecimal():
            return self.reply(400, {"error": "bad player ID"})
        with self.server.lock:
            self.server.provider_hits[player_id] += 1
            hits = self.server.provider_hits[player_id]
        if player_id == "10009" and hits > 1:
            return self.reply(429, {"error": "quota"}, {"Retry-After": "30"})
        if player_id == "10008":
            time.sleep(1.5)
        if player_id == "10007":
            return self.reply(302, headers={"Location": "/redirect-target"})
        if player_id == "10006":
            return self.reply(200, b"{broken JSON")
        if player_id == "10005":
            return self.reply(200, [])
        if player_id in {"10002", "10003", "10004"}:
            return self.reply({"10002": 403, "10003": 404, "10004": 503}[player_id])
        if player_id == "10001":
            return self.reply(200, {"info": {"charac_name": "Zone fixture", "zone_id": "7"}})
        return self.reply(200, {"info": {"nickname": "Synthetic player", "user_region": "AE"}})

    def catalog(self, path):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        selected = cookie["lab_store"].value if "lab_store" in cookie else "AE"
        headers = {}
        if path.count("/") == 3:
            selected = path.split("/")[2]
            headers["Set-Cookie"] = f"lab_store={selected}; Path=/; Secure; HttpOnly; SameSite=Lax"
        if selected not in CATALOG:
            return self.reply(400)
        currency, price = CATALOG[selected]
        return self.reply(200, {"sku": "DEMO_UC60", "shop_country": selected,
                                "currency": currency, "price": price}, headers)

    def do_POST(self):
        path = urlsplit(self.path).path
        if not path.startswith(f"/bot{BOT_TOKEN}/"):
            return self.reply(405)
        method = path.rsplit("/", 1)[-1]
        data = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode())
        fields = {key: values[0] for key, values in data.items()}
        if method == "getMe":
            result = BOT
        elif method == "getUpdates":
            time.sleep(0.06)
            offset = int(fields.get("offset", "0"))
            with self.server.lock:
                result = [update for update in self.server.updates if update["update_id"] >= offset]
        elif method in {"deleteWebhook", "answerCallbackQuery"}:
            result = True
        elif method in {"sendMessage", "editMessageText"}:
            with self.server.lock:
                self.server.actions.append((method, fields))
                message_id = 1000 + len(self.server.actions)
            result = {"message_id": message_id, "date": int(time.time()), "from": BOT,
                      "chat": {"id": int(fields["chat_id"]), "type": "private"},
                      "text": fields["text"]}
        else:
            return self.reply(404)
        return self.reply(200, {"ok": True, "result": result})
