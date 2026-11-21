"""Run the production entry point with the real SDK against loopback only."""
import os
import runpy
from unittest.mock import patch
from urllib.parse import urlsplit

from telegram.ext import Application

from lab.server import BEARER, BOT_TOKEN

origin = os.environ["KRO_LAB_ORIGIN"]
url = urlsplit(origin)
if (url.scheme, url.hostname, url.path, url.query, url.fragment) != ("https", "127.0.0.1", "", "", ""):
    raise RuntimeError("The lab worker requires a loopback HTTPS server")
os.environ.update(
    BOT_TOKEN=BOT_TOKEN, PLAYER_LOOKUP_URL=origin + "/players/{player_id}",
    PLAYER_LOOKUP_TOKEN=BEARER, REQUEST_TIMEOUT="1", LOOKUP_COOLDOWN_SECONDS="30",
)
original_builder = Application.builder


def loopback_builder():
    return original_builder().base_url(origin + "/bot")


with patch.object(Application, "builder", loopback_builder):
    runpy.run_module("main", run_name="__main__")
