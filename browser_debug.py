r"""
أدوات تشخيص المتصفح: تلتقط كل حاجة محتاجها لتصليح أي خطأ أثناء التشغيل.

بتجمع لكل لقطة:
  page.html          شكل الصفحة الكامل وقت الخطأ
  page.png           صورة الصفحة (full page، وبترجع للـ viewport لو فشلت)
  console.jsonl      كل رسائل الكونسول (log/info/warn/error) بمكانها في الكود
  errors.jsonl       الاستثناءات غير الملتقطة في الصفحة (pageerror / exceptionThrown)
  network.jsonl      الطلبات الفاشلة والردود 4xx/5xx
  browser-log.jsonl  سجل المتصفح من CDP (Log.entryAdded): شبكة، أمان، CORS، CSP
  meta.json          الرابط، العنوان، الـ viewport، الكوكيز، الوقت، ملخص العدّادات
  storage.json       localStorage و sessionStorage
  selectors.json     حالة كل selector المشروع بيعتمد عليه (كام عنصر لقى)
  trace.zip          تتبّع Playwright كامل — بـ --trace (شوفه بـ playwright show-trace)
  network.har        كل الشبكة بصيغة HAR — بـ --har
  perf.json          عدّادات الأداء من CDP (Performance.getMetrics)

---

استعمال كمكتبة (الحالة الأهم: دَمب تلقائي عند أي خطأ):

    from browser_debug import PageRecorder

    rec = PageRecorder(page, label="scrape")
    try:
        ...                      # الكود بتاعك
    except Exception:
        rec.dump("failed")       # يحفظ كل حاجة
        raise
    finally:
        rec.detach()

أو أقصر، بيعمل دَمب لوحده لو حصل استثناء:

    from browser_debug import recording

    with recording(page, "post-12345") as rec:
        page.goto(url)
        rec.mark("after-goto")   # لقطة وسط الشغل، اختيارية

---

استعمال من سطر الأوامر (المتصفح لازم يكون شغال على CDP):

    .venv\Scripts\python.exe browser_debug.py snapshot
        يصوّر كل التابّات المفتوحة حالًا

    .venv\Scripts\python.exe browser_debug.py snapshot --url https://www.facebook.com/groups/123
        يفتح تاب، يسجّل التحميل من أوله، يصوّر، ويقفل

    .venv\Scripts\python.exe browser_debug.py watch
        يفضل يطبع الكونسول والأخطاء والطلبات الفاشلة وإنت بتتصرف في المتصفح بإيدك،
        وعند Ctrl+C يحفظ كل اللقطات

    .venv\Scripts\python.exe browser_debug.py selectors --url https://www.facebook.com/groups/123/posts/456/
        **أهم أمر لما حاجة تبوظ.** بيختبر كل selector المشروع معتمد عليه على صفحة
        حقيقية ويقول كل واحد لقى كام عنصر. فيسبوك بيغيّر الـ DOM، فالصفر هنا
        بيشاور على السطر اللي محتاج تعديل على طول.

    أضف --trace أو --har لأي أمر فوق:
        --trace   يحفظ trace.zip. شوفه بـ:
                  .venv\Scripts\python.exe -m playwright show-trace debug\...\trace.zip
        --har     يحفظ network.har (كل الطلبات والردود بالتفصيل)

المخرجات في:  debug\<الوقت>-<الاسم>\  جوه فولدر المشروع (أو FB_DEBUG_DIR)
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEBUG_DIR = Path(os.environ.get("FB_DEBUG_DIR", ROOT / "debug"))
CDP = os.environ.get("FB_CDP", "http://127.0.0.1:9333")

HAR_CONTENT = os.environ.get("FB_DEBUG_HAR_CONTENT", "omit")

# الكونسول بيطلع رسائل بالآلاف على فيسبوك. نحد الحجم بدل ما الذاكرة تتفجّر.
MAX_EVENTS = int(os.environ.get("FB_DEBUG_MAX_EVENTS", "5000"))

# كل selector المشروع بيعتمد عليه، والسطر اللي بيستخدمه.
# لما فيسبوك يغيّر الـ DOM، العدد بيبقى صفر وده بيحدد مكان الإصلاح على طول.
#
# `tier` بيقول الصفر معناه إيه:
#   critical  صفر = السحب أو الرد واقف. لازم إصلاح.
#   degraded  صفر = ماشي بس ناقص (في fallback). يستاهل نظرة.
#   context   صفر طبيعي حسب الصفحة أو حالتها. مش مشكلة لوحده.
#
# `zero_ok` بيشرح إمتى الصفر طبيعي، عشان التقرير مايخضّش من غير داعي.
PROBES: list[dict] = [
    # --- التعليقات: لب السحب كله ---
    {"name": "comment.article", "kind": "css", "selector": "div[role='article'][aria-label]",
     "used_by": "visible_snapshot", "expected": "كل تعليق = article له aria-label",
     "tier": "critical", "page": "post"},
    {"name": "comment.permalink", "kind": "css", "selector": "a[href*='comment_id=']",
     "used_by": "visible_snapshot / normalize_comment / post_one",
     "expected": "رابط التعليق — منه بنطلع comment_id", "tier": "critical", "page": "post"},
    {"name": "reply.textbox", "kind": "css", "selector": "[role='textbox']",
     "used_by": "post_one", "expected": "صندوق كتابة الرد", "tier": "critical", "page": "post"},
    {"name": "comment.author", "kind": "css", "selector": "a[href*='/user/']",
     "used_by": "visible_snapshot", "expected": "رابط صاحب التعليق",
     "tier": "degraded", "page": "post", "zero_ok": "ممكن التعليقات كلها من صفحات مش حسابات"},
    {"name": "text.dir_auto", "kind": "css", "selector": "div[dir='auto']",
     "used_by": "visible_snapshot", "expected": "عقد النص", "tier": "critical", "page": "post"},

    # --- هيكل الصفحة ---
    {"name": "post.story", "kind": "css", "selector": "[role='article']:not([aria-label])",
     "used_by": "visible_snapshot (postRoot)",
     "expected": "article المنشور نفسه (التعليقات ليها aria-label، هو لأ)", "tier": "critical", "page": "post"},
    {"name": "post.own_time_link", "kind": "css", "selector": "a[href*='multi_permalinks=']",
     "used_by": "visible_snapshot", "expected": "رابط وقت المنشور", "tier": "degraded", "page": "post",
     "zero_ok": "created_time بيفضل فاضي بس السحب ماشي"},
    {"name": "post.dialog_title", "kind": "css", "selector": "h2",
     "used_by": "visible_snapshot", "expected": "«منشور فلان» — fallback لصاحب المنشور",
     "tier": "context", "page": "post", "zero_ok": "المنشور مفتوح كصفحة مش نافذة، أو صاحبه لقيناه من رابطه"},
    {"name": "post.photo", "kind": "css", "selector": "a[href*='/photo/'], a[href*='/photos/']",
     "used_by": "visible_snapshot", "expected": "صورة المنشور", "tier": "context", "page": "post",
     "zero_ok": "المنشور مفيهوش صورة"},
    {"name": "post.shared_link", "kind": "css",
     "selector": "a[href*='/posts/'], a[href*='permalink.php'], a[href*='story_fbid=']",
     "used_by": "visible_snapshot", "expected": "رابط المنشور الأصلي لو المنشور متشارك",
     "tier": "context", "page": "post", "zero_ok": "المنشور مش متشارك"},
    {"name": "post.shared_text", "kind": "css", "selector": "div[dir='auto'], span[dir='auto']",
     "used_by": "visible_snapshot", "expected": "نص المنشور المتشارك (مفيهوش data-ad-preview)",
     "tier": "context", "page": "post", "zero_ok": "مفيش نص"},
    {"name": "comment.own_link", "kind": "css", "selector": "div[role='article'][aria-label] a[href*='comment_id=']",
     "used_by": "distinct_comment_ids / comments_newest_first", "expected": "رابط وقت كل تعليق (منه الـ id والوقت الكامل)",
     "tier": "context", "page": "post", "zero_ok": "مفيش تعليقات"},
    {"name": "comment.photo_link", "kind": "css", "selector": "a[href*='/photo/']",
     "used_by": "visible_snapshot", "expected": "صورة في تعليق", "tier": "context", "page": "post",
     "zero_ok": "مفيش صور"},
    {"name": "comment.media", "kind": "css", "selector": "img, video",
     "used_by": "visible_snapshot", "expected": "صورة/ستيكر/فيديو في تعليق", "tier": "context", "page": "post",
     "zero_ok": "مفيش ميديا"},
    {"name": "comments.off_text", "kind": "text",
     "selector": r"تم إيقاف تشغيل التعليقات|تم إيقاف التعليقات|Commenting has been turned off|turned off commenting|Comments are turned off|comments have been turned off",
     "used_by": "comments_off_shown / sort_comments_newest", "expected": "صاحب المنشور قفل التعليقات",
     "tier": "context", "page": "post", "zero_ok": "التعليقات مفتوحة"},
    {"name": "post.unavailable_text", "kind": "text",
     "selector": r"هذا المحتوى غير متوفر حاليًا|هذا المحتوى غير متاح|This content isn.t available",
     "used_by": "post_unavailable / scrape_post", "expected": "المنشور اتمسح أو اتخفى",
     "tier": "context", "page": "post", "zero_ok": "المنشور موجود"},
    {"name": "group.join_button", "kind": "role", "role": "button", "selector": r"انضمام إلى المجموعة|Join group",
     "used_by": "group_is_private_for_us", "expected": "الحساب مش عضو (جروب خاص = مفيش موجز)",
     "tier": "context", "page": "feed", "zero_ok": "الحساب عضو"},
    {"name": "feed.container", "kind": "css", "selector": "[role='feed']",
     "used_by": "group_is_private_for_us", "expected": "موجز الجروب", "tier": "degraded", "page": "feed",
     "zero_ok": "جروب خاص والحساب مش عضو"},
    {"name": "post.video_link", "kind": "css", "selector": "a[href*='/reel/'], a[href*='/videos/'], a[href*='/watch/']",
     "used_by": "visible_snapshot", "expected": "رابط الفيديو/الريل", "tier": "context", "page": "post",
     "zero_ok": "المنشور مش فيديو"},
    {"name": "comments.none_text", "kind": "text",
     "selector": r"لا توجد تعليقات حتى الآن|كن أول من يعلّق|كن أول من يعلق|No comments yet|Be the first to comment",
     "used_by": "no_comments_shown / sort_comments_newest",
     "expected": "إثبات إن المنشور مفيهوش تعليقات (فمفيش زرار فرز)", "tier": "context", "page": "post",
     "zero_ok": "المنشور فيه تعليقات"},
    {"name": "menu.open", "kind": "css", "selector": "[role='menu']",
     "used_by": "close_open_menu", "expected": "قائمة مفتوحة (Escape بيقفلها)", "tier": "context", "page": "any",
     "zero_ok": "مفيش قائمة مفتوحة — طبيعي"},
    {"name": "post.dialog", "kind": "css", "selector": "div[role='dialog']",
     "used_by": "visible_snapshot / expand_comments / scroll_comment_pane",
     "expected": "نافذة المنشور", "tier": "context", "page": "post",
     "zero_ok": "المنشور مفتوح كصفحة كاملة مش نافذة — الكود بيرجع لـ role=main"},
    {"name": "post.main", "kind": "css", "selector": "div[role='main']",
     "used_by": "visible_snapshot", "expected": "عمود المحتوى", "tier": "critical", "page": "any"},
    {"name": "post.message", "kind": "css",
     "selector": "div[data-ad-preview='message'], div[data-ad-comet-preview='message']",
     "used_by": "visible_snapshot", "expected": "نص المنشور", "tier": "degraded", "page": "post",
     "zero_ok": "في fallback على document.title، فالنص بييجي بس ناقص"},
    {"name": "post.external_link", "kind": "css", "selector": "a[href*='l.facebook.com/l.php']",
     "used_by": "visible_snapshot", "expected": "رابط خارجي", "tier": "context", "page": "post",
     "zero_ok": "المنشور مفيهوش رابط خارجي"},
    {"name": "post.video", "kind": "css", "selector": "video",
     "used_by": "visible_snapshot", "expected": "فيديو المنشور", "tier": "context", "page": "post",
     "zero_ok": "المنشور نص أو صورة"},
    {"name": "any.button", "kind": "css", "selector": "[role='button']",
     "used_by": "expand_comments / sort_comments_newest / post_one", "expected": "أي زرار",
     "tier": "critical", "page": "any"},

    # --- الموجز ---
    {"name": "feed.post_links", "kind": "css", "selector": "a[href*='/posts/'], a[href*='/permalink/']",
     "used_by": "collect_post_ids", "expected": "روابط المنشورات في الموجز",
     "tier": "critical", "page": "feed"},
    {"name": "feed.sort_label", "kind": "role", "role": "button", "selector": r"فرز موجز المجموعة",
     "used_by": "collect_post_ids", "expected": "زرار فرز موجز الجروب",
     "tier": "context", "page": "feed", "zero_ok": "إحنا على صفحة منشور مش على الموجز"},

    # --- الفرز: أكتر حاجة بتبوظ ---
    {"name": "sort.state_newest", "kind": "role", "role": "button", "selector": r"الأحدث|Newest",
     "used_by": "sort_comments_newest", "expected": "إثبات إن الفرز بقى «الأحدث»",
     "tier": "context", "page": "post",
     "zero_ok": "طبيعي قبل ما sort_comments_newest تشتغل — الصفحة بتفتح على «الأكثر ملاءمة»"},
    {"name": "sort.opener", "kind": "role", "role": "button",
     "selector": r"الأكثر ملاءمة|كل التعليقات|Most relevant|All comments",
     "used_by": "sort_comments_newest", "expected": "الزرار اللي يفتح قائمة الفرز",
     "tier": "context", "page": "post",
     "zero_ok": "طبيعي لو الفرز بقى «الأحدث» خلاص — بص على sort.state_newest"},
    {"name": "sort.menuitem_newest", "kind": "role", "role": "menuitem",
     "selector": r"الأحدث|Newest|التعليقات الأحدث",
     "used_by": "sort_comments_newest", "expected": "خيار «الأحدث» جوه القائمة",
     "tier": "context", "page": "post", "zero_ok": "القائمة مقفولة — بيظهر بعد الضغط على sort.opener"},

    # --- الردود والتوسيع ---
    {"name": "reply.button", "kind": "role", "role": "button", "selector": r"^(رد|Reply)$",
     "used_by": "post_one", "expected": "زرار «رد» على تعليق", "tier": "critical", "page": "post"},
    {"name": "reply.composer", "kind": "css",
     "selector": "[role='textbox'][aria-label*='ردًا'], [role='textbox'][aria-label*='reply' i]",
     "used_by": "post_one", "expected": "صندوق الرد «اكتب ردًا عامًا...» (فيسبوك ساعات بيرسمه قبل الضغط على «رد»)",
     "tier": "context", "page": "post", "zero_ok": "بيظهر بعد الضغط على «رد»"},
    {"name": "comment.public_composer", "kind": "css",
     "selector": "[role='textbox'][aria-label*='تعليقًا عامًا'], [role='textbox'][aria-label*='public comment' i]",
     "used_by": "post_one (مستبعد)", "expected": "صندوق «اكتب تعليقًا عامًا» — الرد مايتكتبش فيه أبدًا",
     "tier": "context", "page": "post", "zero_ok": "المنشور مقفول التعليقات أو الصندوق لسه مارسمش"},
    {"name": "reply.any_article", "kind": "css", "selector": "[role='article']",
     "used_by": "post_one (reply_present / expand_thread)", "expected": "كل article (منشور + تعليقات + ردود) — لدوّارة الردود",
     "tier": "degraded", "page": "post"},
    {"name": "reply.own_author", "kind": "css", "selector": "a[href*='/user/'], a[href*='profile.php?id=']",
     "used_by": "reply_present", "expected": "صاحب الرد — للتأكد إن الرد اللي ظهر بتاع حسابنا",
     "tier": "degraded", "page": "post", "zero_ok": "ممكن الصفحة مافيهاش روابط حسابات"},
    {"name": "reply.thread_expander", "kind": "role", "role": "button", "selector": r"(عرض|View).*(رد|repl)",
     "used_by": "expand_thread", "expected": "«عرض N رد» تحت التعليق — للتأكد إن ردنا مش موجود قبل كده",
     "tier": "context", "page": "post", "zero_ok": "التعليق مالوش ردود أو ردوده مفتوحة"},
    {"name": "expand.more_comments", "kind": "role", "role": "button",
     "selector": r"عرض المزيد من التعليقات|View more comments",
     "used_by": "expand_comments", "expected": "عرض المزيد من التعليقات",
     "tier": "context", "page": "post", "zero_ok": "كل التعليقات ظاهرة خلاص"},
    {"name": "expand.previous", "kind": "role", "role": "button",
     "selector": r"عرض التعليقات السابقة|View previous comments",
     "used_by": "expand_comments", "expected": "عرض التعليقات السابقة",
     "tier": "context", "page": "post", "zero_ok": "مفيش تعليقات أقدم"},
    {"name": "expand.replies", "kind": "role", "role": "button",
     "selector": r"عرض.*(الردود|رد)|View.*repl",
     "used_by": "expand_comments", "expected": "فتح شجرة الردود",
     "tier": "context", "page": "post", "zero_ok": "مفيش ردود مخفية"},
]


# أصفار طبيعية على منشور مفيهوش تعليقات (comments.none_text ظاهر)
NO_COMMENT_ZEROS = {"comment.article", "comment.permalink", "reply.button", "text.dir_auto"}

# رسايل فيسبوك نفسه اللي بتظهر في كل صفحة ومالهاش علاقة بالسكربت (اتشافت في تشغيل حقيقي 2026-10-21).
# بتتعدّ في summary.txt بس؛ الملفات الخام بتفضل فيها كل حاجة.
KNOWN_NOISE = [
    r"Error with Permissions-Policy header: Unrecognized feature",
    r"Permissions policy violation: unload is not allowed",
    r"Minified React error #418",                       # hydration mismatch جوه فيسبوك
    r"Multiple readback operations using getImageData",
    r"ERR_(ABORTED|CONNECTION_CLOSED) .*fbcdn\.net",    # ستريم فيديو اتلغى لما الصفحة اتنقلت
    r"selfxss|^%c(انتبه!|Stop!)",                       # تحذير فيسبوك للمطورين في كل صفحة
    r"chrome-extension://invalid",                      # فيسبوك بيدوّر على إضافات المتصفح
]
_NOISE_RE = None


def is_known_noise(text) -> bool:
    global _NOISE_RE
    if _NOISE_RE is None:
        import re as _re
        _NOISE_RE = _re.compile("|".join(KNOWN_NOISE))
    return bool(text) and bool(_NOISE_RE.search(str(text)))


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _safe(name: str) -> str:
    keep = "-_."
    cleaned = "".join(c if (c.isalnum() or c in keep) else "-" for c in name)
    return cleaned.strip("-") or "snap"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def probe_selectors(page, page_kind: str = "auto") -> dict:
    """يختبر كل selector المشروع معتمد عليه ويرجّع كام عنصر لقى. مابيرفعش استثناء.

    page_kind: post | feed | any | auto (بيستنتج من الرابط)
    """
    import re as _re

    url = ""
    with contextlib.suppress(Exception):
        url = page.url or ""
    if page_kind == "auto":
        if _re.search(r"/(posts|permalink)/\d+", url):
            page_kind = "post"
        elif "/groups/" in url:
            page_kind = "feed"
        else:
            page_kind = "any"

    results: list[dict] = []
    for probe in PROBES:
        row: dict[str, Any] = {k: probe.get(k) for k in
                               ("name", "kind", "selector", "used_by", "expected", "tier", "page", "zero_ok")}
        # probe مش بتاع نوع الصفحة الحالية = الصفر فيه متوقع
        row["applies"] = probe.get("page") in ("any", page_kind)
        try:
            if probe["kind"] == "css":
                row["count"] = page.locator(probe["selector"]).count()
            elif probe["kind"] == "text":
                row["count"] = page.get_by_text(_re.compile(probe["selector"])).count()
            else:
                row["count"] = page.get_by_role(
                    probe["role"], name=_re.compile(probe["selector"])).count()
        except Exception as exc:
            row["count"] = -1
            row["error"] = str(exc)[:200]
        results.append(row)

    def zeros(tier: str) -> list[str]:
        return [r["name"] for r in results
                if r["count"] == 0 and r["tier"] == tier and r["applies"]]

    broken = zeros("critical")
    degraded = zeros("degraded")

    # منشور مفيهوش تعليقات: فيسبوك كاتب «لا توجد تعليقات حتى الآن»، فأصفار التعليقات والرد والفرز طبيعية
    none_text = next((r for r in results if r["name"] == "comments.none_text"), None)
    no_comments = bool(none_text and none_text["count"] > 0 and page_kind == "post")
    if no_comments:
        broken = [n for n in broken if n not in NO_COMMENT_ZEROS]

    # قاعدة مشتقّة: الفرز بيبوظ بس لو الاتنين صفر —
    # واحد منهم صفر معناه إننا على حالة أو التانية، وده طبيعي
    state = next((r for r in results if r["name"] == "sort.state_newest"), None)
    opener = next((r for r in results if r["name"] == "sort.opener"), None)
    sort_broken = bool(state and opener and page_kind == "post" and not no_comments
                       and state["count"] == 0 and opener["count"] == 0)
    if sort_broken:
        broken.append("sort.* (لا حالة ولا زرار فتح)")

    return {
        "url": url,
        "page_kind": page_kind,
        "probes": results,
        "critical_zero": broken,
        "degraded_zero": degraded,
        "sort_broken": sort_broken,
        "no_comments": no_comments,
        "errors": [r["name"] for r in results if r["count"] < 0],
        # متوافق مع اللي قبله
        "zero_match": broken,
        "zero_count": len(broken),
    }


def format_selector_report(report: dict) -> str:
    mark = {"critical": "!!", "degraded": " ~", "context": "  "}
    lines = [
        f"نوع الصفحة: {report['page_kind']}   {report['url'][:90]}",
        "",
        f"{'':2} {'selector':<23} {'عدد':>5} {'نوعه':<9} يستخدمه",
        "-" * 92,
    ]
    for row in report["probes"]:
        count = row["count"]
        if count < 0:
            flag = "??"
        elif count == 0 and row["applies"]:
            flag = mark.get(row["tier"], "  ")
        else:
            flag = "  "
        skip = "" if row["applies"] else "  (مش بتاع الصفحة دي)"
        lines.append(f"{flag} {row['name']:<23} {count:>5} {row['tier']:<9} {row['used_by']}{skip}")

    lines.append("")
    if report["critical_zero"]:
        lines.append("مكسور — ده بيوقف الشغل:")
        for name in report["critical_zero"]:
            row = next((r for r in report["probes"] if r["name"] == name), None)
            lines.append(f"  !! {name}" + (f"  <- {row['selector']}" if row else ""))
        lines.append("  صلّح الـ selector في: " +
                     ", ".join(sorted({r["used_by"] for r in report["probes"]
                                       if r["name"] in report["critical_zero"]})))
    else:
        lines.append("كل الـ selectors الأساسية سليمة.")

    if report["degraded_zero"]:
        lines += ["", "ناقص بس ماشي (في fallback):"]
        for name in report["degraded_zero"]:
            row = next((r for r in report["probes"] if r["name"] == name), None)
            lines.append(f"   ~ {name}: {row.get('zero_ok') or ''}" if row else f"   ~ {name}")

    if report["errors"]:
        lines += ["", "probes رمت استثناء: " + ", ".join(report["errors"])]

    lines += ["", "الأصفار اللي من نوع context طبيعية — كل واحد بيشرح نفسه في selectors.json."]
    return "\n".join(lines)


class PageRecorder:
    """يتنصّت على صفحة ويجمع كل الإشارات اللي بتفيد في تشخيص خطأ."""

    def __init__(self, page, label: str = "page", attach: bool = True, use_cdp: bool = True,
                 trace: bool = False, har: bool = False):
        self.page = page
        self.label = label
        self.console: list[dict] = []
        self.errors: list[dict] = []
        self.network: list[dict] = []
        self.browser_log: list[dict] = []
        self.lifecycle: list[dict] = []
        self.dumps: list[Path] = []
        self._cdp = None
        self._attached = False
        self._t0 = time.time()
        self._trace_on = False
        self._har_path: Path | None = None
        if attach:
            self.attach(use_cdp=use_cdp)
        if trace:
            self.start_trace()
        if har:
            self.start_har()

    # ---------- التتبّع و HAR (على مستوى الـ context مش الصفحة) ----------

    def start_trace(self) -> bool:
        """تتبّع Playwright كامل: صور، DOM snapshots، أكواد المصدر لكل خطوة.

        ملاحظة معروفة: على اتصال CDP، الخطوات اللي بتتعمل بـ CDP خام مش بتظهر
        في الخط الزمني. خطوات Playwright العادية (goto/click/evaluate) بتظهر.
        """
        try:
            self.page.context.tracing.start(screenshots=True, snapshots=True, sources=True)
            self.page.context.tracing.start_chunk(title=self.label)
            self._trace_on = True
        except Exception as exc:
            self._push(self.lifecycle, {"kind": "trace-unavailable", "message": str(exc)[:300]})
            self._trace_on = False
        return self._trace_on

    def start_har(self) -> bool:
        try:
            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            path = DEBUG_DIR / f"{_stamp()}-{_safe(self.label)}.har"
            # من غير محتوى الردود: الـ HAR كان 140–426MB للجروب (سكربتات وصور وفيديو)، والمطلوب
            # للتشخيص الحالة والتوقيت والهيدرز بس. FB_DEBUG_HAR_CONTENT=embed يرجّع المحتوى.
            self.page.context.tracing.start_har(path=str(path), content=HAR_CONTENT)
            self._har_path = path
        except Exception as exc:
            self._push(self.lifecycle, {"kind": "har-unavailable", "message": str(exc)[:300]})
            self._har_path = None
        return self._har_path is not None

    # ---------- التنصّت ----------

    def _at(self) -> float:
        return round(time.time() - self._t0, 3)

    def _push(self, bucket: list[dict], row: dict) -> None:
        if len(bucket) >= MAX_EVENTS:
            bucket.pop(0)
        row["at"] = self._at()
        bucket.append(row)

    def attach(self, use_cdp: bool = True) -> "PageRecorder":
        if self._attached:
            return self
        page = self.page

        def on_console(msg) -> None:
            text = ""
            try:
                text = msg.text
            except Exception:
                text = "<unreadable>"
            row: dict[str, Any] = {"type": msg.type, "text": text}
            try:
                loc = msg.location or {}
                if loc.get("url"):
                    row["source"] = f"{loc.get('url')}:{loc.get('lineNumber')}:{loc.get('columnNumber')}"
            except Exception:
                pass
            self._push(self.console, row)

        def on_pageerror(err) -> None:
            self._push(self.errors, {"kind": "pageerror", "message": str(err)})

        def on_requestfailed(req) -> None:
            failure = ""
            try:
                failure = req.failure or ""
            except Exception:
                pass
            # طلبات ملغية عادية (navigation/abort) مش بالضرورة أخطاء، بنسجّلها وخلاص
            self._push(self.network, {
                "kind": "requestfailed",
                "method": req.method,
                "url": req.url,
                "resource_type": req.resource_type,
                "failure": failure,
            })

        def on_response(resp) -> None:
            try:
                status = resp.status
            except Exception:
                return
            if status < 400:
                return
            self._push(self.network, {
                "kind": "response",
                "status": status,
                "method": resp.request.method,
                "url": resp.url,
                "resource_type": resp.request.resource_type,
            })

        def on_crash(_page) -> None:
            self._push(self.errors, {"kind": "crash", "message": "page crashed"})

        def on_dialog(dialog) -> None:
            self._push(self.errors, {"kind": "dialog", "message": dialog.message, "dialog_type": dialog.type})
            with contextlib.suppress(Exception):
                dialog.dismiss()

        def on_framenavigated(frame) -> None:
            with contextlib.suppress(Exception):
                if frame == self.page.main_frame:
                    self._push(self.lifecycle, {"kind": "navigated", "url": frame.url})

        self._handlers = [
            ("console", on_console),
            ("pageerror", on_pageerror),
            ("requestfailed", on_requestfailed),
            ("response", on_response),
            ("crash", on_crash),
            ("dialog", on_dialog),
            ("framenavigated", on_framenavigated),
        ]
        for event, handler in self._handlers:
            with contextlib.suppress(Exception):
                page.on(event, handler)

        if use_cdp:
            self._attach_cdp()
        self._attached = True
        return self

    def _attach_cdp(self) -> None:
        """سجل المتصفح نفسه: أخطاء CORS و CSP و mixed content اللي الكونسول العادي بيفوّتها."""
        try:
            self._cdp = self.page.context.new_cdp_session(self.page)
            self._cdp.send("Log.enable")
            self._cdp.send("Runtime.enable")
            # من أول التسجيل، عشان عدّادات perf.json تبقى عن التشغيل كله مش من لحظة الدَمب
            with contextlib.suppress(Exception):
                self._cdp.send("Performance.enable")
        except Exception as exc:
            self._push(self.lifecycle, {"kind": "cdp-unavailable", "message": str(exc)})
            self._cdp = None
            return

        def on_entry(event) -> None:
            entry = (event or {}).get("entry") or {}
            self._push(self.browser_log, {
                "source": entry.get("source"),
                "level": entry.get("level"),
                "text": entry.get("text"),
                "url": entry.get("url"),
                "line": entry.get("lineNumber"),
            })

        def on_exception(event) -> None:
            detail = (event or {}).get("exceptionDetails") or {}
            self._push(self.errors, {
                "kind": "exceptionThrown",
                # detail.text غالبًا «Uncaught» بس؛ الرسالة الحقيقية في exception.description
                "message": ((detail.get("exception") or {}).get("description") or detail.get("text") or "")[:2000],
                "url": detail.get("url") or "",
                "line": detail.get("lineNumber"),
                "stack": [
                    f"{f.get('functionName') or '<anon>'} @ {f.get('url')}:{f.get('lineNumber')}"
                    for f in ((detail.get("stackTrace") or {}).get("callFrames") or [])[:15]
                ],
            })

        with contextlib.suppress(Exception):
            self._cdp.on("Log.entryAdded", on_entry)
        with contextlib.suppress(Exception):
            self._cdp.on("Runtime.exceptionThrown", on_exception)

    def detach(self) -> None:
        if self._trace_on:
            with contextlib.suppress(Exception):
                self.page.context.tracing.stop()
            self._trace_on = False
        if self._har_path is not None:
            with contextlib.suppress(Exception):
                self.page.context.tracing.stop_har()
            self._har_path = None
        for event, handler in getattr(self, "_handlers", []):
            with contextlib.suppress(Exception):
                self.page.remove_listener(event, handler)
        if self._cdp is not None:
            with contextlib.suppress(Exception):
                self._cdp.detach()
            self._cdp = None
        self._attached = False

    # ---------- اللقطات ----------

    def mark(self, note: str) -> None:
        """علامة في الخط الزمني من غير حفظ ملفات."""
        self._push(self.lifecycle, {"kind": "mark", "note": note})

    def counts(self) -> dict:
        return {
            "console": len(self.console),
            "console_errors": sum(1 for c in self.console if c.get("type") == "error"),
            "console_warnings": sum(1 for c in self.console if c.get("type") == "warning"),
            "page_errors": len(self.errors),
            "network_problems": len(self.network),
            "browser_log": len(self.browser_log),
        }

    def _summary(self, meta: dict, reason: str, exc: BaseException | None) -> str:
        """ملخص بشري يتقرأ بسرعة."""
        lines = [
            f"# {self.label} / {reason}",
            f"url:   {meta.get('url')}",
            f"title: {meta.get('title')}",
            f"saved: {meta.get('saved_at')}  (+{meta.get('elapsed_sec')}s)",
            "",
            "counts: " + json.dumps(meta["counts"], ensure_ascii=False),
        ]
        if exc is not None:
            lines += ["", f"exception: {meta['exception']['type']}: {meta['exception']['message']}"]
        if meta.get("selectors_zero_match"):
            lines += ["", "selectors with zero matches (شوف selectors.txt):",
                      "  " + ", ".join(meta["selectors_zero_match"])]
        for key, label in (("trace", "trace"), ("har", "har")):
            if meta.get(key):
                lines.append(f"{label}: {meta[key]}")
        # ضوضاء فيسبوك المعروفة بتتعدّ بس، عشان الخطأ الحقيقي مايتدفنش وسط 99 تحذير
        noise = sum(1 for c in self.console if is_known_noise(c.get("text"))) + \
            sum(1 for e in self.errors if is_known_noise(e.get("message"))) + \
            sum(1 for n in self.network if is_known_noise(f"{n.get('failure') or ''} {n.get('url') or ''}"))
        if noise:
            lines += ["", f"known Facebook noise (not listed, still in the .jsonl files): {noise}"]
        hot = [c for c in self.console if c.get("type") == "error" and not is_known_noise(c.get("text"))][-15:]
        if hot:
            lines += ["", "last console errors:"] + [f"  - {c.get('text','')[:300]}" for c in hot]
        errors = [e for e in self.errors if not is_known_noise(e.get("message"))]
        if errors:
            lines += ["", "page errors:"] + [f"  - {e.get('kind')}: {str(e.get('message'))[:300]}" for e in errors[-15:]]
        network = [n for n in self.network if not is_known_noise(f"{n.get('failure') or ''} {n.get('url') or ''}")]
        if network:
            lines += ["", "network problems:"] + [
                f"  - {n.get('status') or n.get('failure')} {n.get('method','')} {str(n.get('url'))[:160]}"
                for n in network[-20:]
            ]
        return "\n".join(lines) + "\n"

    def dump(self, reason: str = "dump", exc: BaseException | None = None) -> Path:
        """يحفظ كل حاجة في فولدر واحد ويرجّع مساره. مابيرفعش استثناء أبدًا."""
        out = DEBUG_DIR / f"{_stamp()}-{_safe(self.label)}-{_safe(reason)}"
        out.mkdir(parents=True, exist_ok=True)

        meta: dict[str, Any] = {
            "label": self.label,
            "reason": reason,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "elapsed_sec": self._at(),
            "counts": self.counts(),
        }

        # الرابط والعنوان أول حاجة: لو الصفحة ميتة، ده اللي بيفرق
        for key, getter in (("url", lambda: self.page.url), ("title", lambda: self.page.title())):
            try:
                meta[key] = getter()
            except Exception as e:
                meta[key] = f"<unavailable: {e}>"

        if exc is not None:
            meta["exception"] = {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-8000:],
            }

        try:
            meta["viewport"] = self.page.viewport_size
        except Exception:
            pass

        def write_logs() -> None:
            _write_jsonl(out / "console.jsonl", self.console)
            _write_jsonl(out / "errors.jsonl", self.errors)
            _write_jsonl(out / "network.jsonl", self.network)
            _write_jsonl(out / "browser-log.jsonl", self.browser_log)
            _write_jsonl(out / "lifecycle.jsonl", self.lifecycle)
            (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            (out / "summary.txt").write_text(self._summary(meta, reason, exc), encoding="utf-8")

        # الرخيص والمهم الأول: لو العملية اتقتلت وسط الـ trace (ممكن ياخد دقيقة)، دول يفضلوا
        with contextlib.suppress(Exception):
            write_logs()

        # HTML
        try:
            (out / "page.html").write_text(self.page.content(), encoding="utf-8", errors="replace")
        except Exception as e:
            meta["html_error"] = str(e)
            # الصفحة ممكن تكون في نص تنقّل. نجرب الـ DOM الخام من الـ frame
            try:
                html = self.page.evaluate("() => document.documentElement.outerHTML")
                (out / "page.html").write_text(html, encoding="utf-8", errors="replace")
                meta["html_error"] += " | recovered via evaluate"
            except Exception as e2:
                meta["html_error"] += f" | evaluate failed: {e2}"

        # الصورة: full page الأول، وبعدين viewport
        shot = out / "page.png"
        # تاب في الخلفية مابيرسمش، والصورة بتستنى لحد الـ timeout (اتشاف على المتصفح الحقيقي)
        with contextlib.suppress(Exception):
            self.page.bring_to_front()
        try:
            self.page.screenshot(path=str(shot), full_page=True, timeout=20000)
            meta["screenshot"] = "full_page"
        except Exception as e:
            try:
                self.page.screenshot(path=str(shot), timeout=10000)
                meta["screenshot"] = f"viewport only ({e})"
            except Exception as e2:
                meta["screenshot"] = f"failed: {e2}"

        # النصوص الظاهرة: أسرع في القراءة من HTML خام
        try:
            text = self.page.evaluate("() => document.body ? document.body.innerText : ''")
            (out / "page.txt").write_text(text or "", encoding="utf-8", errors="replace")
        except Exception:
            pass

        # التخزين والكوكيز
        try:
            storage = self.page.evaluate(
                """() => {
                  const grab = (s) => { const o = {}; try { for (let i = 0; i < s.length; i++) { const k = s.key(i); o[k] = s.getItem(k); } } catch (e) { o._error = String(e); } return o; };
                  return {local: grab(localStorage), session: grab(sessionStorage)};
                }"""
            )
            (out / "storage.json").write_text(json.dumps(storage, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        try:
            cookies = self.page.context.cookies()
            meta["cookie_count"] = len(cookies)
            # أسماء ونطاقات بس. القيم أسرار جلسة، مش بتتحفظ.
            meta["cookie_names"] = sorted({f"{c.get('domain')}{c.get('name')}" for c in cookies})[:80]
        except Exception:
            pass

        # حالة الـ selectors: ده أسرع طريق لمعرفة إيه اللي بار في الـ DOM
        try:
            report = probe_selectors(self.page)
            (out / "selectors.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            (out / "selectors.txt").write_text(
                format_selector_report(report), encoding="utf-8")
            meta["selectors_zero_match"] = report["zero_match"]
        except Exception as e:
            meta["selectors_error"] = str(e)

        # عدّادات الأداء من CDP: بتبيّن لو الصفحة تقيلة أو فيها تسريب ذاكرة
        try:
            if self._cdp is not None:
                metrics = self._cdp.send("Performance.getMetrics")
            else:
                session = self.page.context.new_cdp_session(self.page)
                session.send("Performance.enable")
                metrics = session.send("Performance.getMetrics")
                session.detach()
            flat = {m["name"]: m["value"] for m in metrics.get("metrics", [])}
            (out / "perf.json").write_text(json.dumps(flat, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

        # التتبّع: كل دَمب بياخد chunk لوحده، والتتبّع يكمل بعد كده
        if self._trace_on:
            try:
                self.page.context.tracing.stop_chunk(path=str(out / "trace.zip"))
                meta["trace"] = "trace.zip"
                self.page.context.tracing.start_chunk(title=f"{self.label}:after-{reason}")
            except Exception as e:
                meta["trace_error"] = str(e)[:300]
                self._trace_on = False

        # HAR مابينفعش يتقسّم. بنقفله عند أول دَمب وننقله جوه الفولدر.
        if self._har_path is not None:
            try:
                self.page.context.tracing.stop_har()
                src = self._har_path
                self._har_path = None
                if src.exists():
                    src.replace(out / "network.har")
                    meta["har"] = "network.har"
            except Exception as e:
                meta["har_error"] = str(e)[:300]

        write_logs()

        self.dumps.append(out)
        print(f"[debug] saved {out}", file=sys.stderr)
        return out


@contextlib.contextmanager
def recording(page, label: str = "page", dump_always: bool = False):
    """يسجّل، ويعمل دَمب تلقائي لو حصل استثناء (أو دايمًا لو dump_always)."""
    rec = PageRecorder(page, label=label)
    try:
        yield rec
    except BaseException as exc:
        rec.dump("exception", exc=exc)
        raise
    else:
        if dump_always:
            rec.dump("ok")
    finally:
        rec.detach()


# ------------------------- سطر الأوامر -------------------------


def _connect(cdp: str):
    from playwright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.connect_over_cdp(cdp)
    except Exception as exc:
        playwright.stop()
        raise SystemExit(
            f"مش قادر أتوصل بـ CDP على {cdp}: {exc}\n"
            f"افتح المتصفح الأول:  .venv\\Scripts\\python.exe launch_browser.py"
        ) from exc
    return playwright, browser


def cmd_selectors(args) -> int:
    """يختبر selectors المشروع على صفحة حقيقية. أول حاجة تعملها لما السحب يبوظ."""
    playwright, browser = _connect(args.cdp)
    try:
        context = browser.contexts[0]
        opened = False
        if args.url:
            page = context.new_page()
            opened = True
            try:
                page.goto(args.url, wait_until="domcontentloaded", timeout=args.timeout)
            except Exception as exc:
                print(f"التحميل فشل: {exc}", file=sys.stderr)
                page.close()
                return 1
            page.wait_for_timeout(args.settle)
        else:
            pages = [p for p in context.pages if not p.is_closed()]
            if not pages:
                print("مفيش تابّات مفتوحة. استخدم --url", file=sys.stderr)
                return 1
            page = pages[-1]
            print(f"التاب الحالي: {page.url}\n")

        report = probe_selectors(page)
        print(format_selector_report(report))

        if args.save:
            rec = PageRecorder(page, label="selectors", trace=args.trace, har=args.har)
            page.wait_for_timeout(500)
            out = rec.dump("selector-check")
            rec.detach()
            print(f"\nاللقطة الكاملة: {out}")
        if opened and not args.keep:
            page.close()
        # خروج بكود 1 لو حاجة أساسية بارت، عشان ينفع في سكربت
        if report["critical_zero"]:
            print(f"\nمكسور: {', '.join(report['critical_zero'])}", file=sys.stderr)
            return 1
        return 0
    finally:
        with contextlib.suppress(Exception):
            browser.close()
        playwright.stop()


def cmd_snapshot(args) -> int:
    playwright, browser = _connect(args.cdp)
    try:
        context = browser.contexts[0]
        if args.url:
            page = context.new_page()
            rec = PageRecorder(page, label="snapshot", trace=args.trace, har=args.har)
            try:
                page.goto(args.url, wait_until=args.wait_until, timeout=args.timeout)
            except Exception as exc:
                print(f"التحميل فشل: {exc}", file=sys.stderr)
                rec.dump("goto-failed", exc=exc)
                return 1
            page.wait_for_timeout(args.settle)
            rec.dump("snapshot")
            rec.detach()
            if not args.keep:
                page.close()
            return 0

        pages = [p for p in context.pages if not p.is_closed()]
        if not pages:
            print("مفيش تابّات مفتوحة", file=sys.stderr)
            return 1
        for index, page in enumerate(pages, 1):
            url = ""
            with contextlib.suppress(Exception):
                url = page.url
            print(f"[{index}/{len(pages)}] {url}")
            rec = PageRecorder(page, label=f"tab{index}", trace=args.trace, har=args.har)
            # اللي فات مش مسجّل، فنستنى شوية نلقط أي كونسول جديد
            page.wait_for_timeout(args.settle)
            rec.dump("snapshot")
            rec.detach()
        return 0
    finally:
        with contextlib.suppress(Exception):
            browser.close()
        playwright.stop()


def cmd_watch(args) -> int:
    playwright, browser = _connect(args.cdp)
    recorders: list[PageRecorder] = []
    try:
        context = browser.contexts[0]

        def live(rec: PageRecorder, tag: str) -> None:
            page = rec.page

            def show_console(msg) -> None:
                if msg.type in ("error", "warning") or args.all:
                    print(f"  [{tag}] console.{msg.type}: {msg.text[:400]}")

            def show_error(err) -> None:
                print(f"  [{tag}] PAGE ERROR: {str(err)[:400]}")

            def show_failed(req) -> None:
                with contextlib.suppress(Exception):
                    print(f"  [{tag}] FAILED {req.method} {req.url[:140]} -> {req.failure}")

            with contextlib.suppress(Exception):
                page.on("console", show_console)
                page.on("pageerror", show_error)
                page.on("requestfailed", show_failed)

        for index, page in enumerate([p for p in context.pages if not p.is_closed()], 1):
            # التتبّع و HAR على مستوى الـ context، فأول مسجّل بس بياخدهم
            first = index == 1
            rec = PageRecorder(page, label=f"watch{index}",
                               trace=args.trace and first, har=args.har and first)
            live(rec, f"tab{index}")
            recorders.append(rec)

        def on_new_page(page) -> None:
            tag = f"tab{len(recorders) + 1}"
            print(f"تاب جديد: {tag}")
            rec = PageRecorder(page, label=f"watch{len(recorders) + 1}")
            live(rec, tag)
            recorders.append(rec)

        with contextlib.suppress(Exception):
            context.on("page", on_new_page)

        print(f"بيراقب {len(recorders)} تاب. اتصرف في المتصفح عادي. Ctrl+C يحفظ كل حاجة.")
        if not recorders:
            print("تحذير: مفيش تابّات. افتح تاب في المتصفح وهيتسجّل تلقائي.", file=sys.stderr)
        deadline = time.time() + args.seconds if args.seconds else None
        try:
            while True:
                if deadline and time.time() >= deadline:
                    print("الوقت خلص.")
                    break
                if recorders:
                    recorders[0].page.wait_for_timeout(500)
                else:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            print("\nجاري الحفظ...")
        for rec in recorders:
            with contextlib.suppress(Exception):
                rec.dump("watch")
            rec.detach()
        return 0
    finally:
        with contextlib.suppress(Exception):
            browser.close()
        playwright.stop()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(
        description="تشخيص المتصفح: HTML وكونسول وأخطاء وشبكة وصور",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--cdp", default=CDP, help=f"عنوان CDP (افتراضي {CDP})")
    parser.add_argument("--trace", action="store_true",
                        help="سجّل trace.zip (شوفه بـ: python -m playwright show-trace <path>)")
    parser.add_argument("--har", action="store_true", help="سجّل network.har بكل الطلبات")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sel = sub.add_parser("selectors", help="اختبر selectors المشروع على صفحة حقيقية")
    sel.add_argument("--url", help="لو موجود: يفتح الرابط ويختبر عليه. غير كده: آخر تاب مفتوح")
    sel.add_argument("--timeout", type=int, default=60000)
    sel.add_argument("--settle", type=int, default=3000)
    sel.add_argument("--save", action="store_true", help="احفظ كمان لقطة كاملة (HTML وصورة)")
    sel.add_argument("--keep", action="store_true", help="ماتقفلش التاب")
    sel.set_defaults(func=cmd_selectors)

    snap = sub.add_parser("snapshot", help="يصوّر التابّات المفتوحة، أو يفتح رابط ويصوّره")
    snap.add_argument("--url", help="لو موجود: يفتح تاب جديد على الرابط ده ويسجّل من أول التحميل")
    snap.add_argument("--wait-until", default="domcontentloaded",
                      choices=["load", "domcontentloaded", "networkidle", "commit"])
    snap.add_argument("--timeout", type=int, default=60000)
    snap.add_argument("--settle", type=int, default=2500, help="انتظار قبل اللقطة بالمللي ثانية")
    snap.add_argument("--keep", action="store_true", help="ماتقفلش التاب بعد اللقطة")
    snap.set_defaults(func=cmd_snapshot)

    watch = sub.add_parser("watch", help="يراقب ويطبع لايف، وعند Ctrl+C يحفظ")
    watch.add_argument("--seconds", type=int, default=0, help="يوقف لوحده بعد كده (0 = لحد Ctrl+C)")
    watch.add_argument("--all", action="store_true", help="اطبع كل الكونسول، مش الأخطاء بس")
    watch.set_defaults(func=cmd_watch)

    args = parser.parse_args()
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
