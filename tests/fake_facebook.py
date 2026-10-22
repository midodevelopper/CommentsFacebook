"""Offline Facebook simulator for browser tests.

Installs a route on a Playwright context that answers every www.facebook.com
request from in-memory data and aborts everything else. The markup copies the
structure the scripts' selectors depend on (role=article + aria-label,
a[href*=comment_id=], div[dir=auto], data-ad-preview=message, role=button sort
control + role=menuitem options, "View N replies" expanders, role=textbox reply
composer submitted with Enter).

It is a model of Facebook's DOM as the code expects it, not a copy of the live
site. A passing browser test means the code still does what it did against this
model; it says nothing about whether Facebook's live DOM still matches.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field

FB = "https://www.facebook.com"


@dataclass
class Comment:
    id: str
    author: str = "مستخدم"
    author_id: str | None = "100000000000001"
    text: str = ""
    time: str = "٥ د"
    replies: list["Comment"] = field(default_factory=list)

    def to_js(self) -> dict:
        return {
            "id": self.id, "author": self.author, "author_id": self.author_id,
            "text": self.text, "time": self.time,
            "replies": [r.to_js() for r in self.replies],
        }


@dataclass
class Post:
    post_id: str
    author: str = "صاحب المنشور"
    author_id: str | None = "100000000000099"
    message: str | None = "نص المنشور"
    created: str = "21 أكتوبر 2026 الساعة 10:00 ص"
    title: str = "Facebook"
    comments: list[Comment] = field(default_factory=list)   # newest first
    # relevant | newest | all-comments | none | no-option | unverified | text-option
    sort_mode: str = "relevant"
    lang: str = "ar"
    page_size: int | None = None          # top-level comments per "show more"
    replies_hidden: bool = True
    nested_replies: bool = False
    virtualize: int | None = None         # keep only the last N top-level comments in the DOM
    dialog: bool = False
    composer: str | None = "top"          # top | bottom | None  (main "write a comment" box)
    reply_button: bool = True
    reply_box: bool = True
    reply_submit: str = "ok"              # ok (reply shows under the comment) | ignore (Enter does nothing)
    # live 2026-10-22: the reply composer («اكتب ردًا عامًا...») already exists before Reply is clicked,
    # and the click fills in an @mention of the comment's author
    reply_box_prerendered: bool = False
    mention: bool = True
    # live 2026-10-22: a non-empty composer makes leaving the page raise a beforeunload dialog
    draft_guard: bool = True
    me_id: str = "100000000000777"        # the logged-in account (author of the replies we post)
    me_name: str = "أنا"
    external_url: str | None = None
    video_src: str | None = None
    escape_closes: bool = False           # live 2026-10-21: Escape closes the post dialog -> facebook.com/
    no_comments_text: bool = True         # Facebook writes «لا توجد تعليقات حتى الآن» when there are none
    # live 2026-10-21: a shared post has no data-ad-preview; the shared text sits in dir=auto blocks
    # (rendered twice: visible + "see more" copy) next to a link to the original /<page>/posts/pfbid...
    shared_text: str | None = None
    # live 2026-10-21: «تم إيقاف تشغيل التعليقات لهذا المنشور من قبل …» — no sort control, no none-text
    comments_off: bool = False
    # live 2026-10-21: video/reel caption lives outside data-ad-preview, next to a /reel/ link
    video_caption: str | None = None
    reel_link: str | None = None
    shared_link: str = "https://www.facebook.com/somepage/posts/pfbid0SharedOriginal"


@dataclass
class Group:
    group_id: str
    post_ids: list[str] = field(default_factory=list)
    page_size: int = 3                    # post cards revealed per scroll
    extra_links: list[str] = field(default_factory=list)
    # live 2026-10-21: the hidden Notifications panel (banner > dialog) holds other groups' post links
    notification_links: list[str] = field(default_factory=list)
    private: bool = False                 # live 2026-10-21: private group, account not a member
    sort_label: str = "فرز موجز المجموعة: المنشورات الجديدة"


_CSS = """
body { font-family: sans-serif; margin: 0; }
[dir=auto] { white-space: pre-wrap; }
[role=article] { padding: 6px; border-bottom: 1px solid #ddd; }
[role=button], [role=menuitem] { display: inline-block; padding: 4px; cursor: pointer; }
[role=textbox] { min-height: 20px; border: 1px solid #999; }
.spacer { height: 3000px; }
"""

_POST_JS = r"""
(() => {
const D = window.__DATA;
const EN = D.lang === 'en';
const T = EN
  ? {relevant: 'Most relevant', all: 'All comments', newest: 'Newest', newestItem: 'Newest',
     more: 'View more comments', reply: 'Reply', replies: n => `View ${n} replies`,
     comment: 'Comment by ', replyBy: 'Reply by ', writeComment: 'Write a public comment…',
     writeReply: 'Write a public reply…'}
  : {relevant: 'الأكثر ملاءمة', all: 'كل التعليقات', newest: 'الأحدث', newestItem: 'الأحدث',
     more: 'عرض المزيد من التعليقات', reply: 'رد', replies: n => `عرض ${n} رد`,
     comment: 'تعليق بواسطة ', replyBy: 'رد بواسطة ', writeComment: 'اكتب تعليقًا عامًا...',
     writeReply: 'اكتب ردًا عامًا...'};
const G = D.group_id, P = D.post_id;
const base = `https://www.facebook.com/groups/${G}`;
const tok = '__cft__[0]=AZtrackingtoken&__tn__=R';
const userUrl = uid => `${base}/user/${uid}/?${tok}`;
let sort = D.sort_mode === 'newest' ? 'newest' : 'initial';
let menuOpen = false;
let shown = D.page_size || D.comments.length;
const open = new Set(D.replies_hidden ? [] : D.comments.map(c => c.id));
window.__sent = [];
window.__clicks = [];

const host = D.dialog
  ? document.body.appendChild(Object.assign(document.createElement('div'), {}))
  : document.body.appendChild(document.createElement('div'));
host.setAttribute('role', D.dialog ? 'dialog' : 'main');
if (D.dialog) host.style.cssText = 'width: 900px; height: 700px; overflow-y: auto;';

function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    e.setAttribute(k, v);
  }
  for (const k of kids) {
    if (k === null || k === undefined) continue;
    e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return e;
}

function article(c, parent) {
  const href = parent
    ? `${base}/posts/${P}/?comment_id=${parent.id}&reply_comment_id=${c.id}&${tok}`
    : `${base}/posts/${P}/?comment_id=${c.id}&${tok}`;
  return el('div', {role: 'article', 'aria-label': (parent ? T.replyBy : T.comment) + c.author, 'data-cid': c.id},
    c.author_id ? el('a', {href: userUrl(c.author_id)}, c.author) : el('span', {}, c.author),
    el('div', {dir: 'auto'}, c.text),
    el('a', {href}, c.time || ''),
    D.reply_button ? el('div', {role: 'button', 'data-action': 'reply', 'data-cid': c.id, tabindex: '0'}, T.reply) : null);
}

function composer(forId, kind) {
  return el('div', {role: 'textbox', contenteditable: 'true', 'data-for': forId,
                    'aria-label': kind === 'reply' ? T.writeReply : T.writeComment});
}

const findComment = id => {
  for (const c of D.comments) {
    if (c.id === id) return {c, parent: null};
    const r = (c.replies || []).find(x => x.id === id);
    if (r) return {c: r, parent: c};
  }
  return null;
};
let replySeq = 0;
if (D.draft_guard) window.addEventListener('beforeunload', e => {
  if ([...document.querySelectorAll('[role=textbox]')].some(b => (b.textContent || '').trim())) {
    e.preventDefault();
    e.returnValue = '';
  }
});

function ordered() {
  return (sort === 'newest' || D.sort_mode === 'none') ? D.comments : [...D.comments].reverse();
}

function render() {
  host.replaceChildren();
  const story = el('div', {role: 'article'},
    el('h2', {}, D.author_id ? el('a', {href: userUrl(D.author_id)}, D.author) : el('a', {href: `${base}/`}, D.author || '')),
    el('a', {href: `${base}/posts/${P}/?${tok}`, 'aria-label': D.created}, '٣ س'),
    D.message !== null && D.shared_text === null ? el('div', {'data-ad-preview': 'message'}, el('div', {dir: 'auto'}, D.message)) : null,
    D.video_caption !== null ? el('div', {class: 'reel'},
      D.reel_link ? el('a', {href: D.reel_link}, 'reel') : null,
      el('div', {dir: 'auto'}, D.video_caption + '... عرض المزيد'),
      el('div', {dir: 'auto'}, 'المقطع الصوتي الأصلي')) : null,
    D.shared_text !== null ? el('div', {class: 'shared'},
      el('a', {href: D.shared_link + '?' + tok}, 'صفحة المصدر'),
      el('div', {dir: 'auto'}, 'منذ ٢١ دقيقة · تمت المشاركة مع العامة'),
      el('div', {dir: 'auto'}, el('span', {}, D.shared_text), el('span', {style: 'display:none'}, D.shared_text))) : null,
    D.external_url ? el('a', {href: 'https://l.facebook.com/l.php?u=' + encodeURIComponent(D.external_url) + '&h=AT0'}, 'link') : null,
    D.video_src ? el('video', {src: D.video_src}) : null,
    el('div', {role: 'button'}, EN ? 'Like' : 'أعجبني'));          // real posts always have action buttons
  host.append(story);
  if (D.composer === 'top') host.append(composer('post'));
  if (D.sort_mode !== 'none') {
    const label = sort === 'newest' ? T.newest : (D.sort_mode === 'all-comments' ? T.all : T.relevant);
    host.append(el('div', {role: 'button', 'data-action': 'sort', tabindex: '0'}, label));
    if (menuOpen) {
      const items = [T.relevant];
      if (D.sort_mode !== 'no-option') items.push(T.newestItem);
      items.push(T.all);
      const role = D.sort_mode === 'text-option' ? null : 'menuitem';
      host.append(el('div', {role: role ? 'menu' : null},
        ...items.map(t => el('div', {role, 'data-action': 'pick', 'data-value': t}, t))));
    }
  }
  const all = ordered();
  let visible = all.slice(0, shown);
  if (D.virtualize) visible = visible.slice(-D.virtualize);
  // a ?comment_id= permalink brings that comment (and its reply thread) into view, like Facebook
  const focus = new URLSearchParams(location.search).get('comment_id');
  const focused = focus && all.find(c => c.id === focus);
  if (focused && !visible.includes(focused)) visible = [focused, ...visible];
  if (focused && new URLSearchParams(location.search).get('reply_comment_id')) open.add(focused.id);
  const list = el('div', {class: 'comments'});
  for (const c of visible) {
    const a = article(c, null);
    list.append(a);
    const reps = c.replies || [];
    if (!reps.length) continue;
    if (open.has(c.id)) {
      if (D.nested_replies) {
        for (const r of reps) a.append(article(r, c));
      } else {
        const box = el('div', {class: 'replies'});
        for (const r of reps) box.append(article(r, c));
        list.append(box);
      }
    } else {
      list.append(el('div', {role: 'button', 'data-action': 'replies', 'data-cid': c.id}, T.replies(reps.length)));
    }
  }
  // hidden per-comment reply composers, appended after each thread (live 2026-10-22)
  if (D.reply_box_prerendered) {
    for (const c of visible) {
      const box = composer(c.id, 'reply');
      box.style.display = 'none';
      const thread = list.querySelector(`[role=article][data-cid="${c.id}"]`);
      let after = thread;
      while (after.nextElementSibling && after.nextElementSibling.classList.contains('replies')) after = after.nextElementSibling;
      after.after(box);
    }
  }
  host.append(list);
  if (D.comments_off) host.append(el('div', {}, EN ? 'Commenting has been turned off for this post.' : 'تم إيقاف تشغيل التعليقات لهذا المنشور من قبل صاحبه.'));
  else if (!D.comments.length && D.no_comments_text) host.append(el('h3', {}, EN ? 'No comments yet' : 'لا توجد تعليقات حتى الآن'));
  if (shown < all.length) host.append(el('div', {role: 'button', 'data-action': 'more'}, T.more));
  if (D.composer === 'bottom') host.append(composer('post'));
  host.append(el('div', {class: 'spacer'}));
}

document.addEventListener('click', e => {
  const t = e.target.closest('[data-action]');
  if (!t) return;
  const act = t.dataset.action;
  window.__clicks.push(act + ':' + (t.dataset.cid || t.dataset.value || ''));
  if (act === 'sort') { menuOpen = !menuOpen; render(); }
  else if (act === 'pick') {
    if (t.dataset.value === T.newestItem && D.sort_mode !== 'unverified') sort = 'newest';
    menuOpen = false; render();
  }
  else if (act === 'more') { shown += (D.page_size || D.comments.length); render(); }
  else if (act === 'replies') {
    // expand in place like Facebook does, so other expander buttons stay attached
    open.add(t.dataset.cid);
    const c = D.comments.find(x => x.id === t.dataset.cid);
    if (D.nested_replies) {
      const parent = t.previousElementSibling;
      for (const r of c.replies) parent.append(article(r, c));
      t.remove();
    } else {
      const box = el('div', {class: 'replies'});
      for (const r of c.replies) box.append(article(r, c));
      t.replaceWith(box);
    }
  }
  else if (act === 'reply') {
    if (!D.reply_box) return;
    let box = D.reply_box_prerendered && document.querySelector(`[role=textbox][data-for="${t.dataset.cid}"]`);
    if (box) box.style.display = '';
    else {
      box = composer(t.dataset.cid, 'reply');
      t.closest('[role=article]').after(box);
    }
    const found = findComment(t.dataset.cid);
    if (D.mention && found) box.textContent = found.c.author + ' ';   // editors keep a typed trailing space as nbsp
  }
});

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if (menuOpen) { menuOpen = false; render(); }
    else if (D.escape_closes) { location.href = 'https://www.facebook.com/'; }
    return;
  }
  const box = e.target.closest && e.target.closest('[role=textbox]');
  if (e.key === 'Enter' && !e.shiftKey && box) {
    e.preventDefault();
    if (D.reply_submit === 'ignore') return;
    const row = {comment_id: box.dataset.for, text: box.innerText.replace(/ /g, ' '), url: location.href};
    window.__sent.push(row);
    fetch('/__test/reply', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(row)});
    box.dataset.sent = '1';
    // like Facebook: the reply shows up in the thread and the composer empties
    const found = findComment(box.dataset.for);
    if (found) {
      const r = {id: String(9900000000000000 + (++replySeq)), author: D.me_name, author_id: D.me_id,
                 text: box.innerText.trim(), time: 'الآن'};
      box.before(article(r, found.parent || found.c));
    }
    box.textContent = '';
  }
});

render();
})();
"""

_FEED_JS = r"""
(() => {
const D = window.__DATA;
let shown = D.page_size;
if (D.private) {
  const priv = document.body.appendChild(document.createElement('div'));
  priv.setAttribute('role', 'main');
  priv.innerHTML = "<h1>جروب</h1><div>مجموعة \u200fخاصة\u200f · ٣٫٥ مليون عضو</div><div role='button'>انضمام إلى المجموعة</div>" +
    "<div>يمكن للأعضاء فقط رؤية الأعضاء الموجودين بالمجموعة وما يقومون بنشره.</div>";
  return;
}
if (D.notification_links.length) {
  const banner = document.body.appendChild(document.createElement('div'));
  banner.setAttribute('role', 'banner');
  const panel = banner.appendChild(document.createElement('div'));
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', 'الإشعارات');
  panel.style.cssText = 'width: 0; overflow: hidden;';
  for (const href of D.notification_links) {
    const a = panel.appendChild(document.createElement('a'));
    a.href = href; a.textContent = 'notification';
  }
}
const main = document.body.appendChild(document.createElement('div'));
main.setAttribute('role', 'main');
function render() {
  main.replaceChildren();
  const sortBtn = document.createElement('div');
  sortBtn.setAttribute('role', 'button');
  sortBtn.textContent = D.sort_label;
  main.append(sortBtn);
  for (const id of D.post_ids.slice(0, shown)) {
    const card = document.createElement('div');
    card.setAttribute('role', 'article');
    card.innerHTML = `<a href="https://www.facebook.com/groups/${D.group_id}/posts/${id}/?__cft__[0]=AZ&__tn__=R">٣ س</a>
      <a href="https://www.facebook.com/groups/${D.group_id}/permalink/${id}/">#</a>`;
    main.append(card);
  }
  for (const href of D.extra_links) {
    const a = document.createElement('a');
    a.href = href; a.textContent = 'extra';
    main.append(a);
  }
  const spacer = document.createElement('div');
  spacer.style.height = '4000px';
  main.append(spacer);
}
document.addEventListener('wheel', () => { if (shown < D.post_ids.length) { shown += D.page_size; render(); } });
render();
})();
"""


def _page(title: str, data: dict, script: str) -> str:
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    safe_title = title.replace("<", "&lt;")
    return (
        "<!doctype html><html lang='ar' dir='rtl'><head><meta charset='utf-8'>"
        f"<title>{safe_title}</title><style>{_CSS}</style></head><body>"
        f"<script>window.__DATA = {blob};</script><script>{script}</script>"
        "</body></html>"
    )


POST_PATH = re.compile(r"^/groups/([^/]+)/(?:posts|permalink)/(\d+)/?$")
FEED_PATH = re.compile(r"^/groups/([^/]+)/?$")


class FakeFacebook:
    def __init__(self):
        self.groups: dict[str, Group] = {}
        self.posts: dict[tuple[str, str], Post] = {}
        self.requests: list[str] = []
        self.replies: list[dict] = []

    # ---- data
    def add_group(self, group: Group, posts: list[Post] | None = None) -> Group:
        self.groups[group.group_id] = group
        for post in posts or []:
            self.add_post(group.group_id, post)
            if post.post_id not in group.post_ids:
                group.post_ids.append(post.post_id)
        return group

    def add_post(self, group_id: str, post: Post) -> Post:
        self.posts[(group_id, post.post_id)] = post
        return post

    # ---- routing
    def install(self, context) -> None:
        context.route("**/*", self._handle)

    def navigations(self) -> list[str]:
        return [u for u in self.requests if "/__test/" not in u]

    def _handle(self, route) -> None:
        url = route.request.url
        self.requests.append(url)
        parts = urllib.parse.urlsplit(url)
        if parts.hostname != "www.facebook.com":
            route.abort()
            return
        if parts.path == "/__test/reply":
            self.replies.append(json.loads(route.request.post_data or "{}"))
            route.fulfill(status=200, content_type="application/json", body='{"ok":true}')
            return
        match = POST_PATH.match(parts.path)
        if match:
            post = self.posts.get((match.group(1), match.group(2)))
            if post is None:
                route.fulfill(status=200, content_type="text/html; charset=utf-8",
                              body="<!doctype html><html><head><meta charset='utf-8'><title>Facebook</title></head>"
                                   "<body><div role='main'>هذا المحتوى غير متاح الآن</div></body></html>")
                return
            data = {"group_id": match.group(1), **post.__dict__,
                    "comments": [c.to_js() for c in post.comments]}
            route.fulfill(status=200, content_type="text/html; charset=utf-8",
                          body=_page(post.title, data, _POST_JS))
            return
        match = FEED_PATH.match(parts.path)
        if match and match.group(1) in self.groups:
            group = self.groups[match.group(1)]
            route.fulfill(status=200, content_type="text/html; charset=utf-8",
                          body=_page(f"{group.group_id} | Facebook", group.__dict__, _FEED_JS))
            return
        route.fulfill(status=404, content_type="text/html; charset=utf-8",
                      body="<!doctype html><title>Not found</title><div role='main'>404</div>")


def wait_for(page, predicate, timeout_ms: int = 3000) -> bool:
    """Pump Playwright's event loop until predicate() is true (route handlers run during waits)."""
    from playwright.sync_api import Page

    # bypass the fast_waits cap so a real 20ms tick happens each loop
    waited = 0
    while waited < timeout_ms:
        if predicate():
            return True
        Page.wait_for_timeout(page, 20)
        waited += 20
    return predicate()


def comments(n: int, start: int = 1, prefix: str = "تعليق") -> list[Comment]:
    """n top-level comments, newest first, with 15-digit IDs."""
    return [
        Comment(id=str(900000000000000 + start + i), author=f"عضو {start + i}",
                author_id=str(100000000000000 + start + i), text=f"{prefix} رقم {start + i}")
        for i in range(n)
    ]
