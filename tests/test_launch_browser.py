"""launch_browser.py + start-cdp-browser.cmd + cdp-ready.html.

No test here starts a real visible browser or touches the real profile:
subprocess.Popen is replaced and PROFILE is redirected to tmp wherever launch() runs.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import launch_browser
from conftest import PYTHON, ROOT

pytestmark = pytest.mark.unit


class PopenSpy:
    def __init__(self, raises: Exception | None = None):
        self.calls: list[tuple[list[str], dict]] = []
        self.raises = raises

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        if self.raises:
            raise self.raises
        return object()


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    """launch() with profile, chromium, Popen, sleep and CDP polling all under test control."""
    profile = tmp_path / "profile"
    chrome = tmp_path / "chrome.exe"
    chrome.write_text("")
    monkeypatch.setattr(launch_browser, "PROFILE", profile)
    monkeypatch.setattr(launch_browser, "chromium_path", lambda: chrome)
    spy = PopenSpy()
    monkeypatch.setattr(launch_browser.subprocess, "Popen", spy)
    sleeps: list[float] = []
    monkeypatch.setattr(launch_browser.time, "sleep", sleeps.append)
    state = {"answers": [], "polls": []}

    def fake_cdp(port, timeout=2.0):
        state["polls"].append(port)
        return state["answers"].pop(0) if state["answers"] else None

    monkeypatch.setattr(launch_browser, "cdp_version", fake_cdp)
    return {"profile": profile, "chrome": chrome, "popen": spy, "sleeps": sleeps, "state": state}


# ---------------------------------------------------------------- invariants

def test_profile_is_fixed_project_profile_dir():
    assert launch_browser.PROFILE == launch_browser.ROOT / "profile"
    assert launch_browser.ROOT == Path(launch_browser.__file__).resolve().parent


def test_no_absolute_drive_paths_in_project_files():
    """The project moves between machines/drives; every path must follow the project folder."""
    drive_path = re.compile(r"\b[A-Za-z]:[\\/]")
    names = ["launch_browser.py", "fb_groups_to_sheet.py", "fb_return_replies.py",
             "browser_debug.py", "start-cdp-browser.cmd", "cdp-ready.html", ".mcp.json"]
    hits = [f"{name}:{i}: {line.strip()}"
            for name in names
            for i, line in enumerate((ROOT / name).read_text(encoding="utf-8").splitlines(), 1)
            if drive_path.search(line)]
    assert hits == []


def test_default_port_is_9333():
    assert launch_browser.DEFAULT_PORT == 9333


def test_ready_page_path():
    assert launch_browser.READY_PAGE == launch_browser.ROOT / "cdp-ready.html"
    assert launch_browser.READY_PAGE.exists()


# ---------------------------------------------------------------- cdp_version (real loopback HTTP)

def test_cdp_version_reads_json_version(http_server, monkeypatch):
    http_server.respond(body={"Browser": "Chrome/141.0", "webSocketDebuggerUrl": "ws://x"})
    assert launch_browser.cdp_version(http_server.port) == {"Browser": "Chrome/141.0", "webSocketDebuggerUrl": "ws://x"}
    assert http_server.requests[-1]["path"] == "/json/version"
    assert http_server.requests[-1]["method"] == "GET"


def test_cdp_version_none_when_port_closed():
    from conftest import free_port

    assert launch_browser.cdp_version(free_port(), timeout=0.5) is None


def test_cdp_version_none_when_not_json(http_server):
    http_server.respond(body="<html>not cdp</html>", content_type="text/html")
    assert launch_browser.cdp_version(http_server.port) is None


def test_cdp_version_none_on_http_error(http_server):
    http_server.respond(status=500, body="boom")
    assert launch_browser.cdp_version(http_server.port) is None


def test_cdp_version_always_targets_loopback_ipv4(monkeypatch):
    seen = {}

    def fake_urlopen(url, timeout):
        seen["url"], seen["timeout"] = url, timeout
        raise launch_browser.urllib.error.URLError("x")

    monkeypatch.setattr(launch_browser.urllib.request, "urlopen", fake_urlopen)
    assert launch_browser.cdp_version(9444, timeout=1.5) is None
    assert seen == {"url": "http://127.0.0.1:9444/json/version", "timeout": 1.5}


# ---------------------------------------------------------------- launch()

def test_existing_browser_detected_no_second_launch(sandbox, capsys):
    sandbox["state"]["answers"] = [{"Browser": "Chrome/1"}]
    assert launch_browser.launch(9333) == 0
    assert sandbox["popen"].calls == []
    assert not sandbox["profile"].exists()
    assert "Chrome/1" in capsys.readouterr().out


def test_missing_chromium_returns_1_with_install_hint(sandbox, capsys):
    sandbox["chrome"].unlink()
    assert launch_browser.launch(9333) == 1
    err = capsys.readouterr().err
    assert "playwright install chromium" in err
    assert sandbox["popen"].calls == []
    assert not sandbox["profile"].exists()


def test_launch_command_line_is_exact(sandbox):
    sandbox["state"]["answers"] = [None, None, {"Browser": "Chrome/2"}]
    assert launch_browser.launch(9333) == 0
    (args, kwargs), = sandbox["popen"].calls
    assert args == [
        str(sandbox["chrome"]),
        "--remote-debugging-port=9333",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={sandbox['profile']}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--new-window",
        launch_browser.READY_PAGE.as_uri(),
    ]
    assert kwargs == {"cwd": str(launch_browser.ROOT)}
    assert sandbox["profile"].is_dir()


def test_launch_uses_requested_port(sandbox):
    sandbox["state"]["answers"] = [None, {"Browser": "x"}]
    assert launch_browser.launch(9555) == 0
    args = sandbox["popen"].calls[0][0]
    assert "--remote-debugging-port=9555" in args
    assert set(sandbox["state"]["polls"]) == {9555}


def test_launch_existing_profile_is_reused_not_reset(sandbox):
    sandbox["profile"].mkdir()
    marker = sandbox["profile"] / "Cookies"
    marker.write_text("session")
    sandbox["state"]["answers"] = [None, {"Browser": "x"}]
    assert launch_browser.launch(9333) == 0
    assert marker.read_text() == "session"


def test_launch_falls_back_to_about_blank_without_ready_page(sandbox, monkeypatch, tmp_path):
    monkeypatch.setattr(launch_browser, "READY_PAGE", tmp_path / "missing.html")
    sandbox["state"]["answers"] = [None, {"Browser": "x"}]
    assert launch_browser.launch(9333) == 0
    assert sandbox["popen"].calls[0][0][-1] == "about:blank"


def test_launch_timeout_after_40_polls_of_half_a_second(sandbox, capsys):
    assert launch_browser.launch(9333) == 1
    assert sandbox["sleeps"] == [0.5] * 40
    assert len(sandbox["state"]["polls"]) == 41      # 1 pre-check + 40 polls
    assert "CDP" in capsys.readouterr().err


def test_launch_popen_failure_propagates(sandbox):
    """Characterization: an OSError from Popen is not caught or reported nicely."""
    sandbox["popen"].raises = OSError("cannot exec")
    with pytest.raises(OSError):
        launch_browser.launch(9333)


def test_main_parses_port(monkeypatch):
    seen = []
    monkeypatch.setattr(launch_browser, "launch", lambda port: seen.append(port) or 7)
    monkeypatch.setattr(sys, "argv", ["launch_browser.py"])
    assert launch_browser.main() == 7
    monkeypatch.setattr(sys, "argv", ["launch_browser.py", "--port", "9444"])
    assert launch_browser.main() == 7
    assert seen == [9333, 9444]


# ---------------------------------------------------------------- chromium discovery (real Playwright)

@pytest.mark.cli
def test_chromium_path_resolves_installed_playwright_chromium():
    """Runs in a subprocess: chromium_path() starts its own sync_playwright()."""
    out = subprocess.run(
        [PYTHON, "-c", "import launch_browser,json;p=launch_browser.chromium_path();"
                       "print(json.dumps([str(p), p.exists()]))"],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    path, exists = json.loads(out.stdout.strip().splitlines()[-1])
    assert exists, path
    assert "ms-playwright" in path and "chromium-" in path


# ---------------------------------------------------------------- CLI

@pytest.mark.cli
def test_cli_help():
    out = subprocess.run([PYTHON, "launch_browser.py", "--help"], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8", timeout=60,
                         env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert out.returncode == 0
    assert "--port" in out.stdout


@pytest.mark.cli
def test_cli_rejects_non_integer_port():
    out = subprocess.run([PYTHON, "launch_browser.py", "--port", "abc"], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8", timeout=60,
                         env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert out.returncode == 2
    assert "invalid int value" in out.stderr


# ---------------------------------------------------------------- start-cdp-browser.cmd

windows_only = pytest.mark.skipif(os.name != "nt", reason="cmd launcher is Windows-only")


@pytest.mark.cli
@windows_only
def test_cmd_launcher_forwards_args_to_python_launcher():
    out = subprocess.run(["cmd", "/c", str(ROOT / "start-cdp-browser.cmd"), "--help"],
                         capture_output=True, timeout=60)
    assert out.returncode == 0
    assert b"--port" in out.stdout


@pytest.mark.cli
@windows_only
def test_cmd_launcher_fails_without_venv(tmp_path):
    shutil.copy(ROOT / "start-cdp-browser.cmd", tmp_path / "start-cdp-browser.cmd")
    out = subprocess.run(["cmd", "/c", str(tmp_path / "start-cdp-browser.cmd")],
                         capture_output=True, timeout=60)
    assert out.returncode == 1
    assert b".venv" in out.stdout


def test_cmd_launcher_targets_venv_python_and_launch_browser():
    text = (ROOT / "start-cdp-browser.cmd").read_text(encoding="utf-8")
    assert r'set "PY=%ROOT%.venv\Scripts\python.exe"' in text
    assert '"%PY%" "%ROOT%launch_browser.py" %*' in text


# ---------------------------------------------------------------- cdp-ready.html

def test_ready_page_is_static_and_mentions_profile_and_port():
    """Characterization: the page hardcodes 9333 even when --port changes it."""
    html = (ROOT / "cdp-ready.html").read_text(encoding="utf-8")
    assert "profile\\" in html
    assert "127.0.0.1:9333" in html
    assert "<script" not in html.lower()
