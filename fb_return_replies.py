r"""يرجع ردود عمود reply من الشيت إلى تعليقات فيسبوك — API v2 (ENHANCEMENT_PLAN.md §4).

كل رد بيعدّي على الشيت قبل وبعد: claim (حجز بسور fence) ← فتح التعليق ← تأكد إنه مش منشور قبل كده ← كتابة ←
prepare_submit (آخر تصريح من الشيت قبل Enter مباشرة) ← Enter ← تأكد صارم (من حسابنا، تحت التعليق نفسه، نفس النص
بالظبط) ← mark_status. أي حاجة مش مؤكدة بتبقى uncertain ومابتتبعتش تاني من غير مطابقة. سجل محلي
(dumps/replies/journal.jsonl) بيتكتب قبل كل خطوة مالهاش رجوع، وبيتعاد أول ما السكربت يشتغل.

التشغيل:
  .venv\Scripts\python.exe fb_return_replies.py --max-replies 1
  .venv\Scripts\python.exe fb_return_replies.py --dry-run          # يفتح الأهداف ويتأكد، من غير ضغط «رد» ولا كتابة
  .venv\Scripts\python.exe fb_return_replies.py --debug            # HTML/صورة/كونسول لأي رد مانجحش
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import random
import sys
import time
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

import fb_groups_to_sheet as scraper
from tools.plan_exec.client import ApiError, Client

ROOT = Path(__file__).resolve().parent
CDP = os.environ.get("FB_CDP", "http://127.0.0.1:9333")
JOURNAL = ROOT / "dumps" / "replies" / "journal.jsonl"
# بعد Enter: لحد كام ثانية نستنى الرد يظهر تحت التعليق قبل ما نقول «unconfirmed»
CONFIRM_MS = 15000
RENEW_AFTER_S = 60
CREATE_MUTATION = "useCometUFICreateCommentMutation"
# صندوق «اكتب تعليقًا عامًا» تحت خالص في نافذة المنشور: الرد مايتكتبش فيه أبدًا (كان بينزل تعليق عام جديد)
PUBLIC_COMPOSER_RE = r"تعليقًا عامًا|تعليق عام|اكتب تعليقًا|public comment|Write a comment"


def load_config() -> tuple[str, str, str]:
    """(رابط الـ web app، مفتاح الأدمن، id الشيت) من البيئة أو .secrets/. مفيش قيم في الكود."""
    cfg = {}
    path = ROOT / ".secrets" / "config.json"
    if path.exists():
        cfg = json.loads(path.read_text(encoding="utf-8"))
    url = os.environ.get("FB_SHEET_WEBAPP") or cfg.get("webapp") or ""
    key = os.environ.get("FB_SHEET_ADMIN_KEY") or ""
    key_file = ROOT / ".secrets" / "admin.key"
    if not key and key_file.exists():
        key = key_file.read_text(encoding="utf-8").strip()
    sheet = os.environ.get("FB_SHEET_ID") or cfg.get("sheet_id") or ""
    if not (url and key and sheet):
        raise SystemExit("ناقص إعداد الشيت: FB_SHEET_WEBAPP / FB_SHEET_ADMIN_KEY / FB_SHEET_ID أو .secrets/")
    return url, key, sheet


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
  // NFC، من غير علامات الاتجاه، والمسافات واحدة
  const norm = t => (t || '').normalize('NFC').replace(/[‎‏‪-‮⁦-⁩]/g, '').replace(/\s+/g, ' ').trim();
  const textOf = a => [...a.querySelectorAll("div[dir='auto']")].filter(d => d.closest('[role="article"]') === a)
    .map(d => d.innerText.trim()).filter(Boolean).join('\n');
  const authorIdOf = a => {
    const links = [...a.querySelectorAll('a[href*="/user/"], a[href*="profile.php?id="]')].filter(x => x.closest('[role="article"]') === a);
    const author = links.find(x => !x.closest("div[dir='auto']")) || links[0];
    const m = author ? (author.href.match(/\/user\/(\d+)|[?&]id=(\d+)/) || []) : [];
    return m[1] || m[2] || '';
  };
  // ردود تحت الهدف بالظبط: هدف أساسي = comment_id هو + reply_comment_id؛ هدف رد = متداخل جوّاه في الصفحة
  const repliesUnder = (target, commentId) => {
    const tl = ownLink(target);
    const targetIsReply = !!(tl && params(tl).get('reply_comment_id'));
    return [...document.querySelectorAll('[role="article"]')].filter(a => {
      if (a === target) return false;
      const l = ownLink(a);
      if (!l) return false;
      const p = params(l);
      const rid = p.get('reply_comment_id');
      if (!rid || rid === commentId) return false;
      if (targetIsReply) { const anc = a.parentElement && a.parentElement.closest('[role="article"]'); return anc === target; }
      return p.get('comment_id') === commentId;
    });
  };
"""


def own_user_id(page) -> str:
    """id الحساب المسجّل (كوكي c_user) — للتأكد إن الرد اللي لقيناه بتاعنا. القيمة مابتتطبعش."""
    try:
        return next((c["value"] for c in page.context.cookies("https://www.facebook.com")
                     if c.get("name") == "c_user"), "")
    except Exception:
        return ""


def find_own_reply(page, comment_id: str, reply: str, me: str, exclude: list[str] | None = None) -> dict | None:
    """ردنا تحت التعليق الهدف: من حسابنا (لازم، المجهول مابيتحسبش)، والنص = الرد بالظبط أو منشن + الرد.
    بيرجّع {id, time} أو None. exclude = ردود كانت موجودة قبل Enter (للتأكيد إن ده رد جديد)."""
    if not me:
        return None
    return page.evaluate(
        r"""([commentId, reply, me, exclude]) => {""" + THREAD_JS + r"""
          const target = findTarget(commentId);
          if (!target) return null;
          const want = norm(reply);
          // المنشن المسموح قدام الرد: اسم صاحب التعليق الهدف، أو رابط منشن جوه نص الرد نفسه
          const targetLinks = [...target.querySelectorAll('a[href*="/user/"]')].filter(x => x.closest('[role="article"]') === target);
          const targetName = norm(((targetLinks.find(x => !x.closest("div[dir='auto']")) || targetLinks[0]) || {}).innerText || '');
          for (const a of repliesUnder(target, commentId)) {
            const l = ownLink(a);
            const rid = params(l).get('reply_comment_id');
            if (exclude.includes(rid) || authorIdOf(a) !== me) continue;
            const text = norm(textOf(a));
            let ok = text === want;
            if (!ok && text.endsWith(' ' + want)) {
              const prefix = text.slice(0, text.length - want.length - 1).trim();
              const mentions = [...a.querySelectorAll("div[dir='auto'] a")].map(x => norm(x.innerText));
              ok = !!prefix && (prefix === targetName || mentions.includes(prefix));
            }
            if (ok) return {id: rid, time: (l.getAttribute('aria-label') || l.innerText || '').trim()};
          }
          return null;
        }""",
        [comment_id, reply, me, exclude or []],
    )


def reply_present(page, comment_id: str, reply: str, me: str) -> bool:
    return find_own_reply(page, comment_id, reply, me) is not None


def reply_ids(page, comment_id: str) -> list[str]:
    return page.evaluate(
        r"""(commentId) => {""" + THREAD_JS + r"""
          const target = findTarget(commentId);
          return target ? repliesUnder(target, commentId).map(a => params(ownLink(a)).get('reply_comment_id')) : [];
        }""", comment_id)


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


def target_shown(page, comment_id: str) -> bool:
    return bool(page.evaluate("(commentId) => {" + THREAD_JS + "return !!findTarget(commentId); }", comment_id))


def reply_button_shown(page, comment_id: str) -> bool:
    return bool(page.evaluate(
        "(commentId) => {" + THREAD_JS + r"""
          const article = findTarget(commentId);
          return !!article && [...article.querySelectorAll('[role="button"]')]
            .some(el => el.closest('[role="article"]') === article && ['رد', 'Reply'].includes((el.innerText || '').trim()));
        }""", comment_id))


def not_a_member(page) -> bool:
    """«انضمام إلى المجموعة» ظاهر = الحساب مش عضو. مابننضمّش أبدًا."""
    return bool(page.evaluate(
        "(p) => { const m = document.querySelector(\"div[role='main']\") || document.body; return new RegExp(p).test(m.textContent || ''); }",
        scraper.JOIN_RE.pattern))


# لفّات «عرض المزيد» + تمرير بالكتير، لما التعليق الهدف مش ظاهر في أول الصفحة
TARGET_ROUNDS = 25


def bring_target_into_page(page, comment_id: str) -> bool:
    """التعليق الهدف ممكن مايبانش: فيسبوك بيفتح المنشور على «الأكثر ملاءمة» وبيخبّي الباقي (تشغيل الردود
    التالت 2026-10-22: منشور فيه 29 تعليق والظاهر 2 بس، فالرد رجع no-comment). بنعرض المخفي، ونختار
    «الأحدث»، ونفتح «عرض المزيد» ونمرّر لحد ما يظهر. كله قراءة: مفيش كتابة."""
    if target_shown(page, comment_id):
        return True
    scraper.reveal_hidden_comments(page)
    if target_shown(page, comment_id):
        return True
    try:
        scraper.close_open_menu(page)
        scraper.sort_comments_newest(page)
    except Exception:
        pass  # من غير فرز لسه ممكن نلاقيه بالتمرير
    for _ in range(TARGET_ROUNDS):
        if target_shown(page, comment_id):
            return True
        clicked = scraper.expand_comments(page, rounds=1)
        moved = scraper.scroll_comment_pane(page)
        page.wait_for_timeout(600)
        if not clicked and not moved:
            break
    return target_shown(page, comment_id)


def wait_for_reply(page, comment_id: str, reply: str, me: str, before: list[str], timeout_ms: int | None = None) -> dict | None:
    timeout_ms = CONFIRM_MS if timeout_ms is None else timeout_ms
    waited = 0
    while waited <= timeout_ms:
        found = find_own_reply(page, comment_id, reply, me, before)
        if found:
            return found
        page.wait_for_timeout(500)
        waited += 500
    return None


def open_target(page, item: dict) -> str:
    url = item.get("url") or item.get("permalink") or ""
    if not url:
        return "missing-permalink"
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    problem = scraper.account_problem(page)
    if problem:
        raise scraper.AccountBlocked(problem)
    if not_a_member(page):
        return "not-a-member"
    if not bring_target_into_page(page, str(item.get("id") or item["comment_id"])):
        return "no-comment"
    return ""


def post_one(page, item: dict, gate=None) -> str:
    """رد واحد. gate() بيتنادى بعد ما الكتابة اتأكدت ومباشرة قبل Enter (prepare_submit)؛ False = مفيش Enter.
    بيرجّع الحالة، والرد اللي اتلقى في item['_reply'] = {id, time}."""
    problem = open_target(page, item)
    if problem:
        return problem
    comment_id = str(item.get("id") or item["comment_id"])
    reply = item["reply"]
    me = own_user_id(page)
    # تشغيل قبل كده ممكن يكون نشر الرد ومالحقش يعلّمه (أو مااتأكدش منه): مانكررش الرد
    if expand_thread(page, comment_id):
        page.wait_for_timeout(1500)
    found = find_own_reply(page, comment_id, reply, me)
    if found:
        item["_reply"] = found
        return "already-posted"
    before = reply_ids(page, comment_id)
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
          document.querySelectorAll('[role="textbox"]').forEach(b => {
            b.setAttribute('data-hermes-before', '1');
            b.setAttribute('data-hermes-was', norm(b.textContent));
          });
          article.setAttribute('data-hermes-article', '1');
          button.click();
          return 'opened';
        }""",
        comment_id,
    )
    if clicked != "opened":
        return clicked
    # صندوق الرد: بعد التعليق الهدف وجوه نفس الـ thread، ومش صندوق التعليق العام. الأولى: اللي نصه اتغير
    # بالضغطة (فيسبوك حط فيه المنشن — تشغيل الردود التالت)، وبعدين الجديد الظاهر، وبعدين الظاهر.
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
      const mentioned = b => b.hasAttribute('data-hermes-was') && norm(b.textContent)
        && norm(b.textContent) !== b.getAttribute('data-hermes-was');
      const pick = [b => mentioned(b) && visible(b), b => !b.hasAttribute('data-hermes-before') && visible(b), visible,
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
    before_text = page.evaluate(
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
    text = reply if not before_text.strip() or before_text[-1:].isspace() else " " + reply
    page.keyboard.insert_text(text)
    # قبل Enter: الصندوق لازم يبقى «المنشن + مسافة + الرد» بالظبط (أو الرد لوحده لو مفيش منشن)
    expected = " ".join(f"{before_text} {reply}".split())
    if " ".join(box.inner_text().split()) != expected:
        return "typing-failed"
    if gate is not None and not gate():
        return "gate-refused"
    item["_submitted"] = True
    page.keyboard.press("Enter")
    found = wait_for_reply(page, comment_id, reply, me, before)
    if found:
        item["_reply"] = found
        return "posted"
    return "unconfirmed"


def accept_leaving(dialog) -> None:
    """«عايز تسيب الصفحة؟» بسبب مسودة رد مانزلتش: نقبل ونمشي. الرفض كان بيلغي التنقّل للرد اللي بعده
    (ERR_ABORTED)، فرد واحد فاشل كان بيوقّع كل اللي بعده. أي dialog تاني بيترفض."""
    try:
        dialog.accept() if dialog.type == "beforeunload" else dialog.dismiss()
    except Exception:
        pass


# ------------------------------------------------------------------ السجل المحلي

class Journal:
    """سطر لكل خطوة: intent → authorized → submitted → confirmed → marked. fsync بعد كل سطر."""

    def __init__(self, path: Path | None = None):
        # وقت الإنشاء مش وقت تعريف الدالة: عشان JOURNAL يتغيّر (اختبارات، فولدر تاني) من غير ما يكتب في الحقيقي
        self.path = path or JOURNAL

    def write(self, attempt: str, event: str, **fields) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {"attempt": attempt, "event": event, "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        row.update(fields)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def open_attempts(self) -> list[dict]:
        """آخر حالة لكل محاولة مااتعلّمتش، بكل الحقول اللي اتجمعت."""
        if not self.path.exists():
            return []
        attempts: dict[str, dict] = {}
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            rec = attempts.setdefault(row["attempt"], {})
            rec.update(row)
            rec.setdefault("events", []).append(row["event"])
        return [r for r in attempts.values() if r["event"] != "marked"]


def iso_from_fb(text: str) -> str:
    value = scraper.normalize_fb_time(text or "")
    return scraper.iso_time(value) or dt.datetime.now().astimezone().isoformat(timespec="seconds")


def mark(client: Client, sheet: str, rec: dict, to: str, expected: str, note: str = "", request_id: str | None = None,
         reply: dict | None = None) -> dict:
    payload = {"kind": rec["kind"], "id": rec["id"], "claim_id": rec.get("claim_id", ""), "fence": rec.get("fence", ""),
               "to": to, "expected_status": expected, "note": note, "account_id": rec.get("account_id", "")}
    if reply:
        payload["sent_reply_id"] = reply["id"]
        payload["sent_at"] = iso_from_fb(reply.get("time", ""))
    return client.call("mark_status", payload, target=sheet, request_id=request_id)


def mark_any(client: Client, sheet: str, rec: dict, to: str, note: str = "", reply: dict | None = None) -> str:
    """الحالة الحالية ممكن تكون sending أو uncertain (العقد خلص): نجرّب الاتنين، والتعارض = حد سبقنا."""
    for expected in ("sending", "uncertain"):
        try:
            mark(client, sheet, rec, to, expected, note, reply=reply)
            return to
        except ApiError as exc:
            if exc.code != "E_CONFLICT":
                raise
    return "conflict"


def replay(page, client: Client, sheet: str, journal: Journal, me: str) -> list[tuple[str, str]]:
    """المحاولات اللي ماخلصتش من تشغيل قبل كده، كل نوع بطريقته (§4.3). مفيش إرسال هنا أبدًا."""
    done = []
    for rec in journal.open_attempts():
        last = rec["event"]
        if last == "intent":
            client.call("release_claim", {"kind": rec["kind"], "id": rec["id"], "claim_id": rec["claim_id"],
                                          "fence": rec["fence"], "note": "replay: never submitted"}, target=sheet)
            outcome = "released"
        elif last == "confirmed":
            outcome = mark_any(client, sheet, rec, "sent", "replay: confirmed earlier", rec.get("reply"))
        else:  # authorized / submitted: ممكن يكون نزل
            found = None
            if open_target(page, rec) == "":
                if expand_thread(page, rec["id"]):
                    page.wait_for_timeout(1500)
                found = find_own_reply(page, rec["id"], rec["reply"], me)
            outcome = mark_any(client, sheet, rec, "sent" if found else "uncertain",
                               "replay: reconciled" if found else "replay: not found after submit", found)
        journal.write(rec["attempt"], "marked", outcome=outcome)
        done.append((rec["id"], outcome))
    return done


# ------------------------------------------------------------------ تشغيل رد واحد

def run_one(page, client: Client, sheet: str, item: dict, me: str, journal: Journal, counter: dict, recorder=None) -> str:
    claim = client.call("claim", {"kind": item["kind"], "id": item["id"], "account_id": me}, target=sheet)["result"]
    rec = dict(claim["target"], kind=item["kind"], id=item["id"], claim_id=claim["claim_id"], fence=claim["fence"],
               account_id=me)
    attempt = claim["claim_id"]
    journal.write(attempt, "intent", kind=rec["kind"], id=rec["id"], claim_id=rec["claim_id"], fence=rec["fence"],
                  url=rec.get("url", ""), reply=rec["reply"], account_id=me,
                  reply_sha256=hashlib.sha256(rec["reply"].encode("utf-8")).hexdigest())
    claimed = time.time()
    refused = {}
    counter["mark"] = None

    def gate() -> bool:
        if time.time() - claimed > RENEW_AFTER_S:
            client.call("renew", {"kind": rec["kind"], "id": rec["id"], "claim_id": rec["claim_id"], "fence": rec["fence"]}, target=sheet)
        try:
            client.call("prepare_submit", {"kind": rec["kind"], "id": rec["id"], "claim_id": rec["claim_id"],
                                           "fence": rec["fence"], "binding": claim["binding"]}, target=sheet)
        except ApiError as exc:
            refused["code"] = exc.code
            return False
        journal.write(attempt, "authorized")
        journal.write(attempt, "submitted")
        counter["mark"] = counter.get("count", 0)
        return True

    try:
        status = post_one(page, rec, gate)
    except scraper.AccountBlocked:
        if rec.get("_submitted"):
            mark_any(client, sheet, rec, "uncertain", "account blocked after submit")
        else:
            client.call("release_claim", {"kind": rec["kind"], "id": rec["id"], "claim_id": rec["claim_id"], "fence": rec["fence"]}, target=sheet)
        journal.write(attempt, "marked", outcome="account-blocked")
        raise
    except BaseException:
        # استثناء: لو Enter اتضغط ممكن يكون نزل = uncertain؛ لو لأ = مفيش حاجة اتبعتت = release
        try:
            if rec.get("_submitted"):
                mark_any(client, sheet, rec, "uncertain", "exception after submit")
            else:
                client.call("release_claim", {"kind": rec["kind"], "id": rec["id"], "claim_id": rec["claim_id"], "fence": rec["fence"]}, target=sheet)
            journal.write(attempt, "marked", outcome="exception")
        except Exception:
            pass  # السجل لسه مفتوح، والتشغيل الجاي هيكمّل
        raise
    # كام طلب «إنشاء تعليق» اتبعت من الصفحة بعد التصريح (المتوقع 1). لو اسم الطلب اتغيّر عند فيسبوك هيبقى 0
    mutations = counter.get("count", 0) - counter["mark"] if counter.get("mark") is not None else 0
    if status in ("posted", "already-posted"):
        journal.write(attempt, "confirmed", reply=rec["_reply"], mutations=mutations)
        mark(client, sheet, rec, "sent", "sending", status, reply=rec["_reply"])
    elif status == "unconfirmed":
        mark(client, sheet, rec, "uncertain", "sending", "no confirmation within 15 s")
    elif status == "gate-refused":
        client.call("release_claim", {"kind": rec["kind"], "id": rec["id"], "claim_id": rec["claim_id"],
                                      "fence": rec["fence"], "note": f"prepare refused: {refused.get('code')}"}, target=sheet)
    else:
        mark(client, sheet, rec, "failed", "sending", status)
        if recorder:
            recorder.dump(f"{status}-{rec['id']}")
    journal.write(attempt, "marked", outcome=status, mutations=mutations)
    return status


def dry_check(page, item: dict, me: str) -> str:
    """من غير ضغط «رد» ولا كتابة: الهدف ظاهر، وزرار «رد» موجود، والحساب عضو، ومش تعليقنا. حدّه: مابيثبتش إن صندوق
    الرد بيفتح."""
    if item.get("author_id") and item["author_id"] == me:
        return "own-comment"
    problem = open_target(page, item)
    if problem:
        return problem
    return "dry-ok" if reply_button_shown(page, str(item["id"])) else "no-reply-button"


def main() -> None:
    parser = argparse.ArgumentParser(description="إرجاع الردود من الشيت إلى تعليقات فيسبوك (API v2)")
    parser.add_argument("--cdp", default=CDP)
    parser.add_argument("--max-replies", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true", help="يفتح الأهداف ويتأكد من غير ضغط «رد» ولا كتابة")
    parser.add_argument("--allow-post-replies", action="store_true",
                        help="فلاج كود بس (الرد على منشور). مش تصريح: الشيت لازم يسمح كمان")
    parser.add_argument("--min-gap", type=float, default=20, help=argparse.SUPPRESS)
    parser.add_argument("--max-gap", type=float, default=60, help=argparse.SUPPRESS)
    parser.add_argument("--debug", action="store_true", help="احفظ HTML وصورة وكونسول في debug/ لأي رد مانجحش")
    parser.add_argument("--trace", action="store_true", help="مع --debug: سجّل كمان trace.zip و network.har")
    args = parser.parse_args()
    scraper.utf8_console()
    scraper.check_plan_lock()
    url, key, sheet = load_config()
    client = Client(url, key)
    journal = Journal()
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(args.cdp)
        page = browser.contexts[0].new_page()
        # قبل المسجّل: هو بيرفض أي dialog، وأول handler هو اللي بيحسم
        page.on("dialog", accept_leaving)
        counter = {"count": 0}

        def on_request(req) -> None:
            try:
                if req.method == "POST" and CREATE_MUTATION in (req.post_data or ""):
                    counter["count"] += 1
            except Exception:
                pass
        page.on("request", on_request)
        recorder = None
        if args.debug:
            from browser_debug import PageRecorder

            recorder = PageRecorder(page, label="replies", trace=args.trace, har=args.trace)
        try:
            me = own_user_id(page)
            if not me:
                print("ACCOUNT-BLOCKED: no c_user cookie (logged out)", file=sys.stderr)
                raise SystemExit(scraper.ACCOUNT_EXIT)
            for cid, outcome in replay(page, client, sheet, journal, me):
                print(f"replay {cid} {outcome}")
            client.call("sweep_restrictions", {}, target=sheet)
            pending = client.call("pending_replies", {"account_id": me}, target=sheet)["result"]
            eligible = [e for e in pending["eligible"] if e["kind"] == "comment" or args.allow_post_replies]
            print(f"eligible {len(eligible)} blocked {len(pending['blocked'])}")
            for n, item in enumerate(eligible[:max(args.max_replies, 0)]):
                if recorder:
                    recorder.mark(f"{item['kind']} {item['id']}")
                if args.dry_run:
                    print(item["id"], dry_check(page, item, me))
                    continue
                if n:
                    time.sleep(random.uniform(args.min_gap, args.max_gap))
                try:
                    status = run_one(page, client, sheet, item, me, journal, counter, recorder)
                except ApiError as exc:
                    print(item["id"], "skipped", exc.code)
                    continue
                print(item["id"], status)
                if status == "unconfirmed":
                    print(f"تحذير: {item['id']} الرد اتبعت بس ماظهرش — uncertain، مش هيتبعت تاني من غير مطابقة",
                          file=sys.stderr)
            if recorder and args.trace:
                recorder.dump("complete")
        except scraper.AccountBlocked as exc:
            print(f"ACCOUNT-BLOCKED: {exc.reason}", file=sys.stderr)
            raise SystemExit(scraper.ACCOUNT_EXIT) from None
        finally:
            if recorder:
                recorder.detach()
            page.close()


if __name__ == "__main__":
    main()
