"""Opt-in checks against the REAL browser on 127.0.0.1:9333. Never part of the default run.

    .venv\\Scripts\\python.exe -m pytest tests/test_live.py --run-live
    set FB_LIVE_POST_URL=https://www.facebook.com/groups/<G>/posts/<P>/   (for the selector check)

Read-only: they connect, look, and disconnect. No scraping, no replies, no Sheet writes.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request

import pytest

from conftest import PYTHON, ROOT

pytestmark = pytest.mark.live


def test_live_cdp_endpoint_answers():
    with urllib.request.urlopen("http://127.0.0.1:9333/json/version", timeout=5) as resp:
        info = json.loads(resp.read().decode())
    assert "Browser" in info and info.get("webSocketDebuggerUrl")


def test_live_profile_holds_facebook_session():
    code = ("import json; from playwright.sync_api import sync_playwright\n"
            "with sync_playwright() as p:\n"
            "    b = p.chromium.connect_over_cdp('http://127.0.0.1:9333')\n"
            "    names = sorted({c['name'] for c in b.contexts[0].cookies('https://www.facebook.com')})\n"
            "    print(json.dumps(names))\n")
    out = subprocess.run([PYTHON, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert {"c_user", "xs"} <= set(json.loads(out.stdout))


@pytest.mark.skipif(not os.environ.get("FB_LIVE_POST_URL"), reason="set FB_LIVE_POST_URL")
def test_live_selectors_on_real_post():
    out = subprocess.run([PYTHON, "browser_debug.py", "selectors", "--url", os.environ["FB_LIVE_POST_URL"]],
                         cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180,
                         env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert out.returncode == 0, out.stdout + out.stderr
