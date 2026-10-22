"""fb_return_replies.py: Sheets calls, main() loop, and post_one() against the simulator.

No real reply is ever published: post_one runs only inside the headless browser
against tests/fake_facebook.py, and Sheets calls go to a loopback server.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error

import pytest

import fb_return_replies as replies
from conftest import PYTHON, ROOT
from fake_facebook import Comment, Group, Post, comments, wait_for

G = "4678007325765117"
P = "77"


# ---------------------------------------------------------------- constants

@pytest.mark.unit
def test_constants():
    import fb_groups_to_sheet

    assert replies.WEBAPP == fb_groups_to_sheet.WEBAPP
    assert replies.CDP == os.environ.get("FB_CDP", "http://127.0.0.1:9333")


@pytest.mark.cli
def test_token_reads_fb_sheet_token_env_like_the_scraper():
    """Fixed D-05: the reply script used to hardcode TOKEN and ignore FB_SHEET_TOKEN."""
    code = "import fb_return_replies as m; print(m.TOKEN)"
    out = subprocess.run([PYTHON, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60,
                         env={**os.environ, "FB_SHEET_TOKEN": "from-env"})
    assert out.stdout.strip() == "from-env"
    env = {k: v for k, v in os.environ.items() if k != "FB_SHEET_TOKEN"}
    out = subprocess.run([PYTHON, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60, env=env)
    assert out.stdout.strip() == "CommentSecrets"


@pytest.mark.cli
def test_cli_help_lists_flags():
    out = subprocess.run([PYTHON, "fb_return_replies.py", "--help"], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8", timeout=60, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert out.returncode == 0
    for flag in ("--cdp", "--debug", "--trace"):
        assert flag in out.stdout


# ---------------------------------------------------------------- sheet_get / sheet_post (real loopback HTTP)

@pytest.fixture
def sheet(http_server, monkeypatch):
    monkeypatch.setattr(replies, "WEBAPP", http_server.url + "/exec")
    return http_server


@pytest.mark.unit
def test_sheet_get_sends_action_and_token_as_query(sheet):
    sheet.respond(body={"ok": True, "count": 0, "replies": []})
    assert replies.sheet_get("pending_replies") == {"ok": True, "count": 0, "replies": []}
    req = sheet.requests[-1]
    assert req["method"] == "GET" and req["path"] == "/exec"
    assert req["query"] == {"action": "pending_replies", "token": replies.TOKEN}


@pytest.mark.unit
def test_sheet_post_sends_json(sheet):
    sheet.respond(body={"ok": True, "marked": 1})
    assert replies.sheet_post({"token": "t", "action": "mark_replied", "comment_ids": ["9"]}) == {"ok": True, "marked": 1}
    req = sheet.requests[-1]
    assert req["method"] == "POST" and req["headers"]["Content-Type"] == "application/json"
    assert json.loads(req["body"]) == {"token": "t", "action": "mark_replied", "comment_ids": ["9"]}


@pytest.mark.unit
def test_sheet_post_escapes_non_ascii(sheet):
    """Characterization: unlike the scraper, json.dumps here uses ensure_ascii (still valid JSON)."""
    replies.sheet_post({"x": "عربي"})
    assert b"\\u0639" in sheet.requests[-1]["body"]


@pytest.mark.unit
@pytest.mark.parametrize("fn", ["get", "post"])
def test_sheet_calls_raise_on_invalid_json(sheet, fn):
    sheet.respond(body="<html>error</html>", content_type="text/html")
    with pytest.raises(json.JSONDecodeError):
        replies.sheet_get("pending_replies") if fn == "get" else replies.sheet_post({})


@pytest.mark.unit
@pytest.mark.parametrize("fn", ["get", "post"])
def test_sheet_calls_propagate_http_errors(sheet, fn):
    sheet.respond(status=500, body="x")
    with pytest.raises(urllib.error.HTTPError):
        replies.sheet_get("pending_replies") if fn == "get" else replies.sheet_post({})


@pytest.mark.unit
def test_sheet_calls_use_60s_timeout(monkeypatch):
    seen = []

    def fake_urlopen(req, timeout):
        seen.append(timeout)
        raise TimeoutError()

    monkeypatch.setattr(replies.urllib.request, "urlopen", fake_urlopen)
    for call in (lambda: replies.sheet_get("x"), lambda: replies.sheet_post({})):
        with pytest.raises(TimeoutError):
            call()
    assert seen == [60, 60]


# ---------------------------------------------------------------- main() loop (post_one and browser replaced)

class FakeRecorder:
    instances: list["FakeRecorder"] = []

    def __init__(self, page, label="page", trace=False, har=False, **_):
        self.label, self.trace, self.har = label, trace, har
        self.marks, self.dumps, self.detached = [], [], False
        FakeRecorder.instances.append(self)

    def mark(self, note):
        self.marks.append(note)

    def dump(self, reason="dump", exc=None):
        self.dumps.append(reason)

    def detach(self):
        self.detached = True


@pytest.fixture
def loop(monkeypatch):
    import browser_debug

    state = {"pending": {"ok": True, "count": 0, "replies": []}, "posted": [], "outcomes": {},
             "pw_used": False, "page_closed": False}

    class Page:
        def on(self, event, fn):
            state.setdefault("handlers", []).append(event)

        def close(self):
            state["page_closed"] = True

    class PW:
        def __enter__(self):
            state["pw_used"] = True
            return self

        def __exit__(self, *a):
            return False

        class chromium:
            @staticmethod
            def connect_over_cdp(url):
                state["cdp"] = url

                class Ctx:
                    def new_page(self):
                        return Page()

                class B:
                    contexts = [Ctx()]
                return B()

    def post_one(page, item):
        outcome = state["outcomes"].get(item["comment_id"], "posted")
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(replies, "sync_playwright", lambda: PW())
    monkeypatch.setattr(replies, "sheet_get", lambda action: state.setdefault("get", action) and state["pending"])
    monkeypatch.setattr(replies, "sheet_post", lambda payload: state["posted"].append(payload) or {"ok": True, "marked": 1})
    monkeypatch.setattr(replies, "post_one", post_one)
    FakeRecorder.instances = []
    monkeypatch.setattr(browser_debug, "PageRecorder", FakeRecorder)
    return state


def run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["fb_return_replies.py", *argv])
    return replies.main()


def item(cid, reply="شكرا"):
    return {"comment_id": cid, "post_id": P, "group_id": G, "parent_id": "",
            "permalink": f"https://www.facebook.com/groups/{G}/posts/{P}/?comment_id={cid}", "author": "a",
            "reply": reply}


@pytest.mark.unit
def test_main_requests_pending_replies(monkeypatch, loop):
    run(monkeypatch)
    assert loop["get"] == "pending_replies"


@pytest.mark.unit
def test_main_not_ok_exits_with_response(monkeypatch, loop):
    loop["pending"] = {"ok": False, "error": "bad token"}
    with pytest.raises(SystemExit) as exc:
        run(monkeypatch)
    assert exc.value.code == {"ok": False, "error": "bad token"}
    assert not loop["pw_used"]


@pytest.mark.unit
def test_main_old_deployment_without_replies_key(monkeypatch, loop):
    loop["pending"] = {"ok": True, "msg": "receiver is running"}
    with pytest.raises(SystemExit) as exc:
        run(monkeypatch)
    assert "facebook_sheet_receiver.gs" in str(exc.value)


@pytest.mark.unit
@pytest.mark.parametrize("value", [[], None])
def test_main_empty_pending_does_not_open_browser(monkeypatch, loop, capsys, value):
    loop["pending"] = {"ok": True, "replies": value}
    run(monkeypatch)
    assert not loop["pw_used"]
    assert capsys.readouterr().out.strip() == "pending 0"


@pytest.mark.unit
def test_main_marks_only_posted_items(monkeypatch, loop, capsys):
    loop["pending"]["replies"] = [item("1"), item("2"), item("3"), item("4"), item("5")]
    loop["outcomes"] = {"2": "no-comment", "3": "no-reply-button", "4": RuntimeError("boom"),
                        "5": "missing-permalink"}
    run(monkeypatch, "--cdp", "http://127.0.0.1:1")
    assert loop["posted"] == [{"token": replies.TOKEN, "action": "mark_replied", "comment_ids": ["1"]}]
    assert loop["cdp"] == "http://127.0.0.1:1"
    assert loop["page_closed"]
    out = capsys.readouterr().out
    assert "1 posted" in out and "2 no-comment" in out and "4 error boom" in out


@pytest.mark.unit
def test_main_marks_each_success_separately(monkeypatch, loop):
    loop["pending"]["replies"] = [item("1"), item("2")]
    run(monkeypatch)
    assert [p["comment_ids"] for p in loop["posted"]] == [["1"], ["2"]]


@pytest.mark.unit
def test_main_exception_on_one_item_does_not_stop_others(monkeypatch, loop):
    loop["pending"]["replies"] = [item("1"), item("2")]
    loop["outcomes"] = {"1": TimeoutError("textbox")}
    run(monkeypatch)
    assert [p["comment_ids"] for p in loop["posted"]] == [["2"]]


@pytest.mark.unit
def test_main_keyboard_interrupt_is_not_swallowed(monkeypatch, loop):
    loop["pending"]["replies"] = [item("1"), item("2")]
    loop["outcomes"] = {"1": KeyboardInterrupt()}
    with pytest.raises(KeyboardInterrupt):
        run(monkeypatch)
    assert loop["posted"] == [] and loop["page_closed"]


@pytest.mark.unit
@pytest.mark.parametrize("answer", [{"ok": False, "error": "bad token"}, {"ok": True, "marked": 0}])
def test_main_warns_when_mark_replied_fails(monkeypatch, loop, capsys, answer):
    """Fixed D-04: the mark_replied answer used to be printed and never checked. The loop still
    continues (the reply is already on Facebook), but the gap is now reported."""
    loop["pending"]["replies"] = [item("1"), item("2")]
    monkeypatch.setattr(replies, "sheet_post", lambda payload: answer)
    run(monkeypatch)
    err = capsys.readouterr().err
    assert "1 اتنشر بس ماتعلّمش posted" in err and "2 اتنشر بس ماتعلّمش posted" in err


def test_main_no_warning_when_marked(monkeypatch, loop, capsys):
    loop["pending"]["replies"] = [item("1")]
    run(monkeypatch)
    assert "ماتعلّمش" not in capsys.readouterr().err


@pytest.mark.unit
def test_main_mark_replied_http_failure_aborts_remaining(monkeypatch, loop):
    """Characterization: an HTTP error while marking propagates and stops the loop
    (the reply was already posted on Facebook but stays pending in the sheet)."""
    loop["pending"]["replies"] = [item("1"), item("2")]
    done = []

    def failing(payload):
        done.append(payload)
        raise urllib.error.URLError("down")
    monkeypatch.setattr(replies, "sheet_post", failing)
    with pytest.raises(urllib.error.URLError):
        run(monkeypatch)
    assert len(done) == 1 and loop["page_closed"]


@pytest.mark.unit
def test_main_debug_dumps_failures_only(monkeypatch, loop):
    loop["pending"]["replies"] = [item("1"), item("2"), item("3")]
    loop["outcomes"] = {"2": "no-reply-button", "3": RuntimeError("x")}
    run(monkeypatch, "--debug")
    (rec,) = FakeRecorder.instances
    assert rec.label == "replies" and rec.trace is False and rec.har is False
    assert rec.marks == ["comment 1", "comment 2", "comment 3"]
    assert rec.dumps == ["no-reply-button-2", "error-3"]
    assert rec.detached


@pytest.mark.unit
def test_main_debug_trace_enables_har_too(monkeypatch, loop):
    loop["pending"]["replies"] = [item("1")]
    run(monkeypatch, "--debug", "--trace")
    assert FakeRecorder.instances[0].trace and FakeRecorder.instances[0].har


@pytest.mark.unit
def test_main_without_debug_no_recorder(monkeypatch, loop):
    loop["pending"]["replies"] = [item("1")]
    run(monkeypatch, "--trace")
    assert FakeRecorder.instances == []


# ---------------------------------------------------------------- post_one against the simulator

def setup_post(fake_fb, **kw) -> Post:
    cs = kw.pop("cs", None) or comments(3)
    post = Post(P, comments=cs, sort_mode="newest", replies_hidden=False, **kw)
    fake_fb.add_group(Group(G), [post])
    return post


def sent(page):
    return page.evaluate("window.__sent")


@pytest.mark.browser
def test_post_one_types_reply_and_submits_with_enter(page, fake_fb):
    """The @mention Facebook puts in the box stays in front of the reply (owner's choice 2026-10-22)."""
    post = setup_post(fake_fb)
    target = post.comments[1].id
    assert replies.post_one(page, item(target, "شكرًا لك 🌙")) == "posted"
    assert sent(page) == [{"comment_id": target, "text": f"{post.comments[1].author} شكرًا لك 🌙",
                           "url": f"https://www.facebook.com/groups/{G}/posts/{P}/?comment_id={target}"}]
    assert wait_for(page, lambda: len(fake_fb.replies) == 1)
    assert fake_fb.replies[0]["comment_id"] == target


@pytest.mark.browser
def test_post_one_navigates_to_item_permalink(page, fake_fb):
    post = setup_post(fake_fb)
    it = item(post.comments[0].id)
    replies.post_one(page, it)
    assert fake_fb.navigations()[0] == it["permalink"]


@pytest.mark.browser
def test_post_one_reply_to_a_reply(page, fake_fb):
    cs = comments(2)
    cs[0].replies = [Comment(id="880000000000001")]
    setup_post(fake_fb, cs=cs)
    it = item("880000000000001")
    it["permalink"] = f"https://www.facebook.com/groups/{G}/posts/{P}/?comment_id={cs[0].id}&reply_comment_id=880000000000001"
    assert replies.post_one(page, it) == "posted"
    assert sent(page)[0]["comment_id"] == "880000000000001"


@pytest.mark.browser
def test_post_one_english_reply_button(page, fake_fb):
    post = setup_post(fake_fb, lang="en")
    assert replies.post_one(page, item(post.comments[0].id)) == "posted"


@pytest.mark.browser
@pytest.mark.parametrize("permalink", ["", None])
def test_post_one_missing_permalink_does_not_navigate(page, fake_fb, permalink):
    it = item("1")
    it["permalink"] = permalink
    assert replies.post_one(page, it) == "missing-permalink"
    assert fake_fb.requests == []


@pytest.mark.browser
def test_post_one_missing_target_comment(page, fake_fb):
    setup_post(fake_fb)
    it = item("123456789")
    assert replies.post_one(page, it) == "no-comment"
    assert sent(page) == []


@pytest.mark.browser
def test_post_one_missing_reply_button(page, fake_fb):
    post = setup_post(fake_fb, reply_button=False)
    assert replies.post_one(page, item(post.comments[0].id)) == "no-reply-button"
    assert sent(page) == []


@pytest.mark.browser
@pytest.mark.parametrize("composer", [None, "top", "bottom"])
def test_post_one_no_reply_box_types_nothing(page, fake_fb, composer):
    """No reply box opened: return no-reply-box, and never fall back to the public comment box."""
    post = setup_post(fake_fb, reply_box=False, composer=composer)
    assert replies.post_one(page, item(post.comments[0].id)) == "no-reply-box"
    assert sent(page) == []


@pytest.mark.browser
def test_post_one_navigation_failure_raises(page, fake_fb, context):
    context.unroute("**/*")
    context.route("**/*", lambda route: route.abort("connectionrefused"))
    with pytest.raises(Exception) as exc:
        replies.post_one(page, item("1"))
    assert "ERR_CONNECTION_REFUSED" in str(exc.value)


@pytest.mark.browser
def test_post_one_unconfirmed_when_reply_never_shows(page, fake_fb, monkeypatch):
    """Fixed D-03: Facebook ignoring the Enter used to return 'posted' (and the Sheet row got marked)."""
    monkeypatch.setattr(replies, "CONFIRM_MS", 1000)
    post = setup_post(fake_fb, reply_submit="ignore")
    assert replies.post_one(page, item(post.comments[0].id)) == "unconfirmed"
    assert sent(page) == []


@pytest.mark.browser
@pytest.mark.parametrize("composer", ["bottom", "top", None])
def test_post_one_types_into_the_new_reply_box_not_the_public_composer(page, fake_fb, composer):
    """Fixed D-12, confirmed on the live layout 2026-10-21: «اكتب تعليقًا عامًا» sits at the bottom of the
    post dialog, so 'last textbox on the page' posted the reply as a new public comment."""
    post = setup_post(fake_fb, composer=composer)
    target = post.comments[1].id
    assert replies.post_one(page, item(target, "شكرا")) == "posted"
    assert sent(page) == [{"comment_id": target, "text": f"{post.comments[1].author} شكرا",
                           "url": f"https://www.facebook.com/groups/{G}/posts/{P}/?comment_id={target}"}]


@pytest.mark.browser
def test_post_one_exact_comment_id_match(page, fake_fb):
    """Fixed D-01: matching used href.includes('comment_id=' + id), so id '123' replied to the
    comment '1234' that appeared earlier in the DOM."""
    cs = [Comment(id="1234"), Comment(id="123")]
    setup_post(fake_fb, cs=cs)
    assert replies.post_one(page, item("123")) == "posted"
    assert sent(page)[0]["comment_id"] == "123"


@pytest.mark.browser
def test_post_one_parent_id_does_not_match_its_replies_links(page, fake_fb):
    """Replying to a parent must target the parent, not a reply whose link also carries comment_id=parent."""
    cs = [Comment(id="500", replies=[Comment(id="501")])]
    setup_post(fake_fb, cs=cs)
    page.goto("about:blank")
    assert replies.post_one(page, item("500")) == "posted"
    assert sent(page)[0]["comment_id"] == "500"


@pytest.mark.browser
def test_post_one_only_prefix_present_is_no_comment(page, fake_fb):
    setup_post(fake_fb, cs=[Comment(id="1234")])
    assert replies.post_one(page, item("123")) == "no-comment"


@pytest.mark.browser
def test_post_one_multiline_reply_text(page, fake_fb):
    """Characterization: the text is inserted as-is (after the mention); only the final Enter key submits."""
    post = setup_post(fake_fb, mention=False)
    replies.post_one(page, item(post.comments[0].id, "سطر 1\nسطر 2"))
    assert sent(page)[0]["text"] == "سطر 1\nسطر 2"


@pytest.mark.browser
def test_post_one_clicks_reply_with_synthetic_click(page, fake_fb):
    """Characterization: the 'رد' button is clicked from JS (isTrusted == false); the textbox click is real."""
    post = setup_post(fake_fb)
    page.goto("about:blank")
    page.add_init_script("""window.__trusted = [];
        document.addEventListener('click', e => window.__trusted.push(e.isTrusted), true);""")
    replies.post_one(page, item(post.comments[0].id))
    assert page.evaluate("window.__trusted") == [False, True]


# ---------------------------------------------------------------- live run 2026-10-22: reply box, mention, D-03, leaving

@pytest.mark.browser
@pytest.mark.parametrize("target_index", [0, 1, 2])
def test_post_one_uses_the_prerendered_reply_box_of_its_own_comment(page, fake_fb, target_index):
    """Live 2026-10-22: the reply composer exists before the click, so 'a new textbox' found nothing
    (no-reply-box). Every comment has its own hidden composer; the target's must be the one used."""
    post = setup_post(fake_fb, reply_box_prerendered=True, composer="bottom")
    target = post.comments[target_index]
    assert replies.post_one(page, item(target.id, "صباحك فل")) == "posted"
    assert sent(page) == [{"comment_id": target.id, "text": f"{target.author} صباحك فل",
                           "url": f"https://www.facebook.com/groups/{G}/posts/{P}/?comment_id={target.id}"}]


@pytest.mark.browser
def test_post_one_prerendered_box_after_existing_replies(page, fake_fb):
    cs = comments(2)
    cs[0].replies = [Comment(id="880000000000001", text="رد قديم")]
    setup_post(fake_fb, cs=cs, reply_box_prerendered=True)
    assert replies.post_one(page, item(cs[0].id, "تمام")) == "posted"
    assert sent(page)[0]["comment_id"] == cs[0].id


@pytest.mark.browser
def test_post_one_without_mention_prefill_sends_reply_alone(page, fake_fb):
    post = setup_post(fake_fb, mention=False)
    assert replies.post_one(page, item(post.comments[0].id, "شكرا")) == "posted"
    assert sent(page)[0]["text"] == "شكرا"


@pytest.mark.browser
def test_post_one_typing_failed_sends_nothing(page, fake_fb, monkeypatch):
    from playwright.sync_api import Keyboard
    monkeypatch.setattr(Keyboard, "insert_text", lambda self, text: None)
    post = setup_post(fake_fb)
    assert replies.post_one(page, item(post.comments[0].id)) == "typing-failed"
    assert sent(page) == []


@pytest.mark.browser
def test_post_one_existing_reply_is_not_posted_twice(page, fake_fb):
    """A previous run sent the reply but did not mark it (unconfirmed / mark_replied failed): no repost.
    The thread's collapsed replies are opened to look for it."""
    cs = comments(2)
    cs[0].replies = [Comment(id="880000000000001", author="أنا", author_id="100000000000777", text="أنا شكرا")]
    fake_fb.add_group(Group(G), [Post(P, comments=cs, sort_mode="newest", replies_hidden=True)])
    assert replies.post_one(page, item(cs[0].id, "شكرا")) == "already-posted"
    assert sent(page) == []
    assert f"replies:{cs[0].id}" in page.evaluate("window.__clicks")


@pytest.mark.browser
def test_post_one_same_text_by_someone_else_does_not_count(page, fake_fb, context):
    context.add_cookies([{"name": "c_user", "value": "100000000000777", "domain": ".facebook.com", "path": "/"}])
    cs = comments(2)
    cs[0].replies = [Comment(id="880000000000001", author="غيري", author_id="100000000000555", text="شكرا")]
    setup_post(fake_fb, cs=cs)
    assert replies.post_one(page, item(cs[0].id, "شكرا")) == "posted"
    assert len(sent(page)) == 1


@pytest.mark.browser
def test_post_one_confirms_reply_by_own_account(page, fake_fb, context):
    context.add_cookies([{"name": "c_user", "value": "100000000000777", "domain": ".facebook.com", "path": "/"}])
    post = setup_post(fake_fb)
    assert replies.post_one(page, item(post.comments[0].id, "شكرا")) == "posted"


@pytest.mark.browser
def test_post_one_reply_shown_under_another_account_is_unconfirmed(page, fake_fb, context, monkeypatch):
    monkeypatch.setattr(replies, "CONFIRM_MS", 1000)
    context.add_cookies([{"name": "c_user", "value": "100000000000999", "domain": ".facebook.com", "path": "/"}])
    post = setup_post(fake_fb)
    assert replies.post_one(page, item(post.comments[0].id, "شكرا")) == "unconfirmed"


def _leave_draft(fake_fb):
    """Live 2026-10-22: a reply box left with text (here: Enter ignored) asks before leaving the page."""
    post = setup_post(fake_fb, reply_submit="ignore")
    fake_fb.add_post(G, Post("78", comments=comments(1), sort_mode="newest"))
    return post


@pytest.mark.browser
def test_recorder_dismissing_the_leave_prompt_aborts_next_navigation(page, fake_fb, monkeypatch):
    """Characterization of the live failure (2026-10-22, --debug): PageRecorder dismisses every dialog,
    the dismissed beforeunload cancels the next goto (ERR_ABORTED), and every later reply fails.
    (Without any dialog listener Playwright lets the page leave.)"""
    import browser_debug

    monkeypatch.setattr(replies, "CONFIRM_MS", 500)
    post = _leave_draft(fake_fb)
    rec = browser_debug.PageRecorder(page, label="t", use_cdp=False)
    try:
        assert replies.post_one(page, item(post.comments[0].id)) == "unconfirmed"
        with pytest.raises(Exception) as exc:
            page.goto(f"https://www.facebook.com/groups/{G}/posts/78/", wait_until="domcontentloaded", timeout=10000)
        assert "ERR_ABORTED" in str(exc.value)
        assert [e["dialog_type"] for e in rec.errors if e.get("kind") == "dialog"] == ["beforeunload"]
    finally:
        rec.detach()


@pytest.mark.browser
def test_accept_leaving_before_recorder_lets_the_next_reply_load(page, fake_fb, monkeypatch):
    """Fixed: main() registers accept_leaving before the recorder, so one failed reply no longer blocks
    the next ones, and the recorder still logs the dialog."""
    import browser_debug

    monkeypatch.setattr(replies, "CONFIRM_MS", 500)
    post = _leave_draft(fake_fb)
    page.on("dialog", replies.accept_leaving)
    rec = browser_debug.PageRecorder(page, label="t", use_cdp=False)
    try:
        assert replies.post_one(page, item(post.comments[0].id)) == "unconfirmed"
        nxt = fake_fb.posts[(G, "78")].comments[0].id
        it = item(nxt)
        it["permalink"] = f"https://www.facebook.com/groups/{G}/posts/78/?comment_id={nxt}"
        assert replies.post_one(page, it) == "posted"
        assert [e["dialog_type"] for e in rec.errors if e.get("kind") == "dialog"] == ["beforeunload"]
    finally:
        rec.detach()


@pytest.mark.unit
def test_main_marks_already_posted_and_not_unconfirmed(monkeypatch, loop, capsys):
    loop["pending"]["replies"] = [item("1"), item("2"), item("3")]
    loop["outcomes"] = {"1": "already-posted", "2": "unconfirmed", "3": "typing-failed"}
    run(monkeypatch, "--debug")
    assert [p["comment_ids"] for p in loop["posted"]] == [["1"]]
    assert "2 الرد اتبعت بس ماظهرش" in capsys.readouterr().err
    assert FakeRecorder.instances[0].dumps == ["unconfirmed-2", "typing-failed-3"]


@pytest.mark.unit
def test_main_registers_dialog_handler_before_recorder(monkeypatch, loop):
    import browser_debug

    order = []

    class Page:
        def on(self, event, fn):
            order.append(("on", event, fn))

        def close(self):
            pass

    class Ctx:
        def new_page(self):
            return Page()

    class B:
        contexts = [Ctx()]

    class PW:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        class chromium:
            connect_over_cdp = staticmethod(lambda url: B())

    class Rec(FakeRecorder):
        def __init__(self, page, **k):
            order.append(("recorder",))
            super().__init__(page, **k)

    monkeypatch.setattr(replies, "sync_playwright", lambda: PW())
    monkeypatch.setattr(browser_debug, "PageRecorder", Rec)
    loop["pending"]["replies"] = [item("1")]
    run(monkeypatch, "--debug")
    assert order[0] == ("on", "dialog", replies.accept_leaving) and order[1] == ("recorder",)


@pytest.mark.unit
@pytest.mark.parametrize("kind,expected", [("beforeunload", "accept"), ("alert", "dismiss"), ("confirm", "dismiss")])
def test_accept_leaving_accepts_only_beforeunload(kind, expected):
    calls = []

    class D:
        type = kind

        def accept(self):
            calls.append("accept")

        def dismiss(self):
            calls.append("dismiss")

    replies.accept_leaving(D())
    assert calls == [expected]


@pytest.mark.browser
def test_post_one_reply_glued_to_mention_is_not_sent(page, fake_fb, monkeypatch):
    """The box must read «mention + space + reply»; text glued to the mention is not sent."""
    from playwright.sync_api import Keyboard
    real = Keyboard.insert_text

    def glued(self, text):
        page.evaluate("() => { const b = document.querySelector('[data-hermes-target]'); b.textContent = b.textContent.trimEnd(); }")
        real(self, text.lstrip())
    monkeypatch.setattr(Keyboard, "insert_text", glued)
    post = setup_post(fake_fb)
    assert replies.post_one(page, item(post.comments[0].id, "شكرا")) == "typing-failed"
    assert sent(page) == []
