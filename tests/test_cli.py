"""Command-line behaviour of all scripts, run as real subprocesses.

browser_debug.py is driven over a REAL CDP connection to a throwaway headless
Chromium (temporary profile, random port) loading loopback pages, so the
connect_over_cdp path is exercised exactly as in production — minus Facebook.
"""
from __future__ import annotations

import json
import locale
import os
import subprocess
import time
import urllib.request

import pytest

from conftest import PYTHON, ROOT, free_port

pytestmark = pytest.mark.cli

UTF8 = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def run(*args, env=None, timeout=120):
    return subprocess.run([PYTHON, *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env or UTF8, timeout=timeout)


# ---------------------------------------------------------------- fb_groups_to_sheet.py

def test_scraper_help():
    out = run("fb_groups_to_sheet.py", "--help")
    assert out.returncode == 0
    for flag in ("--group", "--min-posts", "--max-posts", "--min-comments", "--max-comments", "--cdp",
                 "--dry", "--batch-size", "--debug", "--trace"):
        assert flag in out.stdout


def test_scraper_missing_required_args_exit_2():
    out = run("fb_groups_to_sheet.py")
    assert out.returncode == 2
    assert "required" in out.stderr


def test_scraper_invalid_range_exit_1_before_browser():
    out = run("fb_groups_to_sheet.py", "--group", "https://www.facebook.com/groups/1", "--min-posts", "0",
              "--max-posts", "1", "--min-comments", "0", "--max-comments", "1")
    assert out.returncode == 1
    assert "min-posts" in out.stderr


def test_scraper_non_integer_limit_exit_2():
    out = run("fb_groups_to_sheet.py", "--group", "x", "--min-posts", "a", "--max-posts", "1",
              "--min-comments", "0", "--max-comments", "1")
    assert out.returncode == 2


def test_scraper_unreachable_cdp_fails(tmp_path):
    """Characterization: no friendly message — Playwright's connect error propagates (exit 1)."""
    code = ("import sys, pathlib, fb_groups_to_sheet as m; m.DUMP_DIR = pathlib.Path(sys.argv[1]); "
            "sys.argv = ['x', '--group', 'https://www.facebook.com/groups/1', '--min-posts', '1', '--max-posts', '1', "
            f"'--min-comments', '0', '--max-comments', '1', '--dry', '--cdp', 'http://127.0.0.1:{free_port()}']; m.main()")
    out = run("-c", code, str(tmp_path))
    assert out.returncode == 1
    assert "connect_over_cdp" in out.stderr or "ECONNREFUSED" in out.stderr


# ---------------------------------------------------------------- stdout encoding

@pytest.mark.skipif(locale.getpreferredencoding(False).lower().replace("-", "") in ("utf8", "cp65001"),
                    reason="only reproducible where the locale code page is not UTF-8")
def test_arabic_print_works_when_stdout_is_piped_without_utf8():
    """Fixed D-15: scripts print Arabic; with stdout redirected (Task Scheduler, `> log.txt`) and no
    PYTHONUTF8/PYTHONIOENCODING, print() used to raise UnicodeEncodeError. main() now forces UTF-8."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    code = ("import sys, launch_browser as m; m.cdp_version = lambda port, timeout=2.0: {'Browser': 'x'}; "
            "sys.argv = ['launch_browser.py', '--port', '1']; raise SystemExit(m.main())")
    out = subprocess.run([PYTHON, "-c", code], cwd=ROOT, capture_output=True, env=env, timeout=60)
    assert out.returncode == 0, out.stderr
    assert "شغال".encode("utf-8") in out.stdout


# ---------------------------------------------------------------- browser_debug.py

def test_debug_help_and_subcommands():
    out = run("browser_debug.py", "--help")
    assert out.returncode == 0
    for word in ("selectors", "snapshot", "watch", "--trace", "--har", "--cdp"):
        assert word in out.stdout


def test_debug_requires_subcommand():
    assert run("browser_debug.py").returncode == 2


def test_debug_unreachable_cdp_exit_1_with_hint(tmp_path):
    out = run("browser_debug.py", "--cdp", f"http://127.0.0.1:{free_port()}", "snapshot",
              env={**UTF8, "FB_DEBUG_DIR": str(tmp_path)})
    assert out.returncode == 1
    assert "launch_browser.py" in out.stderr


@pytest.fixture(scope="module")
def cdp_browser(tmp_path_factory, pw):
    """A real Chromium with a CDP port, temporary profile, headless. Never the real profile."""
    port = free_port()
    profile = tmp_path_factory.mktemp("throwaway-profile")
    proc = subprocess.Popen([pw.chromium.executable_path, "--headless=new", f"--remote-debugging-port={port}",
                             "--remote-debugging-address=127.0.0.1", f"--user-data-dir={profile}",
                             "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
                             "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1):
                break
        except OSError:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("headless chromium did not open CDP")
    yield f"http://127.0.0.1:{port}"
    # Chromium's child processes can outlive the parent on Windows: kill the whole tree
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=30)
    else:
        proc.kill()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass


@pytest.fixture
def site(http_server):
    http_server.respond(body="<html><head><title>Local | Facebook</title></head><body><div role='main'>"
                             "<div role='button'>x</div><script>console.error('local error')</script></div></body></html>",
                        content_type="text/html; charset=utf-8")
    return http_server.url + "/groups/1/posts/2/"


def test_debug_snapshot_url_over_real_cdp(cdp_browser, site, tmp_path):
    out = run("browser_debug.py", "--cdp", cdp_browser, "snapshot", "--url", site, "--settle", "300",
              env={**UTF8, "FB_DEBUG_DIR": str(tmp_path)})
    assert out.returncode == 0, out.stderr
    (folder,) = list(tmp_path.iterdir())
    assert folder.name.endswith("-snapshot-snapshot")
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert meta["url"] == site and meta["title"] == "Local | Facebook"
    console = [json.loads(line) for line in (folder / "console.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(c["text"] == "local error" for c in console)
    # the CLI disconnects but leaves the user's browser running
    with urllib.request.urlopen(cdp_browser + "/json/version", timeout=5) as resp:
        assert resp.status == 200


def test_debug_snapshot_trace_and_har_over_real_cdp(cdp_browser, site, tmp_path):
    out = run("browser_debug.py", "--cdp", cdp_browser, "--trace", "--har", "snapshot", "--url", site,
              "--settle", "300", env={**UTF8, "FB_DEBUG_DIR": str(tmp_path)})
    assert out.returncode == 0, out.stderr
    (folder,) = [p for p in tmp_path.iterdir() if p.is_dir()]
    assert (folder / "trace.zip").stat().st_size > 0
    har = json.loads((folder / "network.har").read_text(encoding="utf-8"))
    assert any(e["request"]["url"] == site for e in har["log"]["entries"])


def test_debug_snapshot_open_tabs_over_real_cdp(cdp_browser, tmp_path):
    out = run("browser_debug.py", "--cdp", cdp_browser, "snapshot", "--settle", "100",
              env={**UTF8, "FB_DEBUG_DIR": str(tmp_path)})
    assert out.returncode == 0, out.stderr
    assert any(p.name.endswith("-tab1-snapshot") for p in tmp_path.iterdir())


def test_debug_snapshot_goto_failure_dumps_and_exit_1(cdp_browser, tmp_path):
    out = run("browser_debug.py", "--cdp", cdp_browser, "snapshot", "--url", f"http://127.0.0.1:{free_port()}/",
              env={**UTF8, "FB_DEBUG_DIR": str(tmp_path)})
    assert out.returncode == 1
    assert any(p.name.endswith("-snapshot-goto-failed") for p in tmp_path.iterdir())


def test_debug_selectors_reports_broken_critical_exit_1(cdp_browser, site, tmp_path):
    out = run("browser_debug.py", "--cdp", cdp_browser, "selectors", "--url", site, "--settle", "200",
              env={**UTF8, "FB_DEBUG_DIR": str(tmp_path)})
    assert out.returncode == 1
    assert "نوع الصفحة: post" in out.stdout
    assert "comment.article" in out.stderr


def test_debug_selectors_save_writes_snapshot(cdp_browser, site, tmp_path):
    out = run("browser_debug.py", "--cdp", cdp_browser, "selectors", "--url", site, "--settle", "200", "--save",
              env={**UTF8, "FB_DEBUG_DIR": str(tmp_path)})
    assert out.returncode == 1
    assert any(p.name.endswith("-selectors-selector-check") for p in tmp_path.iterdir())


def test_debug_watch_for_fixed_seconds(cdp_browser, tmp_path):
    out = run("browser_debug.py", "--cdp", cdp_browser, "watch", "--seconds", "1",
              env={**UTF8, "FB_DEBUG_DIR": str(tmp_path)})
    assert out.returncode == 0, out.stderr
    assert any(p.name.endswith("-watch1-watch") for p in tmp_path.iterdir())


def test_debug_bad_wait_until_choice_exit_2():
    assert run("browser_debug.py", "snapshot", "--wait-until", "never").returncode == 2
