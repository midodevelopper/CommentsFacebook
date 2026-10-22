r"""
أرشفة جروب أو أكتر من جروبات فيسبوك إلى Google Sheet.
قراءة فقط: مفيش رد، ولا لايك، ولا كتابة.

التشغيل (المتصفح لازم يكون مفتوح ومتصل بـ CDP ومسجّل دخول):
  python fb_groups_to_sheet.py ^
    --group https://www.facebook.com/groups/4678007325765117 ^
    --min-posts 1 --max-posts 5 ^
    --min-comments 1 --max-comments 30

أكتر من جروب:
  python fb_groups_to_sheet.py --group URL1 --group URL2 --min-posts 1 --max-posts 10 --min-comments 0 --max-comments 50

تجربة من غير إرسال:
  python fb_groups_to_sheet.py --group URL --min-posts 1 --max-posts 1 --min-comments 0 --max-comments 5 --dry
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

WEBAPP = (
    "https://script.google.com/macros/s/"
    "AKfycbxk49tkf3Q5B5Nv58DcGqrz1_EGrPAbo65PRap9J3EBDnXfpX1W1bCFK868hNGIM4mc3Q/exec"
)
TOKEN = os.environ.get("FB_SHEET_TOKEN", "CommentSecrets")
CDP = os.environ.get("FB_CDP", "http://127.0.0.1:9333")
DUMP_DIR = Path(__file__).resolve().parent / "dumps"

GROUP_RE = re.compile(r"facebook\.com/groups/([^/?#]+)", re.I)
POST_RE = re.compile(r"/groups/[^/]+/(?:posts|permalink)/(\d+)")
# نفس POST_RE بس بيرجّع الجروب كمان، عشان نرفض منشورات الجروبات التانية
POST_GROUP_RE = re.compile(r"/groups/([^/?#]+)/(?:posts|permalink)/(\d+)")
# متربوطين بحدود الباراميتر: من غيرها comment_id= كانت بتلقط جوه reply_comment_id=
CID_RE = re.compile(r"[?&]comment_id=(\d+)")
RCID_RE = re.compile(r"[?&]reply_comment_id=(\d+)")
# فيسبوك مابيحطش زرار فرز خالص لو المنشور مفيهوش تعليقات، وبيكتب ده بدله
NO_COMMENTS_RE = re.compile(r"لا توجد تعليقات حتى الآن|كن أول من يعلّق|كن أول من يعلق|No comments yet|Be the first to comment")
NO_COMMENTS_JS = NO_COMMENTS_RE.pattern
# صاحب المنشور قفل التعليقات: برضه مفيش زرار فرز ولا «لا توجد تعليقات» (اتشاف في تشغيل حقيقي)
COMMENTS_OFF_RE = re.compile(r"تم إيقاف تشغيل التعليقات|تم إيقاف التعليقات|Commenting has been turned off|"
                             r"turned off commenting|Comments are turned off|comments have been turned off")
COMMENTS_OFF_JS = COMMENTS_OFF_RE.pattern
# المنشور اتمسح أو اتخفى بين ما شفناه في الموجز وفتحناه (اتشاف في تشغيل حقيقي)
UNAVAILABLE_RE = re.compile(r"هذا المحتوى غير متوفر حاليًا|هذا المحتوى غير متاح|This content isn.t available")
# جروب خاص والحساب مش عضو: مفيش موجز خالص، بس زرار «انضمام»
PRIVATE_RE = re.compile(r"مجموعة\s*\u200f?\s*خاصة|Private group")
JOIN_RE = re.compile(r"انضمام إلى المجموعة|Join group")
EXPAND_RE = re.compile(
    r"(عرض المزيد من التعليقات|عرض التعليقات السابقة|عرض المزيد من الردود|"
    r"عرض كل الردود|عرض الردود|عرض \d+ من الردود|عرض \d+ رد|"
    r"View more comments|View previous comments|View more replies|"
    r"View all \d+ replies|View \d+ repl)"
)
NEWEST_RE = re.compile(r"^(الأحدث|Newest)\b")
SORT_OPEN_RE = re.compile(r"^(الأكثر ملاءمة|كل التعليقات|الأحدث|Most relevant|All comments|Newest)\b")


def group_key(url: str) -> str:
    match = GROUP_RE.search(url.strip())
    if not match:
        raise SystemExit(f"مش رابط جروب فيسبوك: {url}")
    return match.group(1)


_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_MONTHS = {
    "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "ابريل": 4, "مايو": 5, "يونيو": 6, "يوليو": 7,
    "أغسطس": 8, "اغسطس": 8, "سبتمبر": 9, "أكتوبر": 10, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
# الوحدة -> دقايق. فيسبوك بيكتب «٢٥ د» و «٢ س» و «3h» و «منذ ٤ أيام»
_UNITS = [
    (r"(?:د|دقيقة|دقائق|دقيقتين|m|min|mins|minutes?)", 1),
    (r"(?:س|ساعة|ساعات|ساعتين|h|hr|hrs|hours?)", 60),
    (r"(?:ي|يوم|أيام|ايام|يومين|d|days?)", 60 * 24),
    (r"(?:أ|أسبوع|أسابيع|اسبوع|w|wk|weeks?)", 60 * 24 * 7),
]


def normalize_fb_time(text: str, now: dt.datetime | None = None) -> str:
    """وقت فيسبوك -> «YYYY-MM-DD HH:MM». لو مش مفهوم بيرجع النص زي ما هو.

    بيفهم: «الأربعاء، 21 أكتوبر 2026 الساعة 3:38 م» و «Wednesday, October 21, 2026 at 3:38 PM»
    والنسبي: «٢٥ د» و «منذ ٢ ساعة» و «3h» و «الآن» / «Just now» (محسوب من وقت السحب).
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    now = now or dt.datetime.now()
    s = raw.translate(_AR_DIGITS).replace("،", ",").replace("منذ", "").strip()
    low = s.lower()

    m = re.search(r"(\d{1,2})\s+([^\s\d,]+)\s+(\d{4})", s) or re.search(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", s)
    if m:
        a, b, year = m.groups()
        day, month = (int(a), _MONTHS.get(b.lower())) if a.isdigit() else (int(b), _MONTHS.get(a.lower()))
        clock = re.search(r"(\d{1,2}):(\d{2})\s*(م|ص|pm|am)?", low)
        if month:
            hour, minute = (int(clock.group(1)), int(clock.group(2))) if clock else (0, 0)
            ampm = clock.group(3) if clock else None
            if ampm in ("م", "pm") and hour < 12:
                hour += 12
            if ampm in ("ص", "am") and hour == 12:
                hour = 0
            try:
                return dt.datetime(int(year), month, day, hour, minute).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                return raw

    # من غير سنة: «11 أكتوبر الساعة 5:38 ص» / «October 11 at 5:38 AM» — السنة الحالية،
    # إلا لو التاريخ كده يبقى في المستقبل (يبقى من السنة اللي فاتت)
    found = None
    for m in (re.search(r"(\d{1,2})\s+([^\s\d,]+)", s), re.search(r"([A-Za-z]+)\s+(\d{1,2})\b", s)):
        if m:
            a, b = m.groups()
            day, month = (int(a), _MONTHS.get(b.lower())) if a.isdigit() else (int(b), _MONTHS.get(a.lower()))
            if month:
                found = (day, month)
                break
    if found:
        day, month = found
        clock = re.search(r"(\d{1,2}):(\d{2})\s*(م|ص|pm|am)?", low)
        hour, minute = (int(clock.group(1)), int(clock.group(2))) if clock else (0, 0)
        ampm = clock.group(3) if clock else None
        if ampm in ("م", "pm") and hour < 12:
            hour += 12
        if ampm in ("ص", "am") and hour == 12:
            hour = 0
        try:
            when = dt.datetime(now.year, month, day, hour, minute)
            if when > now + dt.timedelta(days=1):
                when = when.replace(year=now.year - 1)
            return when.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return raw

    if low in ("الآن", "just now", "now") or low.startswith("الآن"):
        return now.strftime("%Y-%m-%d %H:%M")
    if low.startswith(("أمس", "امس", "yesterday")):
        clock = re.search(r"(\d{1,2}):(\d{2})\s*(م|ص|pm|am)?", low)
        day = now - dt.timedelta(days=1)
        if clock:
            hour = int(clock.group(1)) % 12 + (12 if clock.group(3) in ("م", "pm") else 0)
            day = day.replace(hour=hour, minute=int(clock.group(2)))
        return day.strftime("%Y-%m-%d %H:%M")
    compact = re.sub(r"\s+", "", low)
    for unit, minutes in _UNITS:
        m = re.fullmatch(rf"(\d+){unit}", compact)
        if m:
            return (now - dt.timedelta(minutes=int(m.group(1)) * minutes)).strftime("%Y-%m-%d %H:%M")
    return raw


def clean_title(title: str) -> str:
    """«(2) اسم الجروب | Facebook» -> «اسم الجروب». الـ (2) عدد الإشعارات مش جزء من الاسم."""
    parts = [p.strip() for p in (title or "").split("|")]
    parts = [p for p in parts if p and p != "Facebook"]
    name = parts[0] if parts else ""
    return re.sub(r"^\(\d+\)\s*", "", name).strip()


def without_token(payload: dict) -> dict:
    """نسخة للحفظ على الديسك: التوكن مالوش لازمة في الدَمب."""
    return {k: v for k, v in payload.items() if k != "token"}


def sheet_comment(comment: dict, post_content: str = "") -> dict:
    return {
        "comment_id": comment.get("comment_id") or "",
        "parent_id": comment.get("parent_id") or comment.get("parent_comment_id") or "",
        "reply_comment_id": comment.get("reply_comment_id") or "",
        "is_reply": bool(comment.get("is_reply")),
        "reply_target_id": comment.get("reply_target_id") or comment.get("comment_id") or "",
        "author": comment.get("author") or "",
        "author_id": comment.get("author_id") or "",
        "author_url": comment.get("author_url") or "",
        "text": comment.get("text") or "",
        "created_time": comment.get("created_time") or "",
        "permalink": comment.get("permalink") or "",
        "post content": post_content or comment.get("post content") or comment.get("post_content") or "",
        "reply": comment.get("reply") or "",
        "reply_status": comment.get("reply_status") or "",
    }


def sheet_post_payload(group_id: str, post: dict, comments: list[dict]) -> dict:
    return {
        "token": TOKEN,
        "group_id": group_id,
        "posts": [{
            "post_id": post.get("post_id"),
            "author": post.get("author") or "",
            "author_id": post.get("author_id") or "",
            "text": post.get("text") or post.get("post_content") or "",
            "post content": post.get("post_content") or post.get("text") or "",
            "created_time": post.get("created_time") or "",
            "url": post.get("url") or post.get("permalink") or "",
            "permalink": post.get("permalink") or post.get("url") or "",
            "comment_sort": post.get("comment_sort") or "الأحدث",
            "comments": [sheet_comment(c, post.get("post_content") or post.get("text") or "") for c in comments],
        }],
    }


# Apps Script بيرجّع HTTP 500 عابر أحيانًا (اتشاف في تشغيل حقيقي، والطلب اللي بعده بثانية نجح).
# إعادة المحاولة آمنة: الشيت بيعمل upsert بـ post_id / comment_id، فمفيش تكرار.
SHEET_ATTEMPTS = 3
SHEET_BACKOFF = (5, 15)
# 404 «يتعذر فتح الملف في الوقت الحالي» عابر من Google وقت الضغط (RUN6: بعد 110 طلب ناجح، والـ ping بعدها
# بثواني نجح) — كان بيوقف الجروب. 429 = طلبات كتير. الاتنين بيتعادوا زي 5xx؛ 403 والتوكن الغلط لأ.
SHEET_RETRY_4XX = (404, 429)


def apps_script_error(page: str) -> str:
    """صفحة خطأ Google (HTML تقيل كله سكربتات) -> الرسالة اللي تتقري."""
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", page or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text[:300] or (page or "")[:300]


def sheet_post(payload: dict) -> str:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for attempt in range(1, SHEET_ATTEMPTS + 1):
        req = urllib.request.Request(
            WEBAPP,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                answer = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = apps_script_error(exc.read().decode("utf-8", "replace"))
            if (exc.code < 500 and exc.code not in SHEET_RETRY_4XX) or attempt == SHEET_ATTEMPTS:
                raise SystemExit(f"الشيت رفض الإرسال HTTP {exc.code} (محاولة {attempt}): {detail}") from exc
            problem = f"HTTP {exc.code}: {detail}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt == SHEET_ATTEMPTS:
                raise
            problem = str(exc)
        else:
            # Apps Script بيرجّع 200 حتى لو رفض (توكن غلط، خطأ في السكربت، صفحة خطأ HTML)
            try:
                parsed = json.loads(answer)
            except ValueError:
                raise SystemExit(f"رد الشيت مش JSON — غالبًا صفحة خطأ من Apps Script: {apps_script_error(answer)}") from None
            if isinstance(parsed, dict) and parsed.get("ok") is True:
                return answer
            busy = isinstance(parsed, dict) and "lock" in str(parsed.get("error", "")).lower()
            if not busy or attempt == SHEET_ATTEMPTS:
                raise SystemExit(f"الشيت رفض البيانات: {answer[:300]}")
            problem = f"القفل مشغول: {parsed.get('error')}"
        wait = SHEET_BACKOFF[min(attempt, len(SHEET_BACKOFF)) - 1]
        print(f"الشيت مارضيش ({problem[:160]}) — محاولة {attempt + 1} بعد {wait} ثانية", file=sys.stderr)
        time.sleep(wait)
    raise AssertionError("unreachable")


class SheetSender:
    """بيبعت للشيت في thread جنبي، فالسحب مابيستناش Apps Script (4–9 ث للطلب في تشغيلات حقيقية).

    اللي مستني في الطابور بيتجمع في طلب واحد (نفس الجروب، لحد batch_size تعليق و MAX_POSTS منشور)،
    فعدد الطلبات بيقل كمان. الترتيب محفوظ والشيت upsert بالـ id، فالتجميع مابيغيّرش اللي بيتكتب.
    أي فشل بيوقف السحب زي الأول: raise_if_failed بعد كل منشور، و close في آخر الجروب.
    """
    MAX_POSTS = 25

    def __init__(self, batch_size: int):
        self.batch_size = batch_size
        self.queue: queue.Queue = queue.Queue()
        self.error: BaseException | None = None
        self.requests = 0
        self.thread = threading.Thread(target=self._run, name="sheet-sender", daemon=True)
        self.thread.start()

    def put(self, payload: dict) -> None:
        self.raise_if_failed()
        self.queue.put(payload)

    def raise_if_failed(self) -> None:
        if self.error is not None:
            raise SystemExit(f"الإرسال للشيت وقف: {self.error}")

    def close(self, check: bool = True) -> None:
        """بيستنى لحد ما كل اللي في الطابور يتبعت."""
        self.queue.put(None)
        self.thread.join()
        if check:
            self.raise_if_failed()

    @staticmethod
    def _comments(payload: dict) -> int:
        return sum(len(p.get("comments") or []) for p in payload["posts"])

    def _merged(self, items: list[dict]) -> list[dict]:
        out: list[dict] = []
        for payload in items:
            last = out[-1] if out else None
            if (last and last.get("group_id") == payload.get("group_id")
                    and len(last["posts"]) + len(payload["posts"]) <= self.MAX_POSTS
                    and self._comments(last) + self._comments(payload) <= self.batch_size):
                for post in payload["posts"]:
                    # نفس المنشور مرتين في طلب واحد: الشيت بياخد أول مرة بس، فالتعليقات تتضم في واحد
                    same = next((p for p in last["posts"] if p.get("post_id") == post.get("post_id")), None)
                    if same:
                        same["comments"] = same["comments"] + list(post.get("comments") or [])
                    else:
                        last["posts"].append(dict(post, comments=list(post.get("comments") or [])))
            else:
                out.append(dict(payload, posts=[dict(p, comments=list(p.get("comments") or []))
                                                for p in payload["posts"]]))
        return out

    def _run(self) -> None:
        done = False
        while not done:
            items = [self.queue.get()]
            while True:
                try:
                    items.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            if None in items:
                done = True
                items = items[:items.index(None)]
            for payload in self._merged(items):
                if self.error is not None:
                    break
                try:
                    answer = sheet_post(payload)
                except BaseException as exc:  # SystemExit من sheet_post كمان
                    self.error = exc
                    break
                self.requests += 1
                print(f"sheet: {len(payload['posts'])} posts / {self._comments(payload)} comments: {answer[:220]}",
                      flush=True)


POLL_MS = 250


def wait_until(page, ready, max_ms: int) -> bool:
    """بيستنى لحد ما ready() يبقى True، كل POLL_MS، لحد max_ms بالكتير.

    بدل الانتظار الثابت: نفس الحد الأقصى اللي كان قبل كده، فعمره ما يبقى أبطأ — بس بيخلص أول ما
    الصفحة تجهز. الانتظارات الثابتة كانت نص وقت المنشور (27.6 من 56.7 ث على 5 منشورات، 2026-10-22).
    """
    waited = 0
    while True:
        if ready():
            return True
        if waited >= max_ms:
            return False
        step = min(POLL_MS, max_ms - waited)
        page.wait_for_timeout(step)
        waited += step


def own_group_post_id(href: str, group_id: str) -> str | None:
    """رقم المنشور لو الرابط لمنشور في الجروب ده بالذات، غير كده None.

    الإشعارات والاقتراحات بتعرض منشورات جروبات تانية على نفس الصفحة. لو الجروب
    متدّي باسم (slug) مش رقم، مانقدرش نقارن، فبنقبل أي جروب.
    """
    match = POST_GROUP_RE.search(href or "")
    if not match:
        return None
    if group_id.isdigit() and match.group(1) != group_id:
        return None
    return match.group(2)


# روابط المنشورات من الموجز بس: مش من نافذة الإشعارات (banner) ولا أي dialog ولا الشريط الجانبي
FEED_LINKS_JS = """() => {
  const scope = document.querySelector("div[role='main']") || document.body;
  return [...scope.querySelectorAll("a[href*='/posts/'], a[href*='/permalink/']")]
    .filter(a => !a.closest("[role='dialog'], [role='banner'], [role='complementary']"))
    .map(a => a.href);
}"""


def feed_post_ids(page, group_id: str) -> list[str]:
    ids: list[str] = []
    for href in page.evaluate(FEED_LINKS_JS):
        post_id = own_group_post_id(href, group_id)
        if post_id and post_id not in ids:
            ids.append(post_id)
    return ids


# موجز فاضي بيتعاد تحميله مرة: في RUN6 جروب نشط (17.5 ألف عضو، منشور من 16 دقيقة) طلع 0 منشورات لأن الموجز
# ماترسمش في الوقت والـ CPU عند 100% مع 4 نوافذ — وبعدها بشوية نفس الصفحة كان فيها منشورات عادي
FEED_ATTEMPTS = 2


def collect_post_ids(page, group_id: str, max_posts: int) -> tuple[list[str], str]:
    for attempt in range(1, FEED_ATTEMPTS + 1):
        page.goto(
            f"https://www.facebook.com/groups/{group_id}?sorting_setting=CHRONOLOGICAL",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        # الموجز بيترسم بعد DOMContentLoaded: لحد ما يظهر منشور من الجروب أو يبان إنه خاص (4 ث بالكتير زي الأول)
        wait_until(page, lambda: bool(feed_post_ids(page, group_id)) or group_is_private_for_us(page), 4000)
        if group_is_private_for_us(page):
            print(f"الجروب {group_id} خاص والحساب مش عضو فيه — مفيش منشورات نقدر نشوفها", file=sys.stderr)
            return [], ""
        if not feed_post_ids(page, group_id) and post_unavailable(page):
            # «هذا المحتوى غير متوفر حاليًا» على صفحة الجروب نفسها: اتمسح أو اتخفى عن الحساب — مفيش لازمة نعيد
            print(f"الجروب {group_id} مش متاح (فيسبوك: «هذا المحتوى غير متوفر حاليًا») — اتمسح أو اتخفى عن الحساب",
                  file=sys.stderr)
            return [], ""
        ids, label = _scroll_feed(page, group_id, max_posts)
        if ids:
            return ids, label
        if attempt < FEED_ATTEMPTS:
            print(f"موجز الجروب {group_id} طلع فاضي — بنعيد تحميله (محاولة {attempt + 1})", file=sys.stderr)
    # جروب مش موجود كان بيخلص بـ 0 منشورات من غير ما يقول حاجة (اتشاف في تشغيل حقيقي)
    print(f"موجز الجروب {group_id} مافيهوش ولا منشور ظاهر — الجروب مش موجود أو فاضي أو مش متاح للحساب",
          file=sys.stderr)
    return [], label


def _scroll_feed(page, group_id: str, max_posts: int) -> tuple[list[str], str]:
    # textContent مش innerText: innerText بيجبر layout على كل زرار، وده أخد 18 ثانية على فيسبوك
    label = page.evaluate(
        """() => {
          const t = [...document.querySelectorAll('[role="button"]')]
            .map(e => (e.textContent || '').replace(/\\s+/g, ' ').trim())
            .find(x => x.includes('فرز موجز المجموعة'));
          return t || '';
        }"""
    )
    ids: list[str] = []
    stale = 0
    while len(ids) < max_posts and stale < 6:
        before = len(ids)
        for post_id in feed_post_ids(page, group_id):
            if post_id not in ids:
                ids.append(post_id)
                if len(ids) >= max_posts:
                    break
        stale = stale + 1 if len(ids) == before else 0
        if len(ids) >= max_posts:
            break
        page.mouse.wheel(0, 2200)
        # لحد ما منشور جديد يترسم (1.8 ث بالكتير زي الأول)
        wait_until(page, lambda: any(p not in ids for p in feed_post_ids(page, group_id)), 1800)
    return ids[:max_posts], label


# جذر المنشور المفتوح: أعرض dialog فيه منشور (article من غير aria-label)، وإلا role=main.
# مش «أول dialog»: نافذة الإشعارات dialog مخفي بيبقى أول واحد في الصفحة.
POST_ROOT_JS = r"""
  const postRoot = () => {
    const dialogs = [...document.querySelectorAll("div[role='dialog']")]
      .filter(d => d.querySelector("[role='article']:not([aria-label])"));
    return dialogs.sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width)[0]
      || document.querySelector("div[role='main']") || document.body;
  };
"""


# حالات فرز مقبولة: «الأحدث»، أو مفيش تعليقات خالص / التعليقات مقفولة (الترتيب مالوش معنى)
SORT_OK = ("الأحدث", "no-comments", "comments-off", "single-comment", "newest-by-time")
# حالات مفيهاش تعليقات أصلًا: لقطة الهيدر وخلاص، من غير لفّات تمرير
NO_COMMENT_STATES = ("no-comments", "comments-off")


def no_comments_shown(page) -> bool:
    """فيسبوك كاتب «لا توجد تعليقات حتى الآن» ومفيش ولا تعليق ظاهر."""
    return bool(page.evaluate(
        "(pattern) => {" + POST_ROOT_JS + """
          const root = postRoot();
          if (root.querySelectorAll("div[role='article'][aria-label]").length) return false;
          return new RegExp(pattern).test(root.textContent || '');
        }""",
        NO_COMMENTS_JS,
    ))


def page_text_matches(page, pattern: str) -> bool:
    """نص ظاهر في جذر المنشور (مش الصفحة كلها: الموجز ورا النافذة ممكن يكون فيه نفس الكلام)."""
    return bool(page.evaluate(
        "(pattern) => {" + POST_ROOT_JS + "return new RegExp(pattern).test(postRoot().textContent || ''); }",
        pattern,
    ))


def comments_off_shown(page) -> bool:
    return page_text_matches(page, COMMENTS_OFF_JS)


def post_unavailable(page) -> bool:
    """«هذا المحتوى غير متوفر حاليًا»: المنشور اتمسح أو اتخفى."""
    return bool(page.evaluate(
        "(p) => { const b = document.body; return new RegExp(p).test(b ? (b.innerText || b.textContent || '') : ''); }",
        UNAVAILABLE_RE.pattern))


def group_is_private_for_us(page) -> bool:
    """جروب خاص والحساب مش عضو: «مجموعة خاصة» + زرار «انضمام» ومفيش موجز."""
    return bool(page.evaluate(
        """([priv, join]) => {
          const main = document.querySelector("div[role='main']") || document.body;
          const text = main.textContent || '';
          return !document.querySelector("[role='feed']") && new RegExp(priv).test(text) && new RegExp(join).test(text);
        }""",
        [PRIVATE_RE.pattern, JOIN_RE.pattern],
    ))


def distinct_comment_ids(page) -> int:
    """كام تعليق مختلف ظاهر (فيسبوك أحيانًا بيرسم نفس التعليق مرتين بنفس الـ id)."""
    return int(page.evaluate(
        "() => {" + POST_ROOT_JS + r"""
          const ids = new Set();
          postRoot().querySelectorAll("div[role='article'][aria-label] a[href*='comment_id=']").forEach(a => {
            try {
              const q = new URL(a.href).searchParams;
              ids.add(q.get('reply_comment_id') || q.get('comment_id'));
            } catch (e) {}
          });
          return ids.size;
        }"""
    ))


def comments_newest_first(page) -> bool:
    """التعليقات الظاهرة (من غير الردود) مترتبة من الأحدث للأقدم حسب وقتها الكامل؟

    بنستخدمها بس بعد ما ضغطنا «الأحدث» فعلًا وفيسبوك شال الزرار، فمانقدرش نقرا الحالة منه.
    دقة الوقت دقيقة، فالمتساوي مقبول. أي وقت مش مفهوم = مش متأكدين = False.
    """
    stamps = page.evaluate(
        "() => {" + POST_ROOT_JS + r"""
          return [...postRoot().querySelectorAll("div[role='article'][aria-label]")]
            .map(a => a.querySelector("a[href*='comment_id=']"))
            .filter(l => l && !new URL(l.href).searchParams.get('reply_comment_id'))
            .map(l => l.getAttribute('aria-label') || l.innerText || '');
        }"""
    )
    times = [normalize_fb_time(t) for t in stamps]
    if not times or not all(re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", t) for t in times):
        return False
    return all(a >= b for a, b in zip(times, times[1:]))


def close_open_menu(page) -> None:
    """Escape بس لو في قائمة مفتوحة. Escape من غير قائمة بيقفل نافذة المنشور نفسها
    وفيسبوك بيرجع للصفحة الرئيسية."""
    if page.locator("[role='menu']").count():
        page.keyboard.press("Escape")


def comment_count(page) -> int:
    """كام تعليق/رد مرسوم في جذر المنشور دلوقتي (عشان نعرف إن الضغطة أو التمرير جاب جديد)."""
    return int(page.evaluate(
        "() => {" + POST_ROOT_JS + "return postRoot().querySelectorAll(\"div[role='article'][aria-label]\").length; }"))


# المنشور اترسم: تعليق، أو زرار فرز، أو فيسبوك كاتب إن مفيش تعليقات / مقفولة / المنشور اتمسح
POST_READY_JS = "(p) => {" + POST_ROOT_JS + r"""
  const root = postRoot();
  if (root.querySelector("div[role='article'][aria-label]")) return true;
  const sort = new RegExp(p.sort);
  if ([...root.querySelectorAll('[role="button"]')].some(b => sort.test((b.textContent || '').trim()))) return true;
  const text = root.textContent || '';
  return new RegExp(p.none).test(text) || new RegExp(p.off).test(text)
    || new RegExp(p.gone).test(document.body ? document.body.textContent || '' : '');
}"""


HIDDEN_COMMENTS_JS = r"عرض التعليقات المخفية|View hidden comments|See hidden comments"
# عداد التعليقات = الرقم جوه زرار «كتابة تعليق» تحت المنشور (اتشاف في صفحات حقيقية 2026-10-22)
COMMENT_COUNTER_CSS = ("[role='button'][aria-label='كتابة تعليق'], [role='button'][aria-label='Leave a comment'], "
                       "[role='button'][aria-label='Comment']")
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def reveal_hidden_comments(page) -> int:
    """يضغط «عرض التعليقات المخفية» (تعليقات فيسبوك أو الأدمن خبّاها). عرض بس، مفيش كتابة.

    اتشاف في RUN6: منشور كل تعليقاته مخفية = مفيش ولا تعليق مرسوم ولا زرار فرز، فالجروب كله كان بيقف.
    """
    before = comment_count(page)
    clicked = int(page.evaluate(
        "(pattern) => {" + POST_ROOT_JS + r"""
          const re = new RegExp(pattern);
          const buttons = [...postRoot().querySelectorAll('[role="button"]')]
            .filter(b => re.test((b.textContent || '').replace(/\s+/g, ' ').trim()));
          buttons.forEach(b => b.click());
          return buttons.length;
        }""", HIDDEN_COMMENTS_JS))
    if clicked:
        wait_until(page, lambda: comment_count(page) > before, 2500)
    return clicked


def comment_total(page) -> int | None:
    """عدد التعليقات اللي فيسبوك كاتبه تحت المنشور (بالردود). None لو مش ظاهر أو مختصر («1.2 ألف»)."""
    text = page.evaluate(
        "(css) => {" + POST_ROOT_JS + r"""
          const b = postRoot().querySelector(css);
          return b ? (b.textContent || '').trim() : null;
        }""", COMMENT_COUNTER_CSS)
    if text is None:
        return None
    digits = text.translate(ARABIC_DIGITS).strip()
    return int(digits) if digits.isdigit() else None


def newest_first_threads(comments: list[dict]) -> list[dict]:
    """ترتيب «الأحدث» بإيدنا لما فيسبوك مايديناش زرار فرز: التعليقات بالوقت الكامل (الأحدث فوق)،
    وكل رد تحت تعليقه بنفس ترتيبه. الوقت المتساوي بيفضل بترتيب الصفحة."""
    replies: dict[str, list[dict]] = {}
    for c in comments:
        if c["is_reply"]:
            replies.setdefault(c["parent_id"], []).append(c)
    ordered: list[dict] = []
    for top in sorted((c for c in comments if not c["is_reply"]), key=lambda c: c["created_time"], reverse=True):
        ordered.append(top)
        ordered += replies.pop(top["comment_id"], [])
    for rest in replies.values():  # ردود تعليقها نفسه مش ظاهر
        ordered += rest
    return ordered


# من غير \b: في JavaScript الحروف العربي مش «word»، فـ \b بعد «الأحدث» عمره ما يطابق
SORT_BUTTON_JS = r"^(الأكثر ملاءمة|كل التعليقات|الأحدث|Most relevant|All comments|Newest)"


def post_ready(page) -> bool:
    return bool(page.evaluate(POST_READY_JS, {"sort": SORT_BUTTON_JS, "none": NO_COMMENTS_JS,
                                              "off": COMMENTS_OFF_JS, "gone": UNAVAILABLE_RE.pattern}))


def sort_comments_newest(page) -> str:
    """يفتح قائمة فرز التعليقات ويختار «الأحدث». الضغط لازم يكون حقيقي مش JavaScript.

    بيرجّع «الأحدث»، أو «no-comments» لو المنشور مفيهوش تعليقات خالص (مفيش زرار فرز
    أصلًا والترتيب مالوش معنى)، أو سبب الفشل.
    """
    name = re.compile(r"الأحدث|Newest")
    opener_name = re.compile(r"الأكثر ملاءمة|كل التعليقات|Most relevant|All comments")
    # نفس المهلة الكلية اللي كانت (1.5 + 8×0.7 ث)، بس كل ربع ثانية بدل ما نستنى الأول
    for _ in range((1500 + 8 * 700) // POLL_MS):
        if page.get_by_role("button", name=name).count():
            return "الأحدث"
        if page.get_by_role("button", name=opener_name).count():
            break
        if no_comments_shown(page):
            return "no-comments"
        if comments_off_shown(page):
            return "comments-off"
        page.wait_for_timeout(POLL_MS)
    opener = page.get_by_role("button", name=opener_name)
    if not opener.count():
        if no_comments_shown(page):
            return "no-comments"
        if comments_off_shown(page):
            return "comments-off"
        # تعليق واحد بس: فيسبوك بيشيل زرار الفرز، والترتيب مالوش معنى (اتشاف في تشغيل حقيقي)
        return "single-comment" if distinct_comment_ids(page) == 1 else "missing"
    try:
        target = opener.last
        target.scroll_into_view_if_needed(timeout=4000)
        target.click(timeout=5000)
    except Exception:
        return "detached"
    menu_item = page.get_by_role("menuitem", name=re.compile(r"الأحدث|Newest|التعليقات الأحدث"))
    menu_text = page.get_by_text(re.compile(r"^الأحدث"))
    wait_until(page, lambda: bool(menu_item.count() or menu_text.count()), 1000)
    option = menu_item if menu_item.count() else menu_text
    if not option.count():
        close_open_menu(page)
        return "no-option"
    option.first.click(timeout=5000)
    if wait_until(page, lambda: bool(page.get_by_role("button", name=name).count()), 2000):
        # الزرار بيتغير قبل ما القايمة تتعاد ترتيبها (اتشاف 2026-10-22: أول لقطة طلعت بترتيب «الأكثر ملاءمة»).
        # نستنى لحد ما التعليقات الظاهرة تبقى فعلًا من الأحدث للأقدم بالوقت الكامل؛ بعدها لو لسه لأ، نكمل
        # زي الأول (الحد الأقصى 2 + 2.5 ث، والانتظار الثابت القديم كان 2 ث بس قبل أول لقطة)
        wait_until(page, lambda: comments_newest_first(page), 2500)
        return "الأحدث"
    # ضغطنا «الأحدث» وفيسبوك شال الزرار خالص (اتشاف في تشغيل حقيقي): نتأكد من الترتيب نفسه بالوقت
    if not page.get_by_role("button", name=opener_name).count() and comments_newest_first(page):
        return "newest-by-time"
    return "unverified"


def expand_comments(page, rounds: int = 30) -> int:
    """يفتح كل التعليقات وكل أشجار الردود. لا يضغط زر «رد» نفسه. بيرجّع كام زرار اتضغط.

    textContent مش innerText: innerText بيعمل layout للنافذة كلها مع كل زرار (2.6 ثانية للنداء).
    """
    total = 0
    for _ in range(rounds):
        before = comment_count(page)
        clicked = page.evaluate(
            r"""() => {""" + POST_ROOT_JS + r"""
              const root = postRoot();
              const wanted = /عرض المزيد من التعليقات|عرض التعليقات السابقة|عرض المزيد من الردود|عرض كل الردود|عرض الردود|عرض \d+ من الردود|عرض \d+ رد|عرض التعليقات المخفية|View more comments|View previous comments|View more replies|View all \d+ replies|View \d+ repl|View hidden comments|See hidden comments/;
              const buttons = [...root.querySelectorAll('[role="button"]')].filter(e => {
                const t = (e.textContent || '').replace(/\s+/g, ' ').trim();
                return t && t !== 'رد' && t !== 'Reply' && wanted.test(t);
              });
              if (!buttons.length) return 0;
              let n = 0;
              // 20 مش 8: منشور فيه عشرات «عرض الردود» كان بياخد لفّة كاملة (+انتظار) لكل 8
              for (const button of buttons.slice(0, 20)) {
                button.click();
                n += 1;
              }
              return n;
            }"""
        )
        if not clicked:
            page.mouse.wheel(0, 1400)
            more = lambda: bool(page.evaluate(  # noqa: E731
                r"""() => {""" + POST_ROOT_JS + r"""
                  const root = postRoot();
                  const wanted = /عرض المزيد من التعليقات|عرض الردود|عرض \d+ رد|عرض التعليقات المخفية|View more comments|View .*repl|View hidden comments|See hidden comments/;
                  return [...root.querySelectorAll('[role="button"]')].some(e => wanted.test((e.textContent || '').replace(/\s+/g, ' ').trim()));
                }"""
            ))
            if not wait_until(page, more, 700):
                return total
            continue
        total += clicked
        # لحد ما التعليقات/الردود اللي اتطلبت تترسم (1.1 ث بالكتير زي الأول)
        wait_until(page, lambda: comment_count(page) > before, 1100)
    return total


def visible_snapshot(page, group_id: str, post_id: str) -> dict:
    """يقرأ المنشور المفتوح والتعليقات الظاهرة دلوقتي.

    كل حاجة متقيّدة بـ article المنشور نفسه جوه جذره (postRoot): فيسبوك بيعرض الموجز
    وراء نافذة المنشور، وأول data-ad-preview أو أول h2 في الصفحة ممكن يبقوا لمنشور تاني.
    """
    return page.evaluate(
        r"""(ids) => {""" + POST_ROOT_JS + r"""
          const postId = ids.postId;
          const groupId = ids.groupId;
          const clean = (href) => {
            if (!href) return '';
            try {
              const u = new URL(href, location.href);
              // __cft__[0] و __tn__ و fbclid: توكنات تتبّع بتتغيّر مع كل تحميل
              [...u.searchParams.keys()].filter(k => k.startsWith('__') || k === 'fbclid')
                .forEach(k => u.searchParams.delete(k));
              u.hash = '';
              return u.toString();
            } catch (e) { return href.split(/[?&]__cft__/)[0]; }
          };
          const userIdFrom = (href) => {
            const m = (href || '').match(/\/user\/(\d+)|[?&]id=(\d+)/);
            return m ? (m[1] || m[2]) : '';
          };
          const named = a => (a.innerText || '').trim();
          const root = postRoot();
          const ownLink = `a[href*='multi_permalinks=${postId}'], a[href*='/posts/${postId}']:not([href*='comment_id='])`;
          const stories = [...root.querySelectorAll("[role='article']:not([aria-label])")];
          const story = stories.find(a => a.querySelector(ownLink)) || stories[0] || null;
          const scope = story || root;

          const comments = [];
          root.querySelectorAll("div[role='article'][aria-label]").forEach(article => {
            const rawLink = [...article.querySelectorAll("a[href*='comment_id=']")].map(x => x.href)[0] || '';
            const authorLink = [...article.querySelectorAll("a[href*='/user/']")].find(named) || null;
            const text = [...article.querySelectorAll("div[dir='auto']")].map(d => d.innerText.trim()).filter(Boolean).join('\n');
            const timeNode = article.querySelector("a[href*='comment_id=']");
            // aria-label بتاع رابط الوقت فيه الوقت الكامل («21 أكتوبر 2026 الساعة 3:38 م»)، والنص نسبي («٢٥ د»)
            const timeAria = timeNode ? (timeNode.getAttribute('aria-label') || '') : '';
            comments.push({
              link: clean(rawLink),
              author: authorLink ? named(authorLink) : '',
              author_id: userIdFrom(authorLink ? authorLink.href : ''),
              author_url: authorLink ? clean(authorLink.href) : '',
              text,
              // ستيكر/GIF/صورة: أي img أو video برّه نص التعليق (الإيموجي img جوه dir=auto، والصورة الشخصية svg)
              has_media: [...article.querySelectorAll("img, video")].some(n => !n.closest("div[dir='auto']"))
                || !!article.querySelector("a[href*='/photo/']"),
              created_time: /[0-9٠-٩]{4}/.test(timeAria) ? timeAria : (timeNode ? (timeNode.innerText || '').trim() : ''),
              aria_label: article.getAttribute('aria-label') || ''
            });
          });

          // صاحب المنشور: أول رابط /user/ عليه اسم جوه المنشور. مش أول h2: ده اسم الجروب
          let authorLink = story ? [...story.querySelectorAll("a[href*='/user/']")].find(named) : null;
          let author = authorLink ? named(authorLink) : '';
          if (!author) {
            // عنوان النافذة «منشور ‏فلان‏» / «Someone's post» — اسم من غير id
            const h = ((root.querySelector('h2') || {}).innerText || '').replace(/[\u200e\u200f\u202a-\u202e]/g, '').trim();
            const m = h.match(/^منشور\s+(.+)$/) || h.match(/^(.+)'s post$/i);
            author = m ? m[1].trim() : '';
          }
          const timeLink = scope.querySelector(`a[href*='multi_permalinks=${postId}']`)
            || scope.querySelector(`a[href*='/posts/${postId}']:not([href*='comment_id='])`);

          const msg = scope.querySelector("div[data-ad-preview='message'], div[data-ad-comet-preview='message']");
          const titleParts = (document.title || '').split('|').map(s => s.trim()).filter(s => s && s !== 'Facebook');
          const titleText = titleParts.length >= 2 ? titleParts[titleParts.length - 1] : '';
          // منشور متشارك: رابط لمنشور جهة تانية (صفحة/حساب)، ومفيش data-ad-preview للنص
          const sharedLink = [...scope.querySelectorAll("a[href*='/posts/'], a[href*='permalink.php'], a[href*='story_fbid=']")]
            .map(a => a.href)
            .find(h => !h.includes(`/groups/${groupId}/`) && !h.includes('multi_permalinks') && !h.includes('comment_id=')) || '';
          const videos = [...scope.querySelectorAll('video')];
          const videoLink = [...scope.querySelectorAll("a[href*='/reel/'], a[href*='/videos/'], a[href*='/watch/']")]
            .map(a => a.href)[0] || '';
          let text = msg ? msg.innerText.trim() : '';
          // فيديو/ريل/منشور متشارك: الوصف مش في data-ad-preview
          if (!text && (sharedLink || videoLink || videos.length)) {
            const skip = new RegExp(ids.noComments + '|·');
            const candidates = [...scope.querySelectorAll("div[dir='auto'], span[dir='auto']")]
              .filter(n => !n.closest("[role='article'][aria-label]"))     // التعليقات جوه article المنشور
              .map(n => (n.innerText || '').trim())
              .filter(t => t && t !== author && !skip.test(t) && !/المقطع الصوتي|Original audio/i.test(t))
              .map(t => t.replace(/(\.\.\.|…)?\s*(عرض المزيد|See more)\s*$/, '').trim())
              .filter(t => t.length > 1);
            let best = candidates.sort((a, b) => b.length - a.length)[0] || '';
            // فيسبوك بيرسم النص مرتين (الظاهر + نسخة «عرض المزيد») جوه نفس العنصر،
            // ساعات لازقين وساعات بينهم سطر جديد (اتشاف على منشور متشارك حقيقي)
            for (const sep of ['', '\n', ' ']) {
              const half = (best.length - sep.length) / 2;
              if (half > 0 && Number.isInteger(half) && best.slice(half, half + sep.length) === sep
                  && best.slice(0, half) === best.slice(half + sep.length)) {
                best = best.slice(0, half);
                break;
              }
            }
            text = best;
          }
          if (!text) text = titleText;

          const external = [...scope.querySelectorAll("a[href*='l.facebook.com/l.php']")].map(a => {
            try { return clean(decodeURIComponent(new URL(a.href).searchParams.get('u') || '')); } catch (e) { return ''; }
          }).find(u => u && !/facebook\.com|fbcdn|meta\.ai/.test(u)) || '';
          // src الفيديو غالبًا blob: مالوش قيمة برّه الصفحة؛ رابط الريل/الفيديو نفسه أحسن
          const videoUrl = videos.map(v => v.currentSrc || v.src).find(u => /^https?:/.test(u || ''))
            || (videoLink ? clean(videoLink) : '');
          const photo = [...scope.querySelectorAll("a[href*='/photo/'], a[href*='/photos/']")].map(a => clean(a.href))[0] || '';
          const shared = sharedLink ? clean(sharedLink) : '';
          const isVideo = videos.length > 0 || !!videoLink;
          const kind = isVideo ? 'video' : external ? 'link' : shared ? 'share' : photo ? 'photo' : (text ? 'text' : '');
          // فيديو مايخدش رابط الصورة المصغّرة كأنه هو
          const media = isVideo ? videoUrl : (external || shared || photo);
          return {
            text,
            // «post content» في الشيت: الرابط/الميديا لو موجودة، وإلا النص — زي ما كان.
            // فيديو src بتاعه blob ومن غير وصف: رابط المنشور نفسه هو رابط الفيديو، بدل خانة فاضية
            post_content: media || text || (isVideo ? `https://www.facebook.com/groups/${groupId}/posts/${postId}/` : ''),
            content_kind: kind,
            media_url: media,
            story_found: !!story,
            author,
            author_id: userIdFrom(authorLink ? authorLink.href : ''),
            author_url: authorLink ? clean(authorLink.href) : '',
            created_time: timeLink ? (timeLink.getAttribute('aria-label') || timeLink.innerText || '').trim() : '',
            comments
          };
        }""",
        {"groupId": group_id, "postId": post_id, "noComments": NO_COMMENTS_JS},
    )


def normalize_comment(raw: dict, group_id: str, post_id: str) -> dict | None:
    link = raw.get("link") or ""
    match = CID_RE.search(link)
    reply = RCID_RE.search(link)
    if not match:
        return None
    parent_id = match.group(1)
    reply_id = reply.group(1) if reply else ""
    own_id = reply_id or parent_id
    permalink = f"https://www.facebook.com/groups/{group_id}/posts/{post_id}/?comment_id={parent_id}"
    if reply_id:
        permalink += f"&reply_comment_id={reply_id}"
    return {
        "group_id": group_id,
        "post_id": post_id,
        "comment_id": own_id,
        "parent_comment_id": parent_id if reply_id else "",
        "parent_id": parent_id if reply_id else "",
        "reply_comment_id": reply_id,
        "is_reply": bool(reply_id),
        "reply_target_id": own_id,
        "author": (raw.get("author") or "").strip(),
        "author_id": raw.get("author_id") or "",
        "author_url": raw.get("author_url") or "",
        "text": (raw.get("text") or "").strip(),
        "created_time": normalize_fb_time(raw.get("created_time") or ""),
        "created_time_raw": (raw.get("created_time") or "").strip(),
        "has_media": bool(raw.get("has_media")),
        "aria_label": raw.get("aria_label") or "",
        "permalink": permalink,
    }


def scroll_comment_pane(page) -> bool:
    """بيمرّر أطول حاوية قابلة للتمرير. بيرجّع True لو اتحركت فعلًا (False = آخر القايمة)."""
    return bool(page.evaluate(
        r"""() => {""" + POST_ROOT_JS + r"""
          const root = postRoot();
          const nodes = [root, ...root.querySelectorAll('div')];
          let best = null;
          let room = 0;
          for (const node of nodes) {
            const left = node.scrollHeight - node.scrollTop - node.clientHeight;
            if (node.clientHeight > 180 && left > room) {
              best = node;
              room = left;
            }
          }
          if (best) {
            const before = best.scrollTop;
            best.scrollTop += Math.max(700, best.clientHeight * 0.8);
            return best.scrollTop > before;
          }
          const y = window.scrollY;
          window.scrollBy(0, 900);
          return window.scrollY > y;
        }"""
    ))


_KIND_RANK = {"": 0, "text": 1, "photo": 2, "link": 3, "share": 3, "video": 4}


def richer_header(snap: dict, header: dict) -> bool:
    """لقطة أحدث أغنى من اللي عندنا؟ (فيديو اترسم بعد أول لقطة مثلًا)"""
    if not snap.get("post_content") and not snap.get("content_kind"):
        return False
    return _KIND_RANK.get(snap.get("content_kind") or "", 0) > _KIND_RANK.get(header.get("content_kind") or "", 0) \
        or not header.get("post_content")


SORT_SKIPPED = "sort-unavailable"
# مفيش زرار فرز، بس كل التعليقات ظاهرة (العدد = عداد فيسبوك)، فاترتّبت «الأحدث» بالوقت الكامل بإيدنا
COMPLETE_BY_TIME = "complete-by-time"


def unavailable_post(group_id: str, post_id: str, url: str, reason: str = "unavailable") -> dict:
    """منشور مالوش صف في الشيت: اتمسح/اتخفى، أو (SORT_SKIPPED) مفيش طريقة نضمن بيها ترتيب «الأحدث»."""
    return {
        "group_id": group_id, "post_id": post_id, "permalink": url, "url": url, "unavailable": True,
        "author": "", "author_id": "", "author_url": "", "text": "", "post_content": "", "content_kind": "",
        "media_url": "", "created_time": "", "created_time_raw": "", "comment_sort": reason,
        "comments_found": 0, "replies_found": 0, "comments_shortfall": False, "comments_over_max": False,
        "capture_mode": "incremental", "sheet_batches": 0, "comments": [],
    }


# تاب في نافذة مشتركة لازم يبقى قدام عشان يترسم. الـ worker في نافذة لوحده ظاهرة أصلًا، فمالوش لازمة
# وبيخطف التركيز من الـ workers التانيين
BRING_TO_FRONT = True
GOTO_ATTEMPTS = 3
# لقطات هيدر منشور من غير تعليقات، ثانية بين كل واحدة، لحد ما نوع المحتوى يبان
HEADER_ROUNDS = 8


def goto_with_retry(page, url: str) -> None:
    """فتح الصفحة قراءة بس فإعادة المحاولة آمنة. timeout واحد (60 ث) كان بيوقف جروب كامل."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    for attempt in range(1, GOTO_ATTEMPTS + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            return
        except PlaywrightTimeout:
            if attempt == GOTO_ATTEMPTS:
                raise
            print(f"الصفحة مافتحتش في 60 ثانية ({url}) — محاولة {attempt + 1}", file=sys.stderr)
            page.wait_for_timeout(5000)


def scrape_post(page, group_id: str, post_id: str, min_comments: int, max_comments: int, on_batch=None, batch_size: int = 80) -> dict:
    url = f"https://www.facebook.com/groups/{group_id}/posts/{post_id}/"
    goto_with_retry(page, url)
    if BRING_TO_FRONT:
        page.bring_to_front()
    # بدل 2.5 ث ثابتة: لحد ما المنشور يترسم (4 ث بالكتير = 2.5 هنا + 1.5 اللي كانت في أول الفرز)
    wait_until(page, lambda: post_ready(page), 4000)
    # قبل الفرز: التعليقات المخفية بتتعرض الأول، عشان العدّ والفرز يشوفوا كل التعليقات
    reveal_hidden_comments(page)
    sort_state = "missing"
    unsorted = False
    for _ in range(3):
        close_open_menu(page)
        try:
            sort_state = sort_comments_newest(page)
        except Exception as exc:
            sort_state = f"error:{exc}"
        if sort_state in SORT_OK:
            break
        page.wait_for_timeout(1200)
    if post_unavailable(page):
        # اتمسح أو اتخفى بعد ما شفناه في الموجز: مش فشل فرز، المنشور نفسه مش موجود
        return unavailable_post(group_id, post_id, url)
    if f"/posts/{post_id}" not in (page.url or ""):
        raise SystemExit(f"فيسبوك نقل الصفحة بعيد عن المنشور {post_id}: {page.url}")
    if sort_state not in SORT_OK:
        # آخر تأكيد قبل ما نوقف التشغيل كله: يمكن المنشور اتمسح والنص ماوصلش للفحص اللي في الصفحة
        try:
            gone = bool(UNAVAILABLE_RE.search(page.content()))
        except Exception:
            gone = False
        if gone:
            return unavailable_post(group_id, post_id, url)
        # مفيش زرار فرز خالص والمنشور فيه تعليقين ظاهرين أو أكتر (اتشاف 2026-10-22): بنسحب كل التعليقات، ولو
        # عددهم وصل لعداد فيسبوك تحت المنشور يبقى ماحدش مستخبي، فبنرتّبهم «الأحدث» بإيدنا بالوقت الكامل.
        # لو أقل من العداد (العرض الافتراضي خبّى حاجة) المنشور بيتعدّى من غير صف (قرار صاحب المشروع).
        # صفحة مااترسمتش (مفيش ولا تعليق ظاهر) لسه بتوقف: ده ممكن يبقى تسجيل خروج أو checkpoint.
        if sort_state == "missing" and distinct_comment_ids(page) >= 2:
            unsorted = True
        else:
            raise SystemExit(f"فرز التعليقات مش «الأحدث» على المنشور {post_id}: {sort_state}")
    # من غير فرز: لازم نشوف كل التعليقات قبل ما نرتّب ونقص لحد max_comments، وإلا ناخد أول N بترتيب غلط
    limit = NO_LIMIT if unsorted else max_comments
    stream = None if unsorted else on_batch
    seen: dict[str, dict] = {}
    order: list[str] = []
    pending: list[dict] = []
    batch_index = 0
    stale = 0
    header = {}
    # فيسبوك بيمسح التعليقات القديمة من الصفحة لما العدد يبقى بالآلاف،
    # فلازم نلتقط الظاهر قبل التمرير، ونفتح الردود، لحد ما مفيش جديد.
    # من غير تعليقات: لقطات الهيدر بس (ثانية بين كل واحدة) بدل 5 لفّات تمرير فاضية،
    # لحد ما النوع يبان. منشور فيه نص بيخلص من أول لقطة؛ الانتظار بيبقى بس لمنشور مالوش نص ولا ميديا لسه.
    # لقطة واحدة ماكانتش كفاية (D-36)، و 3 كمان لأ: فيديو من غير وصف اترسم بعد أكتر من 3 ثواني (D-41).
    no_comments = sort_state in NO_COMMENT_STATES
    for round_no in range(HEADER_ROUNDS if no_comments else 400):
        if len(order) >= limit:
            break
        if no_comments and round_no:
            page.wait_for_timeout(1000)
        snap = visible_snapshot(page, group_id, post_id)
        if richer_header(snap, header):
            header["post_content"] = snap["post_content"]
            header["content_kind"] = snap.get("content_kind") or ""
            header["media_url"] = snap.get("media_url") or ""
        if snap.get("text") and not header.get("text"):
            header["text"] = snap["text"]
        if snap.get("author"):
            header["author"] = snap.get("author") or header.get("author") or ""
            header["author_id"] = snap.get("author_id") or header.get("author_id") or ""
            header["author_url"] = snap.get("author_url") or header.get("author_url") or ""
        if snap.get("created_time"):
            header["created_time"] = snap.get("created_time") or header.get("created_time") or ""
        added = 0
        for raw in snap.get("comments") or []:
            comment = normalize_comment(raw, group_id, post_id)
            if not comment or comment["comment_id"] in seen:
                continue
            if len(order) >= limit:
                break
            seen[comment["comment_id"]] = comment
            order.append(comment["comment_id"])
            pending.append(comment)
            added += 1
        while stream and len(pending) >= batch_size:
            batch = pending[:batch_size]
            del pending[:batch_size]
            batch_index += 1
            stream(batch, batch_index, False)
        if no_comments:
            # أول لقطة بقت بدري (من غير الانتظار الثابت)، فالنوع لوحده مش كفاية: صاحب المنشور ووقته كمان
            if header.get("content_kind") and header.get("author") and header.get("created_time"):
                break
            continue
        expanded = expand_comments(page, rounds=1)
        if added:
            stale = 0
        else:
            stale += 1
        shown = comment_count(page)
        moved = scroll_comment_pane(page)
        # لحد ما تعليقات جديدة تترسم بعد التمرير (0.7 ث بالكتير زي الأول)
        wait_until(page, lambda: comment_count(page) > shown, 700)
        # آخر القايمة: مفيش «عرض المزيد» والتمرير ماتحركش — لفّتين كفاية مش 5
        # (5 لفّات فاضية أخدت ~150 ثانية على منشور واحد في تشغيل حقيقي)
        if stale >= 5 or (stale >= 2 and not expanded and not moved):
            break
    if unsorted:
        total = comment_total(page)
        if total is None or len(order) < total:
            skipped = unavailable_post(group_id, post_id, url, reason=SORT_SKIPPED)
            skipped.update(comments_expected=total, comments_seen=len(order))
            return skipped
        order = [c["comment_id"] for c in newest_first_threads([seen[cid] for cid in order])][:max_comments]
        sort_state = COMPLETE_BY_TIME
        pending = [seen[cid] for cid in order] if on_batch else []
        for i in range(0, len(pending), batch_size):
            batch_index += 1
            on_batch(pending[i:i + batch_size], batch_index, i + batch_size >= len(pending))
    elif on_batch and pending:
        batch_index += 1
        on_batch(pending, batch_index, True)
    comments = [seen[cid] for cid in order]
    replies = [c for c in comments if c["is_reply"]]
    return {
        "group_id": group_id,
        "post_id": post_id,
        "permalink": f"https://www.facebook.com/groups/{group_id}/posts/{post_id}/",
        "author": (header.get("author") or "").strip(),
        "author_id": header.get("author_id") or "",
        "author_url": header.get("author_url") or "",
        "text": (header.get("text") or "").strip(),
        "post_content": (header.get("post_content") or header.get("text") or "").strip(),
        "content_kind": header.get("content_kind") or "",
        "media_url": header.get("media_url") or "",
        "created_time": normalize_fb_time(header.get("created_time") or ""),
        "created_time_raw": (header.get("created_time") or "").strip(),
        "url": url,
        "comment_sort": sort_state,
        "comments_found": len(comments),
        "replies_found": len(replies),
        "comments_shortfall": len(comments) < min_comments,
        "comments_over_max": False,
        "capture_mode": "incremental",
        "sheet_batches": batch_index,
        "comments": comments,
    }


def archive_group(page, url: str, min_posts: int, max_posts: int, min_comments: int, max_comments: int, dry: bool = True, batch_size: int = 80, progress=None, sender: SheetSender | None = None) -> dict:
    group_id = group_key(url)
    post_ids, feed_sort = collect_post_ids(page, group_id, max_posts)
    # اسم الجروب من صفحة الموجز نفسها، قبل ما نفتح أي منشور
    name = ""
    try:
        name = clean_title(page.title())
    except Exception:
        name = ""
    posts = []
    unavailable: list[str] = []
    sort_skipped: list[str] = []

    def result() -> dict:
        out = _group_result(group_id, name, feed_sort, min_posts, max_posts, min_comments, max_comments, posts)
        out["posts_unavailable"] = list(unavailable)
        out["posts_sort_skipped"] = list(sort_skipped)
        return out

    for post_id in post_ids:
        def on_batch(comments, batch_index, done):
            payload = {
                "token": TOKEN,
                "group_id": group_id,
                "posts": [{
                    "post_id": post_id,
                    "author": "",
                    "text": "",
                    "created_time": "",
                    "url": f"https://www.facebook.com/groups/{group_id}/posts/{post_id}/",
                    "permalink": f"https://www.facebook.com/groups/{group_id}/posts/{post_id}/",
                    "comments": [sheet_comment(c) for c in comments],
                }],
            }
            answer = sheet_post(payload)
            print(f"sheet batch {batch_index} n={len(comments)} done={done}: {answer[:220]}")

        post = scrape_post(
            page,
            group_id,
            post_id,
            min_comments,
            max_comments,
            on_batch=None,
            batch_size=batch_size,
        )
        if post.get("unavailable"):
            # مالوش صف في الشيت، بس بيتسجّل في الدَمب
            if post["comment_sort"] == SORT_SKIPPED:
                print(f"{group_id}/{post_id}: {SORT_SKIPPED} (مفيش زرار فرز و2+ تعليق) — اتعدّى", flush=True)
                sort_skipped.append(post_id)
            else:
                print(f"{group_id}/{post_id}: unavailable (اتمسح أو اتخفى) — اتعدّى")
                unavailable.append(post_id)
            continue
        print(
            f"{group_id}/{post_id}: comments={post['comments_found']} "
            f"replies={post['replies_found']} sort={post['comment_sort']}"
        )
        if not dry:
            comments = post.get("comments") or []
            chunks = [comments[i:i + batch_size] for i in range(0, len(comments), batch_size)] or [[]]
            for index, chunk in enumerate(chunks, 1):
                payload = sheet_post_payload(group_id, post, chunk)
                if sender:
                    sender.put(payload)
                else:
                    answer = sheet_post(payload)
                    print(f"sheet batch {index}/{len(chunks)} n={len(chunk)}: {answer[:240]}")
                post["sheet_batches"] = index
        posts.append(post)
        # بعد كل منشور: لو اللي بعده وقع، اللي خلص يفضل محفوظ (قبل كده كان بيضيع كله)
        if progress:
            progress(result())
        if sender:
            sender.raise_if_failed()
    return result()


def _group_result(group_id, name, feed_sort, min_posts, max_posts, min_comments, max_comments, posts) -> dict:
    return {
        "token": TOKEN,
        "group_id": group_id,
        "group_url": f"https://www.facebook.com/groups/{group_id}",
        "group_name": name,
        "sort_posts": "CHRONOLOGICAL",
        "feed_sort_label": feed_sort,
        "sort_comments": "الأحدث",
        "limits": {
            "min_posts": min_posts,
            "max_posts": max_posts,
            "min_comments": min_comments,
            "max_comments": max_comments,
        },
        "posts_found": len(posts),
        "posts_shortfall": len(posts) < min_posts,
        "posts": posts,
    }


GROUPS_FILE = Path(__file__).resolve().parent / "groups.txt"


def read_groups_file(path: Path) -> list[str]:
    """ملف الجروبات: سطر لكل جروب — رابط أو id بس. السطور الفاضية و # بتتجاهل، و «- » في أول السطر
    (قايمة markdown) مقبولة، والمكرر بيتشال. أي سطر مش جروب بيوقف التشغيل برقم السطر بدل ما يتنسي."""
    if not path.exists():
        raise SystemExit(f"مفيش --group ومفيش ملف جروبات: {path} — اعمله زي groups.example.txt")
    urls: list[str] = []
    seen: set[str] = set()
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = re.sub(r"^\s*[-*]\s+", "", raw).split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        if line.isdigit():
            line = f"https://www.facebook.com/groups/{line}/"
        match = GROUP_RE.search(line)
        if not match:
            raise SystemExit(f"{path.name} سطر {number}: مش رابط جروب فيسبوك: {raw.strip()}")
        if match.group(1) in seen:
            continue
        seen.add(match.group(1))
        urls.append(line)
    if not urls:
        raise SystemExit(f"ملف الجروبات فاضي: {path}")
    return urls


NO_LIMIT = 10 ** 9
# حد صاحب المشروع (2026-10-22): أكتر من 4 نوافذ بتسحب في نفس الوقت = خطر حظر الحساب من فيسبوك
MAX_WORKERS = 4


def comment_limit(value: str) -> int:
    """رقم، أو all = كل التعليقات من غير حد."""
    if value.strip().lower() == "all":
        return NO_LIMIT
    try:
        return int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"رقم أو all: {value}") from None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="أرشفة جروبات فيسبوك إلى Google Sheet")
    parser.add_argument("--group", action="append",
                        help="رابط جروب. كرره لأكتر من جروب. من غيره: الجروبات من groups.txt")
    parser.add_argument("--groups-file", default=None,
                        help="ملف الجروبات (سطر لكل جروب). الافتراضي groups.txt في فولدر المشروع")
    parser.add_argument("--min-posts", type=int, required=True)
    parser.add_argument("--max-posts", type=int, required=True)
    parser.add_argument("--min-comments", type=int, required=True)
    parser.add_argument("--max-comments", type=comment_limit, required=True, help="رقم، أو all لكل التعليقات")
    parser.add_argument("--cdp", default=CDP)
    parser.add_argument("--dry", action="store_true", help="احفظ JSON بس، من غير إرسال للشيت")
    parser.add_argument("--batch-size", type=int, default=200, help="أقصى عدد تعليقات في طلب واحد للشيت")
    parser.add_argument("--workers", type=int, default=1,
                        help="كام جروب في نفس الوقت، كل واحد في نافذة لوحده (عملية بايثون لكل جروب)")
    parser.add_argument("--debug", action="store_true",
                        help="سجّل الكونسول والأخطاء والشبكة، واحفظ HTML وصورة في debug/ لو حصل خطأ")
    parser.add_argument("--trace", action="store_true",
                        help="مع --debug: سجّل كمان trace.zip و network.har (أتقل، بس بيوريك كل خطوة)")
    # داخلي: الـ workers بيشغّلوا السكربت ده لجروب واحد بنفس ختم التشغيل، في نافذة جديدة
    parser.add_argument("--run-stamp", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--new-window", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.min_posts < 1 or args.max_posts < args.min_posts:
        raise SystemExit("min-posts لازم يكون 1 أو أكتر، وmax-posts أكبر منه أو يساويه")
    if args.min_comments < 0 or args.max_comments < args.min_comments:
        raise SystemExit("min-comments مش سالب، وmax-comments أكبر منه أو يساويه")
    if args.batch_size < 1:
        raise SystemExit("batch-size لازم يكون 1 أو أكتر")
    if not 1 <= args.workers <= MAX_WORKERS:
        raise SystemExit(f"workers من 1 لـ {MAX_WORKERS}: أكتر من كده الحساب ممكن يتحظر من فيسبوك (قرار صاحب المشروع)")
    if args.workers > 1 and args.debug:
        raise SystemExit("--debug مع جروب واحد بس (--workers 1): التشخيص لإعادة عطل محدد، مش للتشغيل الكبير")
    if not args.group:
        groups_file = Path(args.groups_file) if args.groups_file else GROUPS_FILE
        args.group = read_groups_file(groups_file)
        print(f"{len(args.group)} جروب من {groups_file}", file=sys.stderr)
    return args


def utf8_console() -> None:
    """الطباعة بالعربي بتوقع السكربت لو الـ stdout متحوّل لملف أو Task Scheduler (cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def new_window_page(browser, context):
    """صفحة في نافذة لوحدها. التابات اللي ورا بعض في نفس النافذة مخفية، والصفحة المخفية مابتترسمش،
    فكل worker بياخد نافذة ظاهرة بدل تاب."""
    cdp = browser.new_browser_cdp_session()
    try:
        target = cdp.send("Target.createTarget", {"url": "about:blank", "newWindow": True})["targetId"]
    finally:
        cdp.detach()
    # بالـ targetId مش «أول صفحة جديدة»: الـ workers بيفتحوا نوافذ في نفس اللحظة، وكل واحد كان بياخد نافذة التاني
    deadline = time.time() + 30
    while time.time() < deadline:
        for page in context.pages:
            session = context.new_cdp_session(page)
            try:
                if session.send("Target.getTargetInfo")["targetInfo"]["targetId"] == target:
                    return page
            finally:
                session.detach()
        time.sleep(0.2)
    raise SystemExit(f"النافذة الجديدة ماظهرتش في المتصفح: {target}")


def child_args(args: argparse.Namespace, url: str, stamp: str) -> list[str]:
    cmd = [sys.executable, "-u", str(Path(__file__).resolve()), "--group", url,
           "--min-posts", str(args.min_posts), "--max-posts", str(args.max_posts),
           "--min-comments", str(args.min_comments), "--max-comments", str(args.max_comments),
           "--cdp", args.cdp, "--batch-size", str(args.batch_size), "--run-stamp", stamp, "--new-window"]
    return cmd + (["--dry"] if args.dry else [])


def run_parallel(args: argparse.Namespace, stamp: str) -> int:
    """كل جروب في عملية بايثون لوحده، args.workers في نفس الوقت. الجروب اللي يخلص بياخد مكانه
    اللي بعده، فالجروبات الكبيرة مابتعطلش الباقي. جروب يقف (فرز مش «الأحدث» مثلًا) بيقف لوحده
    وبيتقال صريح، والتشغيل كله بيخلص بـ exit 1."""
    from concurrent.futures import ThreadPoolExecutor

    lock = threading.Lock()
    total = len(args.group)
    started = time.time()

    def say(text: str) -> None:
        with lock:
            print(f"+{time.time() - started:7.1f} {text}", flush=True)

    def one(index_url: tuple[int, str]) -> dict:
        index, url = index_url
        key = group_key(url)
        say(f"[{index}/{total}] {key}: start")
        t = time.time()
        proc = subprocess.Popen(child_args(args, url, stamp), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
        last = ""
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                last = line
                say(f"[{key}] {line}")
        code = proc.wait()
        sec = round(time.time() - t, 1)
        say(f"[{index}/{total}] {key}: {'ok' if code == 0 else f'FAILED exit {code}'} in {sec} s")
        return {"group": key, "url": url, "exit": code, "seconds": sec, "last_line": "" if code == 0 else last}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        outcomes = list(pool.map(one, enumerate(args.group, 1)))
    parts = []
    for outcome in outcomes:
        part = DUMP_DIR / f"run-{stamp}-{safe_name(outcome['group'])}.json"
        if part.exists():
            try:
                outcome["result"] = json.loads(part.read_text(encoding="utf-8"))
                parts.append(part)
            except ValueError:
                pass
    summary = DUMP_DIR / f"run-{stamp}.json"
    summary.write_text(json.dumps({"workers": args.workers, "seconds": round(time.time() - started, 1),
                                   "groups": outcomes}, ensure_ascii=False, indent=2), encoding="utf-8")
    for part in parts:
        part.unlink()
    failed = [o for o in outcomes if o["exit"] != 0]
    print(f"done {summary}: {total - len(failed)}/{total} groups ok in {round(time.time() - started, 1)} s")
    for o in failed:
        print(f"  FAILED {o['group']}: {o['last_line']}", file=sys.stderr)
    return 1 if failed else 0


def safe_name(text: str) -> str:
    return re.sub(r"[^\w.-]+", "_", text)


def main() -> None:
    utf8_console()
    args = parse_args()
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = args.run_stamp or time.strftime("%Y%m%d-%H%M%S")
    if args.workers > 1 and len(args.group) > 1:
        raise SystemExit(run_parallel(args, stamp))
    from playwright.sync_api import sync_playwright

    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(args.cdp)
        context = browser.contexts[0]
        global BRING_TO_FRONT
        if args.new_window:
            page = new_window_page(browser, context)
            BRING_TO_FRONT = False
        else:
            page = context.new_page()
        recorder = None
        if args.debug:
            from browser_debug import PageRecorder

            recorder = PageRecorder(page, label="scrape", trace=args.trace, har=args.trace)
        try:
            for url in args.group:
                if recorder:
                    recorder.mark(f"group {url}")
                def save(payload: dict) -> Path:
                    out = DUMP_DIR / f"{payload['group_id']}-{stamp}.json"
                    out.write_text(json.dumps(without_token(payload), ensure_ascii=False, indent=2), encoding="utf-8")
                    return out

                # الإرسال في الخلفية؛ في آخر كل جروب بنستنى لحد ما يخلص، حتى لو الجروب وقف في النص
                sender = None if args.dry else SheetSender(args.batch_size)
                try:
                    payload = archive_group(
                        page,
                        url,
                        args.min_posts,
                        args.max_posts,
                        args.min_comments,
                        args.max_comments,
                        dry=args.dry,
                        batch_size=args.batch_size,
                        progress=save,
                        sender=sender,
                    )
                except BaseException:
                    if sender:
                        sender.close(check=False)
                    raise
                if sender:
                    sender.close()
                results.append(without_token(payload))
                print("saved", save(payload))
                # جروب من غير منشورات (خاص، فاضي): مفيش حاجة تتبعت
                if not args.dry and payload["posts"] and not any(p.get("sheet_batches") for p in payload["posts"]):
                    answer = sheet_post(payload)
                    print("sheet:", answer[:400])
            # من غير ده، --trace في تشغيل ناجح بيرمي التتبّع ويسيب الـ HAR لوحده
            if recorder and args.trace:
                recorder.dump("complete")
        except BaseException as exc:
            # SystemExit كمان: فشل فرز التعليقات بيرفعه، وده بالظبط اللي عايزين نشوف صفحته
            results.append({"failed": f"{type(exc).__name__}: {exc}"})
            if recorder:
                recorder.dump("failed", exc=exc)
            raise
        finally:
            if recorder:
                recorder.detach()
            page.close()
            # الملخص بيتكتب حتى لو التشغيل وقع، بالجروبات اللي خلصت + سبب الوقوع.
            # worker لجروب واحد بيكتب ملخصه باسم الجروب، والأب بيجمعهم في run-<stamp>.json
            summary = DUMP_DIR / (f"run-{stamp}-{safe_name(group_key(args.group[0]))}.json"
                                  if args.run_stamp else f"run-{stamp}.json")
            summary.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            print("done", summary)


if __name__ == "__main__":
    main()
