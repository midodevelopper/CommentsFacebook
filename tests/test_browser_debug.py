"""browser_debug.py: PageRecorder, dump(), recording(), probe_selectors(), report formatting."""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import pytest

import browser_debug as bd
from conftest import ROOT
from fake_facebook import Comment, Group, Post, comments, wait_for

G = "4678007325765117"
DUMP_FILES = {"page.html", "page.png", "page.txt", "storage.json", "selectors.json", "selectors.txt",
              "perf.json", "console.jsonl", "errors.jsonl", "network.jsonl", "browser-log.jsonl",
              "lifecycle.jsonl", "meta.json", "summary.txt"}


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# ---------------------------------------------------------------- helpers

@pytest.mark.unit
@pytest.mark.parametrize("name, expected", [
    ("post-123", "post-123"), ("a b/c\\d:e", "a-b-c-d-e"), ("--x--", "x"), ("", "snap"),
    ("///", "snap"), ("تعليق", "تعليق"), ("v1.2_ok", "v1.2_ok"),
])
def test_safe(name, expected):
    assert bd._safe(name) == expected


@pytest.mark.unit
def test_stamp_format():
    assert re.fullmatch(r"\d{8}-\d{6}", bd._stamp())


@pytest.mark.unit
def test_write_jsonl_unicode(tmp_path):
    bd._write_jsonl(tmp_path / "x.jsonl", [{"a": "عربي"}, {"b": 2}])
    assert (tmp_path / "x.jsonl").read_text(encoding="utf-8") == '{"a": "عربي"}\n{"b": 2}\n'


@pytest.mark.unit
def test_defaults():
    assert bd.CDP.endswith(":9333") or "FB_CDP" in __import__("os").environ
    assert bd.MAX_EVENTS == 5000 or "FB_DEBUG_MAX_EVENTS" in __import__("os").environ


# ---------------------------------------------------------------- PROBES table

@pytest.mark.unit
def test_probes_are_well_formed():
    names = [p["name"] for p in bd.PROBES]
    assert len(names) == len(set(names)) == 44
    for p in bd.PROBES:
        assert p["kind"] in ("css", "role", "text")
        assert p["tier"] in ("critical", "degraded", "context")
        assert p["page"] in ("post", "feed", "any")
        if p["kind"] == "role":
            assert p["role"] and re.compile(p["selector"])
        if p["kind"] == "text":
            assert re.compile(p["selector"])
        if p["tier"] != "critical":
            assert p.get("zero_ok") or p["tier"] == "degraded"


def _js_selectors(source: str) -> set[str]:
    found = set()
    for pat in (r"querySelector(?:All)?\(\s*\"([^\"]+)\"", r"querySelector(?:All)?\(\s*'([^']+)'",
                r"querySelector(?:All)?\(\s*`([^`]+)`", r"eval_on_selector_all\(\s*\"([^\"]+)\"",
                r"locator\(\s*\"([^\"]+)\"\s*\)"):
        found |= set(re.findall(pat, source))
    return {s for s in found if "${" not in s}


@pytest.mark.unit
def test_core_selectors_are_in_probes():
    probes = {p["selector"] for p in bd.PROBES}
    for selector in ("div[role='article'][aria-label]", "a[href*='comment_id=']", "[role='textbox']",
                     "div[dir='auto']", "div[role='dialog']", "div[role='main']", "video",
                     "a[href*='/posts/'], a[href*='/permalink/']"):
        assert selector in probes, selector


@pytest.mark.unit
def test_every_selector_used_in_code_is_in_probes():
    """CLAUDE.md rule: every selector the code uses must be in PROBES (fixed gap D-13)."""
    probes = {p["selector"] for p in bd.PROBES}
    used = set()
    for name in ("fb_groups_to_sheet.py", "fb_return_replies.py"):
        used |= _js_selectors((ROOT / name).read_text(encoding="utf-8"))
    normalized = {s.replace('"', "'") for s in probes}
    # 'div' = generic scroll search; data-hermes-* = our own markers (fb_return_replies), not Facebook's DOM
    absent = {s.replace('"', "'") for s in used if "data-hermes-" not in s} - normalized - {"div"}
    assert absent == set()


# ---------------------------------------------------------------- PageRecorder: event capture (real headless page)

@pytest.mark.browser
def test_recorder_captures_console_with_source(page):
    rec = bd.PageRecorder(page, label="t")
    page.set_content("<script>console.log('hello'); console.error('bad thing'); console.warn('w')</script>")
    assert wait_for(page, lambda: len(rec.console) >= 3)
    assert [c["type"] for c in rec.console[:3]] == ["log", "error", "warning"]
    assert rec.console[1]["text"] == "bad thing"
    assert "at" in rec.console[0]
    assert rec.counts()["console_errors"] == 1 and rec.counts()["console_warnings"] == 1
    rec.detach()


@pytest.mark.browser
def test_recorder_captures_pageerror_and_cdp_exception(page):
    rec = bd.PageRecorder(page, label="t")
    page.set_content("<script>setTimeout(() => { throw new Error('kaboom') }, 0)</script>")
    assert wait_for(page, lambda: any(e["kind"] == "exceptionThrown" for e in rec.errors)
                    and any(e["kind"] == "pageerror" for e in rec.errors))
    assert any("kaboom" in e["message"] for e in rec.errors if e["kind"] == "pageerror")
    thrown = next(e for e in rec.errors if e["kind"] == "exceptionThrown")
    assert isinstance(thrown["stack"], list)
    rec.detach()


@pytest.mark.browser
def test_recorder_captures_failed_requests_and_http_errors(page, context):
    def handler(route):
        if "missing" in route.request.url:
            route.fulfill(status=404, body="nope")
        elif "blocked" in route.request.url:
            route.abort("failed")
        else:
            route.fulfill(status=200, content_type="text/html",
                          body="<img src='https://t.test/missing.png'><img src='https://t.test/blocked.png'>")
    context.route("**/*", handler)
    rec = bd.PageRecorder(page, label="t")
    page.goto("https://t.test/")
    assert wait_for(page, lambda: len(rec.network) >= 2)
    kinds = {(n["kind"], n.get("status")) for n in rec.network}
    assert ("response", 404) in kinds and ("requestfailed", None) in kinds
    failed = next(n for n in rec.network if n["kind"] == "requestfailed")
    assert failed["url"].endswith("blocked.png") and failed["resource_type"] == "image" and failed["failure"]
    rec.detach()


@pytest.mark.browser
def test_recorder_does_not_log_successful_responses(page, context):
    context.route("**/*", lambda r: r.fulfill(status=200, body="<p>ok</p>", content_type="text/html"))
    rec = bd.PageRecorder(page, label="t")
    page.goto("https://ok.test/")
    assert rec.network == []
    rec.detach()


@pytest.mark.browser
def test_recorder_dismisses_and_records_dialogs(page):
    rec = bd.PageRecorder(page, label="t")
    page.set_content("<p>x</p>")
    result = page.evaluate("() => { alert('hi'); return 'after' }")
    assert result == "after"                                 # the dialog didn't block the page
    assert {"kind": "dialog", "message": "hi", "dialog_type": "alert"}.items() <= rec.errors[0].items()
    rec.detach()


@pytest.mark.browser
def test_recorder_records_navigation_and_marks(page, context):
    context.route("**/*", lambda r: r.fulfill(status=200, body="<p>ok</p>", content_type="text/html"))
    rec = bd.PageRecorder(page, label="t")
    rec.mark("before")
    page.goto("https://nav.test/a")
    kinds = [(e["kind"], e.get("note") or e.get("url")) for e in rec.lifecycle]
    assert ("mark", "before") in kinds and ("navigated", "https://nav.test/a") in kinds
    rec.detach()


@pytest.mark.browser
def test_recorder_captures_cdp_browser_log(page, context):
    context.route("**/*", lambda r: r.fulfill(status=404, body="x") if "x.js" in r.request.url
                  else r.fulfill(status=200, content_type="text/html", body="<script src='https://l.test/x.js'></script>"))
    rec = bd.PageRecorder(page, label="t")
    page.goto("https://l.test/")
    assert wait_for(page, lambda: len(rec.browser_log) >= 1)
    assert rec.browser_log[0]["source"] == "network" and rec.browser_log[0]["level"] == "error"
    rec.detach()


@pytest.mark.browser
def test_recorder_without_cdp(page):
    rec = bd.PageRecorder(page, label="t", use_cdp=False)
    assert rec._cdp is None
    rec.detach()


@pytest.mark.browser
def test_detach_stops_capture_and_is_idempotent(page):
    rec = bd.PageRecorder(page, label="t")
    rec.detach()
    page.evaluate("console.log('after detach')")
    page.wait_for_timeout(50)
    assert rec.console == []
    rec.detach()
    assert rec._attached is False


@pytest.mark.browser
def test_attach_twice_registers_handlers_once(page):
    rec = bd.PageRecorder(page, label="t")
    rec.attach()
    page.evaluate("console.log('once')")
    assert wait_for(page, lambda: rec.console)
    page.wait_for_timeout(50)
    assert len(rec.console) == 1
    rec.detach()


@pytest.mark.unit
def test_event_buffer_is_capped(monkeypatch):
    monkeypatch.setattr(bd, "MAX_EVENTS", 3)
    rec = bd.PageRecorder(page=None, attach=False)
    for i in range(5):
        rec._push(rec.console, {"i": i})
    assert [r["i"] for r in rec.console] == [2, 3, 4]


# ---------------------------------------------------------------- dump()

@pytest.mark.browser
def test_dump_writes_every_artifact(page, fake_fb, isolated_output_dirs):
    fake_fb.add_group(Group(G), [Post("77", comments=comments(2), sort_mode="newest")])
    rec = bd.PageRecorder(page, label="post 77")
    page.goto(f"https://www.facebook.com/groups/{G}/posts/77/")
    page.evaluate("console.error('x')")
    out = rec.dump("failed/now", exc=ValueError("broken selector"))
    assert out.parent == isolated_output_dirs / "debug"
    assert out.name.endswith("-post-77-failed-now")
    assert DUMP_FILES <= {p.name for p in out.iterdir()}
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["label"] == "post 77" and meta["reason"] == "failed/now"
    assert meta["url"] == f"https://www.facebook.com/groups/{G}/posts/77/"
    assert meta["exception"]["type"] == "ValueError" and "broken selector" in meta["exception"]["traceback"]
    assert meta["screenshot"] == "full_page"
    assert meta["viewport"] == {"width": 1200, "height": 900}
    assert "تعليق رقم 1" in (out / "page.txt").read_text(encoding="utf-8")
    assert "comment_id=" in (out / "page.html").read_text(encoding="utf-8")
    assert (out / "page.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    summary = (out / "summary.txt").read_text(encoding="utf-8")
    assert summary.startswith("# post 77 / failed/now") and "exception: ValueError" in summary
    assert "last console errors:" in summary
    assert rec.dumps == [out]
    sel = json.loads((out / "selectors.json").read_text(encoding="utf-8"))
    assert sel["page_kind"] == "post"
    rec.detach()


@pytest.mark.browser
def test_dump_never_saves_cookie_values(page, context, isolated_output_dirs):
    secret = "SESSION-SECRET-VALUE-7f3a9c"
    context.add_cookies([{"name": "c_user", "value": secret, "domain": ".facebook.com", "path": "/"},
                         {"name": "xs", "value": secret, "domain": ".facebook.com", "path": "/"}])
    context.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html",
                                              body="<p>logged in</p><script>localStorage.setItem('k','v')</script>"))
    rec = bd.PageRecorder(page, label="c")
    page.goto("https://www.facebook.com/")
    out = rec.dump("cookies")
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["cookie_count"] == 2
    assert meta["cookie_names"] == [".facebook.comc_user", ".facebook.comxs"]
    for f in out.iterdir():
        assert secret.encode() not in f.read_bytes(), f.name
    assert json.loads((out / "storage.json").read_text(encoding="utf-8"))["local"] == {"k": "v"}
    rec.detach()


class _DeadPage:
    """Every page call raises: a crashed or closed page."""

    def __getattr__(self, name):
        raise RuntimeError(f"page is dead ({name})")


@pytest.mark.unit
def test_dump_never_raises_even_when_page_is_dead(isolated_output_dirs):
    rec = bd.PageRecorder(_DeadPage(), label="dead", attach=False)
    rec._push(rec.console, {"type": "error", "text": "last words"})
    out = rec.dump("crash", exc=RuntimeError("x"))
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["url"].startswith("<unavailable:") and meta["title"].startswith("<unavailable:")
    assert "evaluate failed" in meta["html_error"]
    assert meta["screenshot"].startswith("failed:")
    assert "selectors_error" not in meta          # probe_selectors swallows per-probe errors itself
    assert {"console.jsonl", "meta.json", "summary.txt"} <= {p.name for p in out.iterdir()}
    assert jsonl(out / "console.jsonl")[0]["text"] == "last words"


@pytest.mark.browser
def test_dump_on_closed_page(context, isolated_output_dirs):
    p = context.new_page()
    rec = bd.PageRecorder(p, label="closed")
    p.close()
    out = rec.dump("after-close")
    assert (out / "summary.txt").exists()


@pytest.mark.browser
def test_dump_records_trace_chunks_and_har(page, context, isolated_output_dirs):
    context.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body="<p>trace me</p>"))
    rec = bd.PageRecorder(page, label="tr", trace=True, har=True)
    assert rec._trace_on and rec._har_path is not None
    page.goto("https://trace.test/")
    first = rec.dump("one")
    page.goto("https://trace.test/two")
    second = rec.dump("two")
    for out in (first, second):
        with zipfile.ZipFile(out / "trace.zip") as z:
            assert any(n.endswith(".trace") for n in z.namelist())
    har = json.loads((first / "network.har").read_text(encoding="utf-8"))
    assert any(e["request"]["url"] == "https://trace.test/" for e in har["log"]["entries"])
    assert not (second / "network.har").exists()           # HAR closes at the first dump
    meta = json.loads((first / "meta.json").read_text(encoding="utf-8"))
    assert meta["trace"] == "trace.zip" and meta["har"] == "network.har"
    rec.detach()


@pytest.mark.unit
def test_trace_and_har_unavailable_are_recorded_not_raised(isolated_output_dirs):
    rec = bd.PageRecorder(_DeadPage(), label="x", attach=False, trace=True, har=True)
    assert rec._trace_on is False and rec._har_path is None
    assert {e["kind"] for e in rec.lifecycle} == {"trace-unavailable", "har-unavailable"}


# ---------------------------------------------------------------- recording()

@pytest.mark.browser
@pytest.mark.parametrize("exc_type", [RuntimeError, SystemExit])
def test_recording_dumps_on_exception_and_reraises(page, isolated_output_dirs, exc_type):
    with pytest.raises(exc_type):
        with bd.recording(page, "ctx") as rec:
            raise exc_type("boom")
    (out,) = rec.dumps
    assert out.name.endswith("-ctx-exception")
    assert rec._attached is False


@pytest.mark.browser
def test_recording_no_dump_on_success_unless_dump_always(page, isolated_output_dirs):
    with bd.recording(page, "ok") as rec:
        pass
    assert rec.dumps == []
    with bd.recording(page, "ok", dump_always=True) as rec:
        pass
    assert rec.dumps[0].name.endswith("-ok-ok")


# ---------------------------------------------------------------- probe_selectors / format_selector_report

@pytest.mark.browser
def test_probe_on_healthy_post_page(page, fake_fb):
    cs = comments(3)
    fake_fb.add_group(Group(G), [Post("77", comments=cs)])
    page.goto(f"https://www.facebook.com/groups/{G}/posts/77/")
    report = bd.probe_selectors(page)
    assert report["page_kind"] == "post"
    counts = {r["name"]: r["count"] for r in report["probes"]}
    assert counts["comment.article"] == 3 and counts["reply.button"] == 3 and counts["sort.opener"] == 1
    assert report["critical_zero"] == [] and report["sort_broken"] is False
    assert report["zero_match"] is report["critical_zero"] and report["zero_count"] == 0
    assert "كل الـ selectors الأساسية سليمة." in bd.format_selector_report(report)


@pytest.mark.browser
def test_probe_flags_broken_critical_and_sort(page, fake_fb):
    # genuinely broken: no comments rendered AND no "no comments yet" text (D-32 would excuse that)
    fake_fb.add_group(Group(G), [Post("77", comments=[], sort_mode="none", no_comments_text=False)])
    page.goto(f"https://www.facebook.com/groups/{G}/posts/77/")
    report = bd.probe_selectors(page)
    assert {"comment.article", "comment.permalink", "reply.button"} <= set(report["critical_zero"])
    assert report["sort_broken"] is True
    assert "sort.* (لا حالة ولا زرار فتح)" in report["critical_zero"]
    text = bd.format_selector_report(report)
    assert "مكسور — ده بيوقف الشغل:" in text and "!! comment.article" in text


@pytest.mark.browser
def test_probe_feed_kind_ignores_post_only_probes(page, fake_fb):
    fake_fb.add_group(Group(G), [Post("1")])
    page.goto(f"https://www.facebook.com/groups/{G}")
    report = bd.probe_selectors(page)
    assert report["page_kind"] == "feed"
    applies = {r["name"]: r["applies"] for r in report["probes"]}
    assert applies["comment.article"] is False and applies["feed.post_links"] is True
    assert report["critical_zero"] == []
    assert "(مش بتاع الصفحة دي)" in bd.format_selector_report(report)


@pytest.mark.unit
@pytest.mark.parametrize("url, kind", [
    ("https://www.facebook.com/groups/1/posts/2/", "post"),
    ("https://www.facebook.com/groups/1/permalink/2/", "post"),
    ("https://www.facebook.com/groups/1", "feed"),
    ("https://www.facebook.com/", "any"),
    ("about:blank", "any"),
])
def test_probe_page_kind_inference(url, kind):
    class P:
        def __init__(self):
            self.url = url

        def locator(self, s):
            raise RuntimeError("no")

        get_by_role = locator
    report = bd.probe_selectors(P())
    assert report["page_kind"] == kind
    assert all(r["count"] == -1 for r in report["probes"])
    assert len(report["errors"]) == len(bd.PROBES)
    assert "probes رمت استثناء" in bd.format_selector_report(report)


@pytest.mark.unit
def test_probe_explicit_page_kind_overrides_url():
    class P:
        url = "https://www.facebook.com/groups/1/posts/2/"

        def locator(self, s):
            class L:
                def count(self):
                    return 0
            return L()

        def get_by_role(self, role, name=None):
            return self.locator("")
    assert bd.probe_selectors(P(), page_kind="feed")["page_kind"] == "feed"


@pytest.mark.unit
def test_format_report_degraded_section():
    report = {"page_kind": "post", "url": "u", "critical_zero": [], "degraded_zero": ["post.message"],
              "errors": [], "probes": [{"name": "post.message", "count": 0, "tier": "degraded", "applies": True,
                                        "used_by": "visible_snapshot", "selector": "s", "zero_ok": "fallback"}]}
    text = bd.format_selector_report(report)
    assert " ~ post.message" in text and "~ post.message: fallback" in text


@pytest.mark.browser
def test_successful_traced_session_without_dump_loses_trace_and_leaves_loose_har(page, context, isolated_output_dirs):
    """Characterization (defect D-16): the scripts only dump on failure. With --debug --trace and no
    failure, detach() stops tracing without a path (trace discarded) and the HAR is written loose
    into debug/ with no summary next to it."""
    context.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body="<p>ok</p>"))
    rec = bd.PageRecorder(page, label="scrape", trace=True, har=True)
    page.goto("https://ok.test/")
    rec.detach()
    debug_dir = isolated_output_dirs / "debug"
    assert [p.suffix for p in debug_dir.iterdir()] == [".har"]
    assert not list(debug_dir.rglob("trace.zip"))


@pytest.mark.browser
def test_dump_brings_page_to_front_before_screenshot(context, isolated_output_dirs):
    """Fixed D-17: on the live headed browser a background tab made both screenshot attempts time out."""
    first, second = context.new_page(), context.new_page()
    rec = bd.PageRecorder(first, label="bg")
    first.set_content("<p>x</p>")
    second.bring_to_front()
    out = rec.dump("bg")
    assert first.evaluate("document.visibilityState") == "visible"
    assert (out / "page.png").exists()
    rec.detach()
    second.close()
    first.close()


@pytest.mark.unit
def test_dump_writes_logs_meta_and_summary_before_slow_artifacts(isolated_output_dirs, monkeypatch):
    """Crash-safety (seen live: a kill during a 130 MB trace write lost meta/summary/jsonl).
    The cheap files must exist before HTML, screenshot, trace and HAR are attempted."""
    seen = {}

    class Page:
        url = "https://x/"
        viewport_size = None

        def title(self):
            return "t"

        def content(self):
            out = rec_out[0]
            seen["at_html"] = sorted(p.name for p in out.iterdir())
            raise RuntimeError("stop here")

        def __getattr__(self, name):
            raise RuntimeError(name)

    rec_out = []
    rec = bd.PageRecorder(Page(), label="order", attach=False)
    real_mkdir = Path.mkdir

    def spy_mkdir(self, *a, **k):
        real_mkdir(self, *a, **k)
        if self.name.endswith("-order-x"):
            rec_out.append(self)
    monkeypatch.setattr(Path, "mkdir", spy_mkdir)
    rec.dump("x")
    assert {"console.jsonl", "errors.jsonl", "network.jsonl", "browser-log.jsonl", "lifecycle.jsonl",
            "meta.json", "summary.txt"} <= set(seen["at_html"])


@pytest.mark.browser
def test_perf_counters_cover_the_session(page, isolated_output_dirs):
    """Fixed: Performance.enable used to be called at dump time, so durations were always 0."""
    rec = bd.PageRecorder(page, label="perf")
    page.set_content("<div id=x></div><script>for (let i=0;i<300;i++){document.getElementById('x').append(document.createElement('p'))}</script>")
    page.evaluate("() => { for (let i = 0; i < 50; i++) { document.body.style.width = (100 + i) + 'px'; document.body.offsetWidth; } }")
    out = rec.dump("perf")
    perf = json.loads((out / "perf.json").read_text(encoding="utf-8"))
    assert perf["LayoutCount"] > 0
    rec.detach()



@pytest.mark.browser
def test_cdp_exception_keeps_the_real_message(page):
    """Fixed D-28 (seen live): Runtime.exceptionThrown was stored as just 'Uncaught'."""
    rec = bd.PageRecorder(page, label="t")
    page.set_content("<script>setTimeout(() => { throw new TypeError('real reason here') }, 0)</script>")
    assert wait_for(page, lambda: any(e["kind"] == "exceptionThrown" for e in rec.errors))
    thrown = next(e for e in rec.errors if e["kind"] == "exceptionThrown")
    assert "TypeError: real reason here" in thrown["message"]
    rec.detach()


@pytest.mark.browser
def test_probe_no_comments_post_is_not_reported_broken(page, fake_fb):
    """Fixed D-32 (seen live: 8 of 11 healthy posts reported critical): Facebook's 'no comments yet'
    makes zero comment/reply/sort selectors expected."""
    fake_fb.add_group(Group(G), [Post("77", comments=[], sort_mode="none")])
    page.goto(f"https://www.facebook.com/groups/{G}/posts/77/")
    report = bd.probe_selectors(page)
    assert report["no_comments"] is True
    assert report["critical_zero"] == [] and report["sort_broken"] is False


@pytest.mark.browser
def test_probe_still_flags_zero_comments_without_the_text(page, fake_fb):
    fake_fb.add_group(Group(G), [Post("77", comments=[], sort_mode="none", no_comments_text=False)])
    page.goto(f"https://www.facebook.com/groups/{G}/posts/77/")
    report = bd.probe_selectors(page)
    assert report["no_comments"] is False and "comment.article" in report["critical_zero"]


@pytest.mark.unit
@pytest.mark.parametrize("text, noise", [
    ("Error with Permissions-Policy header: Unrecognized feature: 'interest-cohort'.", True),
    ("Permissions policy violation: unload is not allowed in this document.", True),
    ("Minified React error #418; visit https://react.dev/errors/418", True),
    ("net::ERR_ABORTED https://scontent.fjed2-1.fna.fbcdn.net/o1/v/t2/f2/m366/x", True),
    ("TypeError: Cannot read properties of undefined", False),
    ("net::ERR_ABORTED https://www.facebook.com/api/graphql/", False),
    ("", False),
    (None, False),
])
def test_known_noise(text, noise):
    assert bd.is_known_noise(text) is noise


@pytest.mark.unit
def test_summary_counts_noise_and_lists_only_anomalies(isolated_output_dirs):
    rec = bd.PageRecorder(page=None, label="n", attach=False)
    rec._push(rec.console, {"type": "error", "text": "Error with Permissions-Policy header: Unrecognized feature: 'x'."})
    rec._push(rec.console, {"type": "error", "text": "TypeError: boom"})
    rec._push(rec.errors, {"kind": "pageerror", "message": "Minified React error #418; visit"})
    text = rec._summary({"url": "u", "title": "t", "saved_at": "s", "elapsed_sec": 1, "counts": rec.counts()}, "r", None)
    assert "known Facebook noise (not listed, still in the .jsonl files): 2" in text
    assert "TypeError: boom" in text and "Permissions-Policy" not in text and "page errors:" not in text



@pytest.mark.unit
@pytest.mark.parametrize("text", [
    "%cانتبه! font-family:helvetica; font-size:20px;",
    "%cStop! font-family:helvetica;",
    "يمكنك الاطلاع على https://www.facebook.com/selfxss للحصول على مزيد من المعلومات.",
    "net::ERR_FAILED chrome-extension://invalid/",
])
def test_known_noise_round3(text):
    assert bd.is_known_noise(text) is True


@pytest.mark.browser
def test_har_omits_response_bodies_by_default(page, context, isolated_output_dirs):
    """Live run 5: HAR with bodies was 140–426 MB per group. Status/timing/headers are kept."""
    body = "<p>" + ("x" * 50000) + "</p>"
    context.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=body))
    rec = bd.PageRecorder(page, label="har", har=True)
    page.goto("https://har.test/")
    out = rec.dump("har")
    har = json.loads((out / "network.har").read_text(encoding="utf-8"))
    entry = next(e for e in har["log"]["entries"] if e["request"]["url"] == "https://har.test/")
    assert entry["response"]["status"] == 200 and entry["time"] >= 0
    assert "text" not in entry["response"]["content"]
    assert (out / "network.har").stat().st_size < 50000
    rec.detach()
