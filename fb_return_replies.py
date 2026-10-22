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
    "AKfycbxk49tkf3Q5B5Nv58DcGqrz1_EGrPAbo65PRap9J3EBDnXfpX1W1bCFK868hNGIM4mc3Q/exec"
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


# صندوق «اكتب تعليقًا عامًا» تحت خالص في نافذة المنشور: الرد مايتكتبش فيه أبدًا (كان بينزل تعليق عام جديد)
PUBLIC_COMPOSER_RE = r"تعليقًا عامًا|تعليق عام|اكتب تعليقًا|public comment|Write a comment"
# بعد Enter: لحد كام ثانية نستنى الرد يظهر تحت التعليق قبل ما نقول «unconfirmed»
CONFIRM_MS = 15000

# مشترك بين كل نداءات الصفحة: التعليق الهدف بمطابقة الـ id بالظبط، والـ thread بتاعه، و article كل رابط
THREAD_JS = r"""
  const params = a => { try { return new URL(a.href).searchParams; } catch (e) { return new URLSearchParams(); } };
  // رابط الوقت بتاع article نفسه، مش بتاع رد متداخل جواه
  const ownLink = art => [...art.querySelectorAll('a[href*="comment_id="]')]
    .find(a => a.closest('[role="article"]') === art) || null;
  const findTarget = (commentId) => {
    // مطابقة الباراميتر بالظبط: href.includes('comment_id=123') كانت بتلقط 1234...
    const links = [...document.querySelectorAll('a[href*="comment_id="]')];
    const link = links.find(a => params(a).get('reply_comment_id') === commentId)
      || links.find(a => params(a).get('comment_id') === commentId && !params(a).get('reply_comment_id'));
    return link ? (link.closest('[role="article"]') || link.parentElement) : null;
  };
  // الـ thread = comment_id في رابط التعليق (الأب لو الهدف رد)؛ الردود كلها بتشيل نفس comment_id
  const threadOf = art => { const l = art && ownLink(art); return l ? (params(l).get('comment_id') || '') : ''; };
  const follows = (a, b) => !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
  const norm = t => (t || '').replace(/\s+/g, ' ').trim();
"""


def own_user_id(page) -> str:
    """id الحساب المسجّل (كوكي c_user) — للتأكد إن الرد اللي لقيناه بتاعنا. القيمة مابتتطبعش."""
    try:
        return next((c["value"] for c in page.context.cookies("https://www.facebook.com")
                     if c.get("name") == "c_user"), "")
    except Exception:
        return ""


def reply_present(page, comment_id: str, reply: str, me: str) -> bool:
    """رد بنفس النص في thread التعليق الهدف (ومن حسابنا لو نعرفه)."""
    return bool(page.evaluate(
        r"""([commentId, reply, me]) => {""" + THREAD_JS + r"""
          const target = findTarget(commentId);
          const thread = threadOf(target);
          if (!thread) return false;
          const want = norm(reply);
          return [...document.querySelectorAll('[role="article"]')].some(a => {
            const l = ownLink(a);
            if (!l || a === target) return false;
            const p = params(l);
            if (p.get('comment_id') !== thread || !p.get('reply_comment_id') || p.get('reply_comment_id') === commentId) return false;
            if (!norm(a.innerText).includes(want)) return false;
            if (!me) return true;
            const author = [...a.querySelectorAll('a[href*="/user/"], a[href*="profile.php?id="]')]
              .find(x => x.closest('[role="article"]') === a);
            const m = author ? (author.href.match(/\/user\/(\d+)|[?&]id=(\d+)/) || []) : [];
            return !author || (m[1] || m[2]) === me;
          });
        }""",
        [comment_id, reply, me],
    ))


def expand_thread(page, comment_id: str) -> int:
    """يفتح «عرض الردود» بتاعة التعليق الهدف بس (قراءة)، عشان نشوف لو ردنا موجود من تشغيل قبل كده."""
    return page.evaluate(
        r"""(commentId) => {""" + THREAD_JS + r"""
          const target = findTarget(commentId);
          const thread = threadOf(target);
          if (!target || !thread) return 0;
          // أول تعليق بعد الهدف من thread تاني = آخر الـ thread بتاعنا
          const stop = [...document.querySelectorAll('[role="article"]')]
            .find(a => follows(target, a) && !target.contains(a) && threadOf(a) && threadOf(a) !== thread);
          const buttons = [...document.querySelectorAll('[role="button"]')].filter(b =>
            follows(target, b) && (!stop || follows(b, stop))
            && /(عرض|View).*(رد|repl)/i.test(norm(b.textContent)) && !/^(رد|Reply)$/.test(norm(b.textContent)));
          buttons.forEach(b => b.click());
          return buttons.length;
        }""",
        comment_id,
    )


def wait_for_reply(page, comment_id: str, reply: str, me: str, timeout_ms: int = CONFIRM_MS) -> bool:
    waited = 0
    while waited <= timeout_ms:
        if reply_present(page, comment_id, reply, me):
            return True
        page.wait_for_timeout(500)
        waited += 500
    return False


def post_one(page, item: dict) -> str:
    url = item.get("permalink") or ""
    if not url:
        return "missing-permalink"
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    comment_id = str(item["comment_id"])
    reply = item["reply"]
    me = own_user_id(page)
    # تشغيل قبل كده ممكن يكون نشر الرد ومالحقش يعلّمه (أو مااتأكدش منه): مانكررش الرد
    if expand_thread(page, comment_id):
        page.wait_for_timeout(1500)
    if reply_present(page, comment_id, reply, me):
        return "already-posted"
    clicked = page.evaluate(
        r"""(commentId) => {""" + THREAD_JS + r"""
          const article = findTarget(commentId);
          if (!article) return 'no-comment';
          const button = [...article.querySelectorAll('[role="button"]')]
            .filter(el => el.closest('[role="article"]') === article)
            .find(el => (el.innerText || '').trim() === 'رد' || (el.innerText || '').trim() === 'Reply');
          if (!button) return 'no-reply-button';
          // نعلّم الصناديق الموجودة قبل الضغط: صندوق جديد بعده هو الأولى (بس مش شرط: شوف تحت)
          document.querySelectorAll('[data-hermes-article]').forEach(e => e.removeAttribute('data-hermes-article'));
          document.querySelectorAll('[data-hermes-target]').forEach(e => e.removeAttribute('data-hermes-target'));
          document.querySelectorAll('[role="textbox"]').forEach(b => b.setAttribute('data-hermes-before', '1'));
          article.setAttribute('data-hermes-article', '1');
          button.click();
          return 'opened';
        }""",
        comment_id,
    )
    if clicked != "opened":
        return clicked
    # صندوق الرد: بعد التعليق الهدف وجوه نفس الـ thread (مفيش تعليق من thread تاني بينهم)، ومش صندوق
    # التعليق العام. فيسبوك ساعات بيبقى راسمه قبل الضغط وبيحط فيه المنشن بس (اتشاف في تشغيل حقيقي
    # 2026-10-22)، فالجديد أولى بس القديم مقبول. والظاهر أولى من المخفي.
    find_box = r"""(pub) => {""" + THREAD_JS + r"""
      const article = document.querySelector('[data-hermes-article]');
      if (!article) return false;
      const thread = threadOf(article);
      const pubRe = new RegExp(pub, 'i');
      const label = b => [b.getAttribute('aria-label'), b.getAttribute('aria-placeholder')].filter(Boolean).join(' ');
      const others = [...document.querySelectorAll('[role="article"]')]
        .filter(a => follows(article, a) && !article.contains(a) && threadOf(a) && threadOf(a) !== thread);
      const boxes = [...document.querySelectorAll('[role="textbox"]')].filter(b =>
        (article.contains(b) || follows(article, b)) && !pubRe.test(label(b))
        && !others.some(a => follows(a, b)));
      const visible = b => b.getClientRects().length > 0;
      const pick = [b => !b.hasAttribute('data-hermes-before') && visible(b), visible,
                    b => !b.hasAttribute('data-hermes-before')]
        .map(f => boxes.find(f)).find(Boolean);
      if (!pick) return false;
      pick.setAttribute('data-hermes-target', '1');
      return true;
    }"""
    page.wait_for_timeout(800)
    if not page.evaluate(find_box, PUBLIC_COMPOSER_RE):
        page.wait_for_timeout(1500)
        if not page.evaluate(find_box, PUBLIC_COMPOSER_RE):
            return "no-reply-box"
    box = page.locator("[data-hermes-target]").first
    box.click(timeout=5000)
    # المنشن اللي فيسبوك حطه في أول الصندوق بيفضل: المؤشر لآخر الصندوق وبعدين النص (مش fill اللي بيمسحه)
    before = page.evaluate(
        """() => {
          const b = document.querySelector('[data-hermes-target]');
          b.focus();
          const r = document.createRange();
          r.selectNodeContents(b);
          r.collapse(false);
          const s = window.getSelection();
          s.removeAllRanges();
          s.addRange(r);
          // textContent: innerText بيشيل المسافة اللي بعد المنشن في آخر السطر
          return b.textContent || '';
        }"""
    )
    text = reply if not before.strip() or before[-1:].isspace() else " " + reply
    page.keyboard.insert_text(text)
    # قبل Enter: الصندوق لازم يبقى «المنشن + مسافة + الرد» بالظبط (أو الرد لوحده لو مفيش منشن)،
    # وإلا مابنبعتش: نص لازق في المنشن أو ناقص أحسن مايتنشرش
    expected = " ".join(f"{before} {reply}".split())
    if " ".join(box.inner_text().split()) != expected:
        return "typing-failed"
    page.keyboard.press("Enter")
    # «posted» بس لما الرد يظهر فعلًا تحت التعليق (D-03: كان بيتقال posted من غير ما نتأكد)
    return "posted" if wait_for_reply(page, comment_id, reply, me) else "unconfirmed"


def accept_leaving(dialog) -> None:
    """«عايز تسيب الصفحة؟» بسبب مسودة رد مانزلتش: نقبل ونمشي. الرفض كان بيلغي التنقّل للرد اللي بعده
    (ERR_ABORTED)، فرد واحد فاشل كان بيوقّع كل اللي بعده. أي dialog تاني بيترفض."""
    try:
        dialog.accept() if dialog.type == "beforeunload" else dialog.dismiss()
    except Exception:
        pass


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
        # قبل المسجّل: هو بيرفض أي dialog، وأول handler هو اللي بيحسم
        page.on("dialog", accept_leaving)
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
                if status == "unconfirmed":
                    # Enter اتضغط بس الرد ماظهرش: مش بنعلّمه. التشغيل الجاي هيلاقيه لو نزل (already-posted)
                    print(f"تحذير: {item['comment_id']} الرد اتبعت بس ماظهرش تحت التعليق — سايبينه pending",
                          file=sys.stderr)
                if status in ("posted", "already-posted"):
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
