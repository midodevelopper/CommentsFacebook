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
def test_token_ignores_fb_sheet_token_env():
    """Characterization (defect D-05): unlike the scraper, the reply script hardcodes TOKEN."""
    code = "import fb_return_replies as m; print(m.TOKEN)"
    out = subprocess.run([PYTHON, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60,
                         env={**os.environ, "FB_SHEET_TOKEN": "from-env"})
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
def test_main_ignores_mark_replied_failure(monkeypatch, loop):
    """Characterization (defect D-04): the mark_replied answer is printed, never checked."""
    loop["pending"]["replies"] = [item("1")]
    monkeypatch.setattr(replies, "sheet_post", lambda payload: {"ok": False, "error": "bad token"})
    run(monkeypatch)    # no exception, nothing retried


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
    post = setup_post(fake_fb)
    target = post.comments[1].id
    assert replies.post_one(page, item(target, "شكرًا لك 🌙")) == "posted"
    assert sent(page) == [{"comment_id": target, "text": "شكرًا لك 🌙",
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
def test_post_one_editor_never_appears_raises(page, fake_fb):
    """No composer anywhere: the textbox click times out (5 s) and the exception propagates."""
    post = setup_post(fake_fb, reply_box=False, composer=None)
    with pytest.raises(Exception) as exc:
        replies.post_one(page, item(post.comments[0].id))
    assert "Timeout" in type(exc.value).__name__ or "Timeout" in str(exc.value)


@pytest.mark.browser
def test_post_one_navigation_failure_raises(page, fake_fb, context):
    context.unroute("**/*")
    context.route("**/*", lambda route: route.abort("connectionrefused"))
    with pytest.raises(Exception) as exc:
        replies.post_one(page, item("1"))
    assert "ERR_CONNECTION_REFUSED" in str(exc.value)


@pytest.mark.browser
def test_post_one_reports_posted_without_verifying_publication(page, fake_fb):
    """Characterization (defect D-03): Facebook ignoring the Enter still returns 'posted'."""
    post = setup_post(fake_fb, reply_submit="ignore")
    assert replies.post_one(page, item(post.comments[0].id)) == "posted"
    assert sent(page) == []


@pytest.mark.browser
def test_post_one_uses_last_textbox_on_page(page, fake_fb):
    """Characterization (risk D-12): the reply goes into the LAST role=textbox on the page.
    If the main comment composer sits below the reply box, the reply is typed there instead."""
    post = setup_post(fake_fb, composer="bottom")
    target = post.comments[0].id
    assert replies.post_one(page, item(target)) == "posted"
    assert sent(page)[0]["comment_id"] == "post"


@pytest.mark.browser
def test_post_one_comment_id_prefix_collision(page, fake_fb):
    """Characterization (defect D-01): matching uses href.includes('comment_id=' + id), so id '123'
    matches a comment whose id is '1234…' that appears earlier in the DOM."""
    cs = [Comment(id="1234"), Comment(id="123")]
    setup_post(fake_fb, cs=cs)
    assert replies.post_one(page, item("123")) == "posted"
    assert sent(page)[0]["comment_id"] == "1234"


@pytest.mark.browser
def test_post_one_multiline_reply_text(page, fake_fb):
    """Characterization: fill() inserts the text as-is; only the final Enter key submits."""
    post = setup_post(fake_fb)
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
