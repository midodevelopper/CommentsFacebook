r"""يرجع ردود عمود reply من الشيت إلى تعليقات فيسبوك. قراءة الشيت ثم كتابة الرد فقط.

التشغيل:
  .venv\Scripts\python.exe fb_return_replies.py
  .venv\Scripts\python.exe fb_return_replies.py --debug   # يحفظ HTML/صورة/كونسول لأي رد فشل
"""
import argparse
import json
import os
import urllib.parse
import urllib.request

from playwright.sync_api import sync_playwright

WEBAPP = (
    "https://script.google.com/macros/s/"
    "AKfycbzNQ4J6Sewu04sJaWcAJTHeQuCuQzc4At-gNl7oqXKRVTFtedHxaerG7s2-JZFGxfkUmg/exec"
)
TOKEN = "CommentSecrets"
CDP = os.environ.get("FB_CDP", "http://127.0.0.1:9333")


def sheet_get(action: str) -> dict:
    query = urllib.parse.urlencode({"action": action, "token": TOKEN})
    with urllib.request.urlopen(f"{WEBAPP}?{query}", timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def sheet_post(payload: dict) -> dict:
    req = urllib.request.Request(
        WEBAPP,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post_one(page, item: dict) -> str:
    url = item.get("permalink") or ""
    if not url:
        return "missing-permalink"
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    comment_id = str(item["comment_id"])
    clicked = page.evaluate(
        """(commentId) => {
          const link = [...document.querySelectorAll('a[href*="comment_id="]')]
            .find(a => a.href.includes('reply_comment_id=' + commentId) || a.href.includes('comment_id=' + commentId));
          if (!link) return 'no-comment';
          const article = link.closest('[role="article"]') || link.parentElement;
          const button = [...article.querySelectorAll('[role="button"]')]
            .find(el => (el.innerText || '').trim() === 'رد' || (el.innerText || '').trim() === 'Reply');
          if (!button) return 'no-reply-button';
          button.click();
          return 'opened';
        }""",
        comment_id,
    )
    if clicked != "opened":
        return clicked
    page.wait_for_timeout(800)
    box = page.locator("[role='textbox']").last
    box.click(timeout=5000)
    box.fill(item["reply"])
    page.keyboard.press("Enter")
    page.wait_for_timeout(2000)
    return "posted"


def main() -> None:
    parser = argparse.ArgumentParser(description="إرجاع الردود من الشيت إلى تعليقات فيسبوك")
    parser.add_argument("--cdp", default=CDP)
    parser.add_argument("--debug", action="store_true",
                        help="احفظ HTML وصورة وكونسول في debug/ لأي رد مانجحش")
    args = parser.parse_args()

    data = sheet_get("pending_replies")
    if not data.get("ok"):
        raise SystemExit(data)
    if "replies" not in data:
        raise SystemExit("الشيت المنشور لسه مش بيرجع الردود. انشر نسخة جديدة من facebook_sheet_receiver.gs")
    replies = data.get("replies") or []
    print("pending", len(replies))
    if not replies:
        return
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(args.cdp)
        page = browser.contexts[0].new_page()
        recorder = None
        if args.debug:
            from browser_debug import PageRecorder

            recorder = PageRecorder(page, label="replies")
        try:
            for item in replies:
                if recorder:
                    recorder.mark(f"comment {item['comment_id']}")
                try:
                    status = post_one(page, item)
                except Exception as exc:
                    print(item["comment_id"], "error", exc)
                    if recorder:
                        recorder.dump(f"error-{item['comment_id']}", exc=exc)
                    continue
                print(item["comment_id"], status)
                if status == "posted":
                    print(sheet_post({"token": TOKEN, "action": "mark_replied", "comment_ids": [item["comment_id"]]}))
                elif recorder:
                    # مانجحش من غير استثناء: no-comment / no-reply-button / missing-permalink
                    recorder.dump(f"{status}-{item['comment_id']}")
        finally:
            if recorder:
                recorder.detach()
            page.close()


if __name__ == "__main__":
    main()
