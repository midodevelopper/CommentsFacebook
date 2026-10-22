"""Shared fixtures for the offline regression suite.

Safety rails, applied to every test:
  * Sockets may only reach loopback, and never port 9333 (the real browser).
    A test that accidentally calls the real Apps Script or Facebook fails loudly.
  * browser_debug.DEBUG_DIR and fb_groups_to_sheet.DUMP_DIR point at tmp dirs.
  * Browser tests use a throwaway headless Chromium (temporary profile),
    never the project's profile\\ folder.
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
PYTHON = sys.executable
REAL_CDP_PORT = 9333
LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def pytest_addoption(parser):
    parser.addoption("--run-live", action="store_true",
                     help="also run tests marked live (real browser on 9333, real Facebook)")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-live"):
        return
    skip = pytest.mark.skip(reason="live test: opt-in with --run-live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


# ---------------------------------------------------------------- network guard

_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _check(address, allow_real_cdp: bool):
    if isinstance(address, tuple) and len(address) >= 2:
        host, port = address[0], address[1]
        if host not in LOOPBACK:
            raise RuntimeError(f"BLOCKED: test tried to reach the network: {address!r}")
        if port == REAL_CDP_PORT and not allow_real_cdp:
            raise RuntimeError("BLOCKED: test tried to reach the real browser on port 9333")


@pytest.fixture(autouse=True)
def network_guard(request, monkeypatch):
    live = "live" in request.keywords

    def connect(self, address):
        _check(address, live)
        return _real_connect(self, address)

    def connect_ex(self, address):
        _check(address, live)
        return _real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


@pytest.fixture(autouse=True)
def isolated_output_dirs(tmp_path, monkeypatch):
    """No test may write into the real dumps/ or debug/."""
    import browser_debug
    import fb_groups_to_sheet

    monkeypatch.setattr(browser_debug, "DEBUG_DIR", tmp_path / "debug")
    monkeypatch.setattr(fb_groups_to_sheet, "DUMP_DIR", tmp_path / "dumps")
    return tmp_path


# ---------------------------------------------------------------- local HTTP server

class RecordingServer:
    """A loopback HTTP server that records requests and replies from a queue.

    `respond(status, body, delay=0, content_type=...)` sets the default reply.
    `queue(...)` pushes one-shot replies consumed in order.
    """

    def __init__(self):
        self.requests: list[dict] = []
        self._default = (200, b'{"ok": true}', 0.0, "application/json")
        self._queue: list[tuple] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _serve(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                parsed = urllib.parse.urlsplit(self.path)
                server.requests.append({
                    "method": self.command,
                    "path": parsed.path,
                    "query": dict(urllib.parse.parse_qsl(parsed.query)),
                    "headers": dict(self.headers),
                    "body": body,
                })
                status, payload, delay, ctype = server._queue.pop(0) if server._queue else server._default
                if delay:
                    time.sleep(delay)
                if isinstance(payload, (dict, list)):
                    payload = json.dumps(payload).encode()
                elif isinstance(payload, str):
                    payload = payload.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = _serve
            do_POST = _serve

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def respond(self, status=200, body=b'{"ok": true}', delay=0.0, content_type="application/json"):
        self._default = (status, body, delay, content_type)

    def queue(self, status=200, body=b'{"ok": true}', delay=0.0, content_type="application/json"):
        self._queue.append((status, body, delay, content_type))

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def http_server():
    server = RecordingServer()
    yield server
    server.close()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------- headless browser

@pytest.fixture(scope="session")
def pw():
    from playwright.sync_api import sync_playwright

    manager = sync_playwright().start()
    yield manager
    manager.stop()


@pytest.fixture(scope="session")
def chromium(pw):
    browser = pw.chromium.launch(headless=True, args=["--disable-background-networking"])
    yield browser
    browser.close()


@pytest.fixture
def fast_waits(monkeypatch):
    """Cap page.wait_for_timeout so the scripts' fixed sleeps don't dominate runtime.

    Production code keeps its real waits; only the test process is patched.
    """
    from playwright.sync_api import Page

    original = Page.wait_for_timeout

    def capped(self, timeout):
        return original(self, min(timeout, 30))

    monkeypatch.setattr(Page, "wait_for_timeout", capped)


@pytest.fixture
def context(chromium, fast_waits):
    ctx = chromium.new_context(viewport={"width": 1200, "height": 900}, locale="ar-EG")
    yield ctx
    ctx.close()


@pytest.fixture
def fake_fb(context):
    from fake_facebook import FakeFacebook

    fb = FakeFacebook()
    fb.install(context)
    return fb


@pytest.fixture
def page(context):
    p = context.new_page()
    yield p
    if not p.is_closed():
        p.close()


class FakePlaywright:
    """Stands in for `sync_playwright()` inside the scripts' main().

    connect_over_cdp() returns a wrapper around the test's own headless context,
    so the scripts run their real code against offline fixtures. (A second real
    sync_playwright() can't start while the test's one is active.)
    """

    def __init__(self, context):
        self.context = context
        self.cdp_urls: list[str] = []
        outer = self

        class _Browser:
            contexts = [context]

            def close(self):
                pass

        class _Chromium:
            def connect_over_cdp(self, url):
                outer.cdp_urls.append(url)
                return _Browser()

        self.chromium = _Chromium()

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def fake_playwright(context):
    return FakePlaywright(context)


# ---------------------------------------------------------------- Apps Script web app (Node)

class FakeWebApp:
    """The real facebook_sheet_receiver.gs served over loopback HTTP by Node."""

    def __init__(self):
        script = TESTS / "apps_script" / "webapp_server.mjs"
        self.proc = subprocess.Popen(
            ["node", str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8",
        )
        line = self.proc.stdout.readline().strip()
        if not line.startswith("PORT "):
            err = self.proc.stderr.read()
            raise RuntimeError(f"fake web app failed to start: {line!r} {err!r}")
        self.base = f"http://127.0.0.1:{line.split()[1]}"
        self.exec_url = f"{self.base}/exec"

    def _call(self, path, payload=None):
        import urllib.request

        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(self.base + path, data=data, method="POST" if data else "GET",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def state(self) -> dict:
        """{sheetName: [[header...], [row...], ...]}"""
        return self._call("/__state")

    def rows(self, sheet: str) -> list[dict]:
        table = self.state().get(sheet) or []
        if not table:
            return []
        head = table[0]
        return [dict(zip(head, row)) for row in table[1:]]

    def set_cell(self, sheet: str, key: str, column: str, value):
        return self._call("/__set", {"sheet": sheet, "key": key, "column": column, "value": value})

    def close(self):
        self.proc.kill()
        self.proc.wait(timeout=10)
        for stream in (self.proc.stdout, self.proc.stderr):
            stream.close()


def node_available() -> bool:
    return shutil.which("node") is not None


@pytest.fixture
def webapp():
    if not node_available():
        pytest.skip("node is not installed")
    app = FakeWebApp()
    yield app
    app.close()
