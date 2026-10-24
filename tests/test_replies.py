"""fb_return_replies.py (API v2): config, run_one outcomes, journal replay, main() loop, and post_one()
against the simulator.

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


# ---------------------------------------------------------------- constants / config (API v2)

@pytest.mark.unit
def test_no_deployment_url_or_token_in_reply_source():
    source = (ROOT / "fb_return_replies.py").read_text(encoding="utf-8")
    assert "script.google.com/macros" not in source and "CommentSecrets" not in source and "AKfy" not in source
    assert replies.CDP == os.environ.get("FB_CDP", "http://127.0.0.1:9333")


@pytest.mark.unit
def test_load_config_prefers_environment(monkeypatch):
    monkeypatch.setenv("FB_SHEET_WEBAPP", "https://example.test/exec")
    monkeypatch.setenv("FB_SHEET_ADMIN_KEY", "admin-k")
    monkeypatch.setenv("FB_SHEET_ID", "sheet-1")
    assert replies.load_config() == ("https://example.test/exec", "admin-k", "sheet-1")


@pytest.mark.unit
def test_load_config_refuses_when_incomplete(monkeypatch, tmp_path):
    for k in ("FB_SHEET_WEBAPP", "FB_SHEET_ADMIN_KEY", "FB_SHEET_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(replies, "ROOT", tmp_path)
    with pytest.raises(SystemExit):
        replies.load_config()


@pytest.mark.cli
def test_cli_help_lists_flags():
    out = subprocess.run([PYTHON, "fb_return_replies.py", "--help"], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8", timeout=60, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert out.returncode == 0
    for flag in ("--cdp", "--debug", "--trace", "--max-replies", "--dry-run", "--allow-post-replies"):
        assert flag in out.stdout


# ---------------------------------------------------------------- fake v2 API client

class FakeClient:
    """Records calls and answers like the v2 receiver. errors: {action: ApiError} raised once per entry."""

    def __init__(self, eligible=None, blocked=None):
        self.calls = []
        self.eligible = eligible or []
        self.blocked = blocked or []
        self.errors: dict[str, list] = {}
        self.fence = 0

    def call(self, action, payload=None, target=None, request_id=None, budget_ms=240000):
        self.calls.append((action, payload or {}, target))
        queued = self.errors.get(action)
        if queued:
            raise queued.pop(0)
        if action == "pending_replies":
            return {"ok": True, "result": {"eligible": list(self.eligible), "blocked": list(self.blocked)}}
        if action == "claim":
            self.fence += 1
            it = next(e for e in self.eligible if e["id"] == payload["id"])
            return {"ok": True, "result": {"claim_id": f"claim-{payload['id']}-{self.fence}", "fence": self.fence,
                                           "binding": {"reply_sha256": "x"}, "target": dict(it)}}
        return {"ok": True, "result": {}}

    def actions(self):
        return [c[0] for c in self.calls]

    def marks(self):
        return [(c[1]["id"], c[1]["to"], c[1].get("expected_status")) for c in self.calls if c[0] == "mark_status"]


def target(cid, reply="شكرا", kind="comment", author_id="100000000000005"):
    return {"kind": kind, "id": cid, "post_id": P, "group_id": G, "parent_id": "",
            "url": f"https://www.facebook.com/groups/{G}/posts/{P}/?comment_id={cid}", "author": "a",
            "author_id": author_id, "reply": reply}


def item(cid, reply="شكرا"):
    """v1-shaped item still accepted by post_one (comment_id + permalink)."""
    return {"comment_id": cid, "post_id": P, "group_id": G, "parent_id": "",
            "permalink": f"https://www.facebook.com/groups/{G}/posts/{P}/?comment_id={cid}", "author": "a",
            "reply": reply}


ME = "100000000000777"


@pytest.fixture
def journal(tmp_path):
    return replies.Journal(tmp_path / "journal.jsonl")


def fake_post_one(outcome, reply_id="7000000000001"):
    def post_one(page, rec, gate=None):
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome in ("posted", "unconfirmed", "gate-refused", "boom-after-submit"):
            if gate is not None and not gate():
                return "gate-refused"
            rec["_submitted"] = True
            if outcome == "boom-after-submit":
                raise RuntimeError("after enter")
        if outcome in ("posted", "already-posted"):
            rec["_reply"] = {"id": reply_id, "time": "24 أكتوبر 2026 الساعة 11:00 ص"}
        return outcome
    return post_one


# ---------------------------------------------------------------- run_one: every outcome

@pytest.mark.unit
def test_run_one_posted_marks_sent_with_the_reply_id_and_closes_the_journal(monkeypatch, journal):
    client = FakeClient([target("1")])
    monkeypatch.setattr(replies, "post_one", fake_post_one("posted"))
    assert replies.run_one(None, client, "S", target("1"), ME, journal, {"count": 0}) == "posted"
    assert client.actions() == ["claim", "prepare_submit", "mark_status"]
    mark = client.calls[-1][1]
    assert mark["to"] == "sent" and mark["expected_status"] == "sending" and mark["sent_reply_id"] == "7000000000001"
    assert mark["sent_at"].startswith("2026-10-24T11:00:00")
    events = [json.loads(l)["event"] for l in journal.path.read_text(encoding="utf-8").splitlines()]
    assert events == ["intent", "authorized", "submitted", "confirmed", "marked"]
    assert journal.open_attempts() == []


@pytest.mark.unit
def test_run_one_already_posted_marks_sent_without_submitting(monkeypatch, journal):
    client = FakeClient([target("1")])
    monkeypatch.setattr(replies, "post_one", fake_post_one("already-posted"))
    assert replies.run_one(None, client, "S", target("1"), ME, journal, {"count": 0}) == "already-posted"
    assert "prepare_submit" not in client.actions()
    assert client.marks() == [("1", "sent", "sending")]


@pytest.mark.unit
def test_run_one_unconfirmed_marks_uncertain_never_failed(monkeypatch, journal):
    client = FakeClient([target("1")])
    monkeypatch.setattr(replies, "post_one", fake_post_one("unconfirmed"))
    assert replies.run_one(None, client, "S", target("1"), ME, journal, {"count": 0}) == "unconfirmed"
    assert client.marks() == [("1", "uncertain", "sending")]


@pytest.mark.unit
def test_run_one_refused_prepare_releases_without_pressing_enter(monkeypatch, journal):
    from tools.plan_exec.client import ApiError

    client = FakeClient([target("1")])
    client.errors["prepare_submit"] = [ApiError("E_DNC", "author on do-not-contact")]
    monkeypatch.setattr(replies, "post_one", fake_post_one("posted"))
    assert replies.run_one(None, client, "S", target("1"), ME, journal, {"count": 0}) == "gate-refused"
    assert client.actions() == ["claim", "prepare_submit", "release_claim"]
    assert "E_DNC" in client.calls[-1][1]["note"]


@pytest.mark.unit
@pytest.mark.parametrize("status", ["no-comment", "no-reply-button", "no-reply-box", "typing-failed", "not-a-member", "missing-permalink"])
def test_run_one_failures_before_enter_mark_failed(monkeypatch, journal, status):
    client = FakeClient([target("1")])
    monkeypatch.setattr(replies, "post_one", fake_post_one(status))
    assert replies.run_one(None, client, "S", target("1"), ME, journal, {"count": 0}) == status
    assert client.marks() == [("1", "failed", "sending")]


@pytest.mark.unit
def test_run_one_exception_before_enter_releases_the_claim(monkeypatch, journal):
    client = FakeClient([target("1")])
    monkeypatch.setattr(replies, "post_one", fake_post_one(TimeoutError("textbox")))
    with pytest.raises(TimeoutError):
        replies.run_one(None, client, "S", target("1"), ME, journal, {"count": 0})
    assert client.actions() == ["claim", "release_claim"]


@pytest.mark.unit
def test_run_one_exception_after_enter_marks_uncertain(monkeypatch, journal):
    client = FakeClient([target("1")])
    monkeypatch.setattr(replies, "post_one", fake_post_one("boom-after-submit"))
    with pytest.raises(RuntimeError):
        replies.run_one(None, client, "S", target("1"), ME, journal, {"count": 0})
    assert client.marks() == [("1", "uncertain", "sending")]


@pytest.mark.unit
def test_run_one_keyboard_interrupt_is_not_swallowed(monkeypatch, journal):
    client = FakeClient([target("1")])
    monkeypatch.setattr(replies, "post_one", fake_post_one(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        replies.run_one(None, client, "S", target("1"), ME, journal, {"count": 0})
    assert client.actions() == ["claim", "release_claim"]


@pytest.mark.unit
def test_run_one_renews_the_lease_when_the_claim_is_old(monkeypatch, journal):
    client = FakeClient([target("1")])
    monkeypatch.setattr(replies, "post_one", fake_post_one("posted"))
    monkeypatch.setattr(replies, "RENEW_AFTER_S", -1)
    replies.run_one(None, client, "S", target("1"), ME, journal, {"count": 0})
    assert client.actions() == ["claim", "renew", "prepare_submit", "mark_status"]


@pytest.mark.unit
def test_run_one_counts_create_comment_requests_after_authorization(monkeypatch, journal):
    client = FakeClient([target("1")])
    counter = {"count": 3}

    def post_one(page, rec, gate=None):
        assert gate()
        counter["count"] += 1          # what the page request listener does for the create mutation
        rec["_submitted"] = True
        rec["_reply"] = {"id": "7000000000001", "time": ""}
        return "posted"
    monkeypatch.setattr(replies, "post_one", post_one)
    replies.run_one(None, client, "S", target("1"), ME, journal, counter)
    marked = [json.loads(l) for l in journal.path.read_text(encoding="utf-8").splitlines()][-1]
    assert marked["mutations"] == 1


# ---------------------------------------------------------------- journal replay: four separate paths

def open_attempt(journal, last, **extra):
    base = {"kind": "comment", "id": "1", "claim_id": "c1", "fence": 1, "url": target("1")["url"], "reply": "شكرا",
            "account_id": ME}
    journal.write("c1", "intent", **base)
    order = ["authorized", "submitted", "confirmed"]
    for event in order[:order.index(last) + 1] if last in order else []:
        journal.write("c1", event, **(extra if event == last else {}))


@pytest.mark.unit
def test_replay_intent_only_releases_the_claim(journal):
    open_attempt(journal, "intent")
    client = FakeClient()
    assert replies.replay(None, client, "S", journal, ME) == [("1", "released")]
    assert client.actions() == ["release_claim"]
    assert journal.open_attempts() == []


@pytest.mark.unit
def test_replay_confirmed_marks_sent_with_the_journal_reply(journal):
    open_attempt(journal, "confirmed", reply={"id": "7000000000001", "time": ""})
    client = FakeClient()
    assert replies.replay(None, client, "S", journal, ME) == [("1", "sent")]
    assert client.calls[0][1]["sent_reply_id"] == "7000000000001"


@pytest.mark.unit
@pytest.mark.parametrize("last", ["authorized", "submitted"])
def test_replay_submitted_reconciles_and_marks_sent_when_found(monkeypatch, journal, last):
    open_attempt(journal, last)
    monkeypatch.setattr(replies, "open_target", lambda page, rec: "")
    monkeypatch.setattr(replies, "expand_thread", lambda page, cid: 0)
    monkeypatch.setattr(replies, "find_own_reply", lambda page, cid, reply, me, exclude=None: {"id": "7000000000002", "time": ""})
    client = FakeClient()
    assert replies.replay(None, client, "S", journal, ME) == [("1", "sent")]
    assert client.calls[0][1]["to"] == "sent"


@pytest.mark.unit
def test_replay_submitted_not_found_becomes_uncertain_never_pending_or_failed(monkeypatch, journal):
    open_attempt(journal, "submitted")
    monkeypatch.setattr(replies, "open_target", lambda page, rec: "")
    monkeypatch.setattr(replies, "expand_thread", lambda page, cid: 0)
    monkeypatch.setattr(replies, "find_own_reply", lambda *a, **k: None)
    client = FakeClient()
    assert replies.replay(None, client, "S", journal, ME) == [("1", "uncertain")]
    assert [c[0] for c in client.calls] == ["mark_status"]


@pytest.mark.unit
def test_replay_marked_attempts_are_left_alone(journal):
    open_attempt(journal, "confirmed", reply={"id": "7", "time": ""})
    journal.write("c1", "marked", outcome="posted")
    client = FakeClient()
    assert replies.replay(None, client, "S", journal, ME) == [] and client.calls == []


@pytest.mark.unit
def test_replay_tries_uncertain_when_the_lease_already_expired(journal):
    from tools.plan_exec.client import ApiError

    open_attempt(journal, "confirmed", reply={"id": "7000000000001", "time": ""})
    client = FakeClient()
    client.errors["mark_status"] = [ApiError("E_CONFLICT", "status is uncertain, expected sending")]
    assert replies.replay(None, client, "S", journal, ME) == [("1", "sent")]
    assert [c[1]["expected_status"] for c in client.calls] == ["sending", "uncertain"]


# ---------------------------------------------------------------- main()

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
def loop(monkeypatch, tmp_path):
    import browser_debug

    state = {"client": FakeClient(), "order": [], "page_closed": False, "cookies": [{"name": "c_user", "value": ME}],
             "run": [], "dry": [], "slept": []}

    class Ctx:
        def cookies(self, url=None):
            return state["cookies"]

        def new_page(self):
            return Page()

    class Page:
        context = Ctx()

        def on(self, event, fn):
            state["order"].append(("on", event, fn))

        def close(self):
            state["page_closed"] = True

    class PW:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        class chromium:
            @staticmethod
            def connect_over_cdp(url):
                state["cdp"] = url

                class B:
                    contexts = [Ctx()]
                return B()

    def run_one(page, client, sheet, item, me, journal, counter, recorder=None):
        state["run"].append(item["id"])
        outcome = state.get("outcomes", {}).get(item["id"], "posted")
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(replies, "sync_playwright", lambda: PW())
    monkeypatch.setattr(replies, "load_config", lambda: ("https://x/exec", "k", "S"))
    monkeypatch.setattr(replies, "Client", lambda url, key: state["client"])
    monkeypatch.setattr(replies, "JOURNAL", tmp_path / "journal.jsonl")
    monkeypatch.setattr(replies, "run_one", run_one)
    monkeypatch.setattr(replies, "dry_check", lambda page, item, me: state["dry"].append(item["id"]) or "dry-ok")
    monkeypatch.setattr(replies.time, "sleep", state["slept"].append)
    monkeypatch.setattr(replies.scraper, "check_plan_lock", lambda: None)
    FakeRecorder.instances = []
    monkeypatch.setattr(browser_debug, "PageRecorder", FakeRecorder)
    return state


def run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["fb_return_replies.py", *argv])
    return replies.main()


@pytest.mark.unit
def test_main_sweeps_then_lists_pending_for_our_account(monkeypatch, loop):
    run(monkeypatch, "--cdp", "http://127.0.0.1:1")
    assert loop["client"].actions()[:2] == ["sweep_restrictions", "pending_replies"]
    assert loop["client"].calls[1][1] == {"account_id": ME} and loop["client"].calls[1][2] == "S"
    assert loop["cdp"] == "http://127.0.0.1:1" and loop["page_closed"]


@pytest.mark.unit
def test_main_without_c_user_stops_with_account_exit(monkeypatch, loop):
    loop["cookies"] = []
    with pytest.raises(SystemExit) as exc:
        run(monkeypatch)
    assert exc.value.code == 3
    assert loop["client"].calls == []


@pytest.mark.unit
def test_main_respects_max_replies_and_paces_between_replies(monkeypatch, loop):
    loop["client"].eligible = [target(str(i)) for i in range(1, 5)]
    run(monkeypatch, "--max-replies", "3")
    assert loop["run"] == ["1", "2", "3"]
    assert len(loop["slept"]) == 2 and all(20 <= s <= 60 for s in loop["slept"])


@pytest.mark.unit
def test_main_dry_run_never_claims(monkeypatch, loop):
    loop["client"].eligible = [target("1"), target("2")]
    run(monkeypatch, "--dry-run")
    assert loop["dry"] == ["1", "2"] and loop["run"] == []
    assert "claim" not in loop["client"].actions()


@pytest.mark.unit
def test_main_skips_post_targets_unless_the_code_flag_is_given(monkeypatch, loop):
    loop["client"].eligible = [target("1"), target("9", kind="post")]
    run(monkeypatch)
    assert loop["run"] == ["1"]
    loop["run"].clear()
    run(monkeypatch, "--allow-post-replies")
    assert loop["run"] == ["1", "9"]


@pytest.mark.unit
def test_main_api_refusal_on_one_item_does_not_stop_others(monkeypatch, loop, capsys):
    from tools.plan_exec.client import ApiError

    loop["client"].eligible = [target("1"), target("2")]
    loop["outcomes"] = {"1": ApiError("E_CONFLICT", "claimed")}
    run(monkeypatch)
    assert loop["run"] == ["1", "2"]
    assert "1 skipped E_CONFLICT" in capsys.readouterr().out


@pytest.mark.unit
def test_main_account_blocked_mid_run_exits_3(monkeypatch, loop):
    loop["client"].eligible = [target("1"), target("2")]
    loop["outcomes"] = {"1": replies.scraper.AccountBlocked("redirected to https://www.facebook.com/checkpoint")}
    with pytest.raises(SystemExit) as exc:
        run(monkeypatch)
    assert exc.value.code == 3 and loop["run"] == ["1"]


@pytest.mark.unit
def test_main_warns_on_unconfirmed(monkeypatch, loop, capsys):
    loop["client"].eligible = [target("1")]
    loop["outcomes"] = {"1": "unconfirmed"}
    run(monkeypatch)
    assert "uncertain" in capsys.readouterr().err


@pytest.mark.unit
def test_main_debug_recorder_marks_items(monkeypatch, loop):
    loop["client"].eligible = [target("1"), target("2")]
    run(monkeypatch, "--debug")
    (rec,) = FakeRecorder.instances
    assert rec.label == "replies" and rec.marks == ["comment 1", "comment 2"] and rec.detached


@pytest.mark.unit
def test_main_without_debug_no_recorder(monkeypatch, loop):
    loop["client"].eligible = [target("1")]
    run(monkeypatch, "--trace")
    assert FakeRecorder.instances == []


@pytest.mark.unit
def test_main_registers_dialog_handler_before_recorder(monkeypatch, loop):
    import browser_debug

    class Rec(FakeRecorder):
        def __init__(self, page, **k):
            loop["order"].append(("recorder",))
            super().__init__(page, **k)

    monkeypatch.setattr(browser_debug, "PageRecorder", Rec)
    run(monkeypatch, "--debug")
    assert loop["order"][0] == ("on", "dialog", replies.accept_leaving)
    assert ("recorder",) in loop["order"]
    assert loop["order"].index(("on", "dialog", replies.accept_leaving)) < loop["order"].index(("recorder",))


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
def test_post_one_reply_to_a_reply_is_not_confirmed_without_nesting_evidence(page, fake_fb, monkeypatch):
    """D32: under a reply target, «same thread + mention» can't tell which comment we answered. Our new reply
    counts only when the page nests it inside the target; the simulator renders it flat, so it stays
    unconfirmed (and the v2 sheet refuses nested targets anyway)."""
    monkeypatch.setattr(replies, "CONFIRM_MS", 1000)
    cs = comments(2)
    cs[0].replies = [Comment(id="880000000000001")]
    setup_post(fake_fb, cs=cs)
    it = item("880000000000001")
    it["permalink"] = f"https://www.facebook.com/groups/{G}/posts/{P}/?comment_id={cs[0].id}&reply_comment_id=880000000000001"
    assert replies.post_one(page, it) == "unconfirmed"
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
    cs[0].replies = [Comment(id="880000000000001", author="أنا", author_id="100000000000777", text=f"{cs[0].author} شكرا")]
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


# ---------------------------------------------------------------- reply run 3 fixes (2026-10-22)

@pytest.mark.browser
def test_post_one_uses_the_box_that_received_the_mention_not_the_first_visible(page, fake_fb):
    """Reply run 3: a visible empty composer of a nested sub-thread sat right after the target; the reply went
    into it (Facebook showed it as a reply to someone else). The box the click filled with the mention wins."""
    post = setup_post(fake_fb, reply_box_prerendered=True, nested_box_first=True, composer="bottom")
    target = post.comments[0]
    assert replies.post_one(page, item(target.id, "تمام")) == "posted"
    assert sent(page) == [{"comment_id": target.id, "text": f"{target.author} تمام",
                           "url": f"https://www.facebook.com/groups/{G}/posts/{P}/?comment_id={target.id}"}]
    assert page.evaluate(f"document.querySelector('[data-for=\"{target.id}-nested\"]').textContent") == ""


@pytest.mark.browser
def test_post_one_finds_a_target_beyond_the_first_page_of_comments(page, fake_fb):
    """Reply run 3: the post opened with 2 of 29 comments shown and the target was not among them (no-comment)."""
    cs = comments(4)
    setup_post(fake_fb, cs=cs, page_size=1, permalink_focus=False)
    assert replies.post_one(page, item(cs[3].id, "أهلًا")) == "posted"
    assert sent(page)[0]["comment_id"] == cs[3].id


@pytest.mark.browser
def test_post_one_still_no_comment_when_the_target_is_really_gone(page, fake_fb):
    setup_post(fake_fb, page_size=1)
    assert replies.post_one(page, item("123456789")) == "no-comment"
    assert sent(page) == []


# ---------------------------------------------------------------- v2 strict matching and journal location

@pytest.mark.unit
def test_journal_default_path_follows_the_module_setting(monkeypatch, tmp_path):
    """A test once wrote into the real dumps/replies/journal.jsonl because the default was bound at import."""
    monkeypatch.setattr(replies, "JOURNAL", tmp_path / "j.jsonl")
    assert replies.Journal().path == tmp_path / "j.jsonl"


@pytest.mark.browser
def test_post_one_longer_reply_containing_our_text_is_not_ours(page, fake_fb):
    """Substring matching is gone: our account's longer reply that merely contains the text is not «already posted»."""
    cs = comments(2)
    cs[0].replies = [Comment(id="880000000000001", author="أنا", author_id="100000000000777",
                             text=f"{cs[0].author} شكرا جزيلا على كل شيء")]
    fake_fb.add_group(Group(G), [Post(P, comments=cs, sort_mode="newest", replies_hidden=False)])
    assert replies.post_one(page, item(cs[0].id, "شكرا")) == "posted"
    assert len(sent(page)) == 1


@pytest.mark.browser
def test_post_one_same_text_under_another_target_does_not_count(page, fake_fb):
    """Our identical reply under a different comment of the same post is not a reply to this target."""
    cs = comments(2)
    cs[1].replies = [Comment(id="880000000000002", author="أنا", author_id="100000000000777", text=f"{cs[1].author} شكرا")]
    fake_fb.add_group(Group(G), [Post(P, comments=cs, sort_mode="newest", replies_hidden=False)])
    assert replies.post_one(page, item(cs[0].id, "شكرا")) == "posted"
    assert sent(page)[0]["comment_id"] == cs[0].id


@pytest.mark.browser
def test_find_own_reply_returns_the_new_reply_id_and_ignores_preexisting_ones(page, fake_fb):
    post = setup_post(fake_fb)
    target_id = post.comments[0].id
    before = None

    def gate():
        nonlocal before
        before = replies.reply_ids(page, target_id)
        return True
    it = item(target_id, "أهلا")
    assert replies.post_one(page, it, gate) == "posted"
    assert it["_reply"]["id"] not in before and it["_submitted"] is True


@pytest.mark.browser
def test_post_one_gate_refusal_presses_no_enter(page, fake_fb):
    post = setup_post(fake_fb)
    it = item(post.comments[0].id, "أهلا")
    assert replies.post_one(page, it, lambda: False) == "gate-refused"
    assert sent(page) == [] and "_submitted" not in it
