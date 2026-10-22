r"""
يفتح كروميوم بتاع Playwright على البروفايل الثابت وبيفتح منفذ CDP.

البروفايل ثابت دايمًا: profile\ جوه فولدر المشروع (جنب السكربت ده، أيًا كان مكان الفولدر)
مفيش بروفايل تاني. لو اتغير، التسجيل في فيسبوك بيضيع.

التشغيل:
  .venv\Scripts\python.exe launch_browser.py
  .venv\Scripts\python.exe launch_browser.py --port 9333

بعد ما يفتح، سجّل دخول فيسبوك مرة واحدة. الجلسة بتفضل محفوظة في البروفايل.
السكربتات التانية بتتوصل عليه بـ connect_over_cdp.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROFILE = ROOT / "profile"          # البروفايل الثابت. ممنوع يتغير.
READY_PAGE = ROOT / "cdp-ready.html"
DEFAULT_PORT = 9333


def chromium_path() -> Path:
    """مسار كروميوم المنزّل بـ playwright install chromium."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        return Path(p.chromium.executable_path)


def cdp_version(port: int, timeout: float = 2.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def launch(port: int) -> int:
    running = cdp_version(port)
    if running:
        print(f"المتصفح شغال أصلًا على 127.0.0.1:{port}")
        print("  ", running.get("Browser", ""))
        return 0

    chrome = chromium_path()
    if not chrome.exists():
        print(f"كروميوم مش موجود: {chrome}", file=sys.stderr)
        print("شغّل:  .venv\\Scripts\\python.exe -m playwright install chromium", file=sys.stderr)
        return 1

    PROFILE.mkdir(parents=True, exist_ok=True)
    args = [
        str(chrome),
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={PROFILE}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        READY_PAGE.as_uri() if READY_PAGE.exists() else "about:blank",
    ]
    print(f"كروميوم: {chrome}")
    print(f"البروفايل: {PROFILE}")
    print(f"CDP: http://127.0.0.1:{port}")
    subprocess.Popen(args, cwd=str(ROOT))

    for _ in range(40):
        time.sleep(0.5)
        info = cdp_version(port)
        if info:
            print("جاهز:", info.get("Browser", ""))
            return 0
    print("المتصفح فتح لكن منفذ CDP مردّش في الوقت المحدد", file=sys.stderr)
    return 1


def main() -> int:
    # الطباعة بالعربي بتوقع السكربت لو الـ stdout متحوّل لملف (cp1252)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="فتح كروميوم على البروفايل الثابت مع CDP")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return launch(parser.parse_args().port)


if __name__ == "__main__":
    raise SystemExit(main())
