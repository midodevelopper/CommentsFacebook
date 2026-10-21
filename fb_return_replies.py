r"""يرجع ردود عمود reply من الشيت إلى تعليقات فيسبوك. قراءة الشيت ثم كتابة الرد فقط.

التشغيل:
  .venv\Scripts\python.exe fb_return_replies.py
  .venv\Scripts\python.exe fb_return_replies.py --debug   # يحفظ HTML/صورة/كونسول لأي رد فشل
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

from playwright.sync_api import sync_playwright

WEBAPP = (
    "https://script.google.com/macros/s/"
    "AKfycbxV6e807bE6rooDlnk35nazr8QvJBruwlPo50ybCD_CMHG9WHN6kUPpFxe8m7ri8_ZqXw/exec"
)
TOKEN = os.environ.get("FB_SHEET_TOKEN", "CommentSecrets")
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
          // مطابقة الباراميتر بالظبط: href.includes('comment_id=123') كانت بتلقط 1234...
          const params = a => { try { return new URL(a.href).searchParams; } catch (e) { return new URLSearchParams(); } };
          const links = [...document.querySelectorAll('a[href*="comment_id="]')];
          const link = links.find(a => params(a).get('reply_comment_id') === commentId)
            || links.find(a => params(a).get('comment_id') === commentId && !params(a).get('reply_comment_id'));
          if (!link) return 'no-comment';
          const article = link.closest('[role="article"]') || link.parentElement;
          const button = [...article.querySelectorAll('[role="button"]')]
            .find(el => (el.innerText || '').trim() === 'رد' || (el.innerText || '').trim() === 'Reply');
          if (!button) return 'no-reply-button';
          // نعلّم الصناديق الموجودة قبل الضغط: صندوق الرد هو اللي هيظهر بعده
          document.querySelectorAll('[data-hermes-article]').forEach(e => e.removeAttribute('data-hermes-article'));
          document.querySelectorAll('[role="textbox"]').forEach(b => b.setAttribute('data-hermes-before', '1'));
          article.setAttribute('data-hermes-article', '1');
          button.click();
          return 'opened';
        }""",
        comment_id,
    )
    if clicked != "opened":
        return clicked
    # مش «آخر textbox في الصفحة»: صندوق «اكتب تعليقًا عامًا» تحت خالص، وكان الرد بيتكتب فيه
    # كتعليق عام جديد. بناخد أول صندوق جديد ظهر بعد article التعليق.
    find_box = """() => {
      const article = document.querySelector('[data-hermes-article]');
      if (!article) return false;
      const fresh = [...document.querySelectorAll('[role="textbox"]:not([data-hermes-before])')]
        .filter(b => article.contains(b) || (article.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING));
      if (!fresh.length) return false;
      fresh[0].setAttribute('data-hermes-target', '1');
      return true;
    }"""
    page.wait_for_timeout(800)
    if not page.evaluate(find_box):
        page.wait_for_timeout(1500)
        if not page.evaluate(find_box):
            return "no-reply-box"
    box = page.locator("[data-hermes-target]").first
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
    parser.add_argument("--trace", action="store_true",
                        help="مع --debug: سجّل كمان trace.zip و network.har")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

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

            recorder = PageRecorder(page, label="replies", trace=args.trace, har=args.trace)
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
                    answer = sheet_post({"token": TOKEN, "action": "mark_replied", "comment_ids": [item["comment_id"]]})
                    print(answer)
                    if answer.get("ok") is not True or not answer.get("marked"):
                        # الرد اتنشر بس الصف لسه pending: التشغيل الجاي هينشره تاني لو ماتصلّحش
                        print(f"تحذير: {item['comment_id']} اتنشر بس ماتعلّمش posted في الشيت: {answer}",
                              file=sys.stderr)
                elif recorder:
                    # مانجحش من غير استثناء: no-comment / no-reply-button / no-reply-box / missing-permalink
                    recorder.dump(f"{status}-{item['comment_id']}")
            if recorder and args.trace:
                recorder.dump("complete")
        finally:
            if recorder:
                recorder.detach()
            page.close()


if __name__ == "__main__":
    main()
