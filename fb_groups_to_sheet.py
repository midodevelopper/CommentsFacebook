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
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

WEBAPP = (
    "https://script.google.com/macros/s/"
    "AKfycbzNQ4J6Sewu04sJaWcAJTHeQuCuQzc4At-gNl7oqXKRVTFtedHxaerG7s2-JZFGxfkUmg/exec"
)
TOKEN = os.environ.get("FB_SHEET_TOKEN", "CommentSecrets")
CDP = os.environ.get("FB_CDP", "http://127.0.0.1:9333")
DUMP_DIR = Path(r"D:\Hermes Browser\dumps")

GROUP_RE = re.compile(r"facebook\.com/groups/([^/?#]+)", re.I)
POST_RE = re.compile(r"/groups/[^/]+/(?:posts|permalink)/(\d+)")
CID_RE = re.compile(r"comment_id=(\d+)")
RCID_RE = re.compile(r"reply_comment_id=(\d+)")
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
            "text": post.get("post_content") or post.get("text") or "",
            "post content": post.get("post_content") or post.get("text") or "",
            "created_time": post.get("created_time") or "",
            "url": post.get("url") or post.get("permalink") or "",
            "permalink": post.get("permalink") or post.get("url") or "",
            "comment_sort": post.get("comment_sort") or "الأحدث",
            "comments": [sheet_comment(c, post.get("post_content") or post.get("text") or "") for c in comments],
        }],
    }


def sheet_post(payload: dict) -> str:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        WEBAPP,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"الشيت رفض الإرسال HTTP {exc.code}: {detail[:500]}") from exc


def collect_post_ids(page, group_id: str, max_posts: int) -> list[str]:
    page.goto(
        f"https://www.facebook.com/groups/{group_id}?sorting_setting=CHRONOLOGICAL",
        wait_until="domcontentloaded",
        timeout=60000,
    )
    page.wait_for_timeout(4000)
    label = page.evaluate(
        """() => {
          const t = [...document.querySelectorAll('[role="button"]')]
            .map(e => (e.innerText || '').replace(/\\s+/g, ' ').trim())
            .find(x => x.includes('فرز موجز المجموعة'));
          return t || '';
        }"""
    )
    ids: list[str] = []
    stale = 0
    while len(ids) < max_posts and stale < 6:
        hrefs = page.eval_on_selector_all(
            "a[href*='/posts/'], a[href*='/permalink/']",
            "els => els.map(e => e.href)",
        )
        before = len(ids)
        for href in hrefs:
            match = POST_RE.search(href or "")
            if match and match.group(1) not in ids:
                ids.append(match.group(1))
                if len(ids) >= max_posts:
                    break
        stale = stale + 1 if len(ids) == before else 0
        if len(ids) >= max_posts:
            break
        page.mouse.wheel(0, 2200)
        page.wait_for_timeout(1800)
    return ids[:max_posts], label


def sort_comments_newest(page) -> str:
    """يفتح قائمة فرز التعليقات ويختار «الأحدث». الضغط لازم يكون حقيقي مش JavaScript."""
    page.wait_for_timeout(1500)
    name = re.compile(r"الأحدث|Newest")
    opener_name = re.compile(r"الأكثر ملاءمة|كل التعليقات|Most relevant|All comments")
    for _ in range(8):
        if page.get_by_role("button", name=name).count():
            return "الأحدث"
        if page.get_by_role("button", name=opener_name).count():
            break
        page.wait_for_timeout(700)
    opener = page.get_by_role("button", name=opener_name)
    if not opener.count():
        return "missing"
    try:
        target = opener.last
        target.scroll_into_view_if_needed(timeout=4000)
        target.click(timeout=5000)
    except Exception:
        return "detached"
    page.wait_for_timeout(1000)
    option = page.get_by_role("menuitem", name=re.compile(r"الأحدث|Newest|التعليقات الأحدث"))
    if not option.count():
        option = page.get_by_text(re.compile(r"^الأحدث"))
    if not option.count():
        page.keyboard.press("Escape")
        return "no-option"
    option.first.click(timeout=5000)
    page.wait_for_timeout(2000)
    if page.get_by_role("button", name=name).count():
        return "الأحدث"
    return "unverified"


def expand_comments(page, rounds: int = 30) -> None:
    """يفتح كل التعليقات وكل أشجار الردود. لا يضغط زر «رد» نفسه."""
    for _ in range(rounds):
        clicked = page.evaluate(
            r"""() => {
              const root = document.querySelector("div[role='dialog']") || document.body;
              const wanted = /عرض المزيد من التعليقات|عرض التعليقات السابقة|عرض المزيد من الردود|عرض كل الردود|عرض الردود|عرض \d+ من الردود|عرض \d+ رد|View more comments|View previous comments|View more replies|View all \d+ replies|View \d+ repl/;
              const buttons = [...root.querySelectorAll('[role="button"]')].filter(e => {
                const t = (e.innerText || '').replace(/\s+/g, ' ').trim();
                return t && t !== 'رد' && t !== 'Reply' && wanted.test(t);
              });
              if (!buttons.length) return 0;
              let n = 0;
              for (const button of buttons.slice(0, 8)) {
                button.click();
                n += 1;
              }
              return n;
            }"""
        )
        if not clicked:
            page.mouse.wheel(0, 1400)
            page.wait_for_timeout(700)
            clicked = page.evaluate(
                r"""() => {
                  const root = document.querySelector("div[role='dialog']") || document.body;
                  const wanted = /عرض المزيد من التعليقات|عرض الردود|عرض \d+ رد|View more comments|View .*repl/;
                  return [...root.querySelectorAll('[role="button"]')].some(e => wanted.test((e.innerText || '').replace(/\s+/g, ' ').trim()));
                }"""
            )
            if not clicked:
                return
            continue
        page.wait_for_timeout(1100)


def visible_snapshot(page, group_id: str, post_id: str) -> dict:
    return page.evaluate(
        r"""(ids) => {
          const postId = ids.postId;
          const clean = (href) => {
            if (!href) return '';
            try {
              const u = new URL(href);
              ['__cft__', '__tn__', 'fbclid'].forEach(k => u.searchParams.delete(k));
              u.hash = '';
              return u.toString();
            } catch (e) { return href.split('&__cft__')[0]; }
          };
          const userIdFrom = (href) => {
            const m = (href || '').match(/\/user\/(\d+)|[?&]id=(\d+)/);
            return m ? (m[1] || m[2]) : '';
          };
          const dialogs = [...document.querySelectorAll("div[role='dialog']")];
          const root = dialogs.sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width)[0]
            || document.querySelector("div[role='main']") || document.body;
          const msg = document.querySelector("div[data-ad-preview='message'], div[data-ad-comet-preview='message']");
          const header = root.querySelector("h2 a[href*='/user/'], h3 a[href*='/user/'], strong a[href*='/user/']")
            || root.querySelector("h2 a, h3 a");
          const timeLink = root.querySelector(`a[href*='/posts/${postId}']`);
          const comments = [];
          root.querySelectorAll("div[role='article'][aria-label]").forEach(article => {
            const rawLink = [...article.querySelectorAll("a[href*='comment_id=']")].map(x => x.href)[0] || '';
            const authorLink = [...article.querySelectorAll("a[href*='/user/']")].find(a => (a.innerText || '').trim()) || null;
            const text = [...article.querySelectorAll("div[dir='auto']")].map(d => d.innerText.trim()).filter(Boolean).join('\n');
            const timeNode = article.querySelector("a[href*='comment_id=']");
            comments.push({
              link: clean(rawLink),
              author: authorLink ? authorLink.innerText.trim() : '',
              author_id: userIdFrom(authorLink ? authorLink.href : ''),
              author_url: authorLink ? clean(authorLink.href) : '',
              text,
              created_time: timeNode ? (timeNode.innerText || '').trim() : '',
              aria_label: article.getAttribute('aria-label') || ''
            });
          });
          const story = [...document.querySelectorAll("[role='article']")].find(a => !a.getAttribute('aria-label') && a.querySelector(`a[href*='/posts/${postId}']`));
          const titleParts = (document.title || '').split('|').map(s => s.trim()).filter(s => s && s !== 'Facebook');
          const titleText = titleParts.length >= 2 ? titleParts[titleParts.length - 1] : '';
          let postContent = msg ? msg.innerText.trim() : '';
          let contentKind = postContent ? 'text' : '';
          if (!postContent && titleText) {
            postContent = titleText;
            contentKind = 'text';
          }
          const scope = story || root;
          const external = [...scope.querySelectorAll("a[href*='l.facebook.com/l.php']")].map(a => {
            try { return decodeURIComponent(new URL(a.href).searchParams.get('u') || ''); } catch (e) { return ''; }
          }).find(u => u && !/facebook\\.com|fbcdn|meta\\.ai/.test(u)) || '';
          const video = [...scope.querySelectorAll('video')].map(v => v.currentSrc || v.src).find(Boolean) || '';
          if (video) { postContent = video; contentKind = 'video'; }
          else if (external) { postContent = external; contentKind = 'link'; }
          return {
            text: postContent,
            post_content: postContent,
            content_kind: contentKind,
            author: header ? header.innerText.trim() : '',
            author_id: userIdFrom(header ? header.href : ''),
            author_url: header ? clean(header.href) : '',
            created_time: timeLink ? (timeLink.getAttribute('aria-label') || timeLink.innerText || '').trim() : '',
            comments
          };
        }""",
        {"groupId": group_id, "postId": post_id},
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
        "created_time": (raw.get("created_time") or "").strip(),
        "aria_label": raw.get("aria_label") or "",
        "permalink": permalink,
    }


def scroll_comment_pane(page) -> None:
    page.evaluate(
        r"""() => {
          const root = document.querySelector("div[role='dialog']") || document.body;
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
          if (best) best.scrollTop += Math.max(700, best.clientHeight * 0.8);
          else window.scrollBy(0, 900);
        }"""
    )


def scrape_post(page, group_id: str, post_id: str, min_comments: int, max_comments: int, on_batch=None, batch_size: int = 80) -> dict:
    url = f"https://www.facebook.com/groups/{group_id}/posts/{post_id}/"
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.bring_to_front()
    page.wait_for_timeout(2500)
    sort_state = "missing"
    for _ in range(3):
        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        try:
            sort_state = sort_comments_newest(page)
        except Exception as exc:
            sort_state = f"error:{exc}"
        if sort_state == "الأحدث":
            break
        page.wait_for_timeout(1200)
    if sort_state != "الأحدث":
        raise SystemExit(f"فرز التعليقات مش «الأحدث» على المنشور {post_id}: {sort_state}")
    seen: dict[str, dict] = {}
    order: list[str] = []
    pending: list[dict] = []
    batch_index = 0
    stale = 0
    header = {}
    # فيسبوك بيمسح التعليقات القديمة من الصفحة لما العدد يبقى بالآلاف،
    # فلازم نلتقط الظاهر قبل التمرير، ونفتح الردود، لحد ما مفيش جديد.
    for _ in range(400):
        if len(order) >= max_comments:
            break
        snap = visible_snapshot(page, group_id, post_id)
        if snap.get("post_content"):
            header["post_content"] = snap["post_content"]
            header["content_kind"] = snap.get("content_kind") or ""
            header["text"] = snap["post_content"]
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
            if len(order) >= max_comments:
                break
            seen[comment["comment_id"]] = comment
            order.append(comment["comment_id"])
            pending.append(comment)
            added += 1
        while on_batch and len(pending) >= batch_size:
            batch = pending[:batch_size]
            del pending[:batch_size]
            batch_index += 1
            on_batch(batch, batch_index, False)
        expand_comments(page, rounds=1)
        if added:
            stale = 0
        else:
            stale += 1
        scroll_comment_pane(page)
        page.wait_for_timeout(700)
        if stale >= 5:
            break
    if on_batch and pending:
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
        "text": (header.get("post_content") or header.get("text") or "").strip(),
        "post_content": (header.get("post_content") or header.get("text") or "").strip(),
        "content_kind": header.get("content_kind") or "",
        "created_time": (header.get("created_time") or "").strip(),
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


def archive_group(page, url: str, min_posts: int, max_posts: int, min_comments: int, max_comments: int, dry: bool = True, batch_size: int = 80) -> dict:
    group_id = group_key(url)
    post_ids, feed_sort = collect_post_ids(page, group_id, max_posts)
    posts = []
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
        print(
            f"{group_id}/{post_id}: comments={post['comments_found']} "
            f"replies={post['replies_found']} sort={post['comment_sort']}"
        )
        if not dry:
            comments = post.get("comments") or []
            chunks = [comments[i:i + batch_size] for i in range(0, len(comments), batch_size)] or [[]]
            for index, chunk in enumerate(chunks, 1):
                answer = sheet_post(sheet_post_payload(group_id, post, chunk))
                print(f"sheet batch {index}/{len(chunks)} n={len(chunk)}: {answer[:240]}")
                post["sheet_batches"] = index
        posts.append(post)
        time.sleep(1.5)
    name = ""
    try:
        name = page.title().split("|")[0].strip()
    except Exception:
        name = ""
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="أرشفة جروبات فيسبوك إلى Google Sheet")
    parser.add_argument("--group", action="append", required=True, help="رابط جروب. كرره لأكتر من جروب")
    parser.add_argument("--min-posts", type=int, required=True)
    parser.add_argument("--max-posts", type=int, required=True)
    parser.add_argument("--min-comments", type=int, required=True)
    parser.add_argument("--max-comments", type=int, required=True)
    parser.add_argument("--cdp", default=CDP)
    parser.add_argument("--dry", action="store_true", help="احفظ JSON بس، من غير إرسال للشيت")
    parser.add_argument("--batch-size", type=int, default=80, help="حجم دفعة الإرسال للشيت لما التعليقات تبقى بالآلاف")
    args = parser.parse_args()
    if args.min_posts < 1 or args.max_posts < args.min_posts:
        raise SystemExit("min-posts لازم يكون 1 أو أكتر، وmax-posts أكبر منه أو يساويه")
    if args.min_comments < 0 or args.max_comments < args.min_comments:
        raise SystemExit("min-comments مش سالب، وmax-comments أكبر منه أو يساويه")
    return args


def main() -> None:
    args = parse_args()
    from playwright.sync_api import sync_playwright

    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(args.cdp)
        context = browser.contexts[0]
        page = context.new_page()
        try:
            for url in args.group:
                payload = archive_group(
                    page,
                    url,
                    args.min_posts,
                    args.max_posts,
                    args.min_comments,
                    args.max_comments,
                    dry=args.dry,
                    batch_size=args.batch_size,
                )
                results.append(payload)
                out = DUMP_DIR / f"{payload['group_id']}-{stamp}.json"
                out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                print("saved", out)
                if not args.dry and not any(p.get("sheet_batches") for p in payload["posts"]):
                    answer = sheet_post(payload)
                    print("sheet:", answer[:400])
        finally:
            page.close()
    summary = DUMP_DIR / f"run-{stamp}.json"
    summary.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("done", summary)


if __name__ == "__main__":
    main()
