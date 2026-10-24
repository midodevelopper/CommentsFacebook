"""Full offline workflow (API v2):

  launcher detects browser -> scrape group (real fb_groups_to_sheet.main) -> HTTP -> real v2 receiver
  (apps_script/src under Node, in-memory sheet at schema v2) -> owner types replies -> real
  fb_return_replies.main -> claim / prepare_submit / mark_status -> simulated Facebook -> rescrape keeps replies.

Nothing here reaches Facebook, Google, or the real browser: the scripts get a headless context with the
Facebook simulator routed in, and the web-app URL and keys point at loopback test values.
"""
from __future__ import annotations

import json
import re
import sys

import pytest

import fb_groups_to_sheet as scraper
import fb_return_replies as replier
import launch_browser
from fake_facebook import Comment, Group, Post, comments, wait_for
from tools.plan_exec.client import ApiError

pytestmark = pytest.mark.e2e

G = "4678007325765117"


@pytest.fixture
def world(monkeypatch, fake_fb, fake_playwright, webapp, isolated_output_dirs):
    import playwright.sync_api

    monkeypatch.setattr(playwright.sync_api, "sync_playwright", fake_playwright)
    monkeypatch.setattr(replier, "sync_playwright", fake_playwright)
    monkeypatch.setattr(scraper, "WEBAPP", webapp.exec_url)
    monkeypatch.setattr(scraper, "INGEST_KEY", webapp.INGEST_KEY)
    monkeypatch.setattr(replier, "load_config", lambda: (webapp.exec_url, webapp.ADMIN_KEY, webapp.SHEET_ID))
    monkeypatch.setattr(replier, "JOURNAL", isolated_output_dirs / "dumps" / "replies" / "journal.jsonl")
    monkeypatch.setattr(scraper, "check_plan_lock", lambda: None)
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)

    c1 = comments(4, start=1)
    c1[1].replies = [Comment(id="880000000000001", text="رد على التعليق")]
    c2 = comments(3, start=20)
    fake_fb.add_group(Group(G, page_size=1), [
        Post("1111111111111111", comments=c1, page_size=2),
        Post("2222222222222222", comments=c2, message="منشور تاني"),
    ])
    return {"fb": fake_fb, "pw": fake_playwright, "sheet": webapp, "c1": c1, "c2": c2,
            "dumps": isolated_output_dirs / "dumps", "debug": isolated_output_dirs / "debug"}


def scrape(monkeypatch, *extra):
    monkeypatch.setattr(sys, "argv", ["fb_groups_to_sheet.py", "--group", f"https://www.facebook.com/groups/{G}",
                                      "--min-posts", "1", "--max-posts", "5", "--min-comments", "0",
                                      "--max-comments", "50", "--batch-size", "3", "--cdp", "http://127.0.0.1:9999",
                                      *extra])
    scraper.main()


def reply(monkeypatch, *extra):
    monkeypatch.setattr(sys, "argv", ["fb_return_replies.py", "--cdp", "http://127.0.0.1:9999",
                                      "--min-gap", "0", "--max-gap", "0", *extra])
    replier.main()


def test_browser_startup_step_reuses_running_browser(monkeypatch, http_server):
    """Step 1: the launcher sees a live CDP endpoint and does not start a second browser."""
    http_server.respond(body={"Browser": "Chrome/141"})
    started = []
    monkeypatch.setattr(launch_browser.subprocess, "Popen", lambda *a, **k: started.append(a))
    assert launch_browser.launch(http_server.port) == 0
    assert started == []


def test_full_workflow(monkeypatch, world, capsys):
    fb, sheet = world["fb"], world["sheet"]

    # ---- scrape: read-only against Facebook, upsert into the sheet
    scrape(monkeypatch)
    assert world["pw"].cdp_urls == ["http://127.0.0.1:9999"]
    assert fb.replies == []                                            # scraping never writes to Facebook
    assert fb.navigations()[0].endswith("?sorting_setting=CHRONOLOGICAL")

    posts = sheet.rows("Posts")
    assert [p["post_id"] for p in posts] == ["1111111111111111", "2222222222222222"]
    assert all(p["comment_sort"] == "الأحدث" and p["group_id"] == G for p in posts)
    assert posts[1]["text"] == "منشور تاني" and posts[1]["media_url"] == ""
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", p["received_at"]) for p in posts)

    rows = {r["comment_id"]: r for r in sheet.rows("Comments")}
    expected = {c.id for c in world["c1"] + world["c2"]} | {"880000000000001"}
    assert set(rows) == expected                                        # every comment once, ids exact
    r = rows["880000000000001"]
    assert (r["is_reply"], r["parent_id"], r["reply_target_id"]) == (True, world["c1"][1].id, "880000000000001")
    assert r["post_id"] == "1111111111111111" and r["post_text"] == posts[0]["text"]
    assert r["url"].endswith(f"comment_id={world['c1'][1].id}&reply_comment_id=880000000000001")
    assert all(v["reply"] == "" and v["reply_status"] == "" for v in rows.values())

    out = capsys.readouterr().out
    # الإرسال في الخلفية: كل تعليق اتبعت مرة، ومفيش طلب أكبر من --batch-size
    sent = [int(n) for n in re.findall(r"^sheet: \d+ posts / (\d+) comments: \{\"ok\":true", out, re.M)]
    assert sum(sent) == 8 and sent and max(sent) <= 3
    dump = json.loads(next(world["dumps"].glob(f"{G}-*.json")).read_text(encoding="utf-8"))
    assert dump["posts_found"] == 2 and dump["posts"][0]["comments_found"] == 5

    # ---- owner writes replies in the sheet (API edit: no onEdit); one target will disappear from Facebook
    ok_target = world["c1"][0].id
    gone_target = world["c2"][2].id
    sheet.set_cell("Comments", ok_target, "reply", "شكرًا على تعليقك")
    sheet.set_cell("Comments", gone_target, "reply", "رد على تعليق اتمسح")
    fb.posts[(G, "2222222222222222")].comments = world["c2"][:2]

    # ---- publish replies through claim / prepare_submit / mark_status
    reply(monkeypatch)
    out = capsys.readouterr().out
    assert "eligible 2" in out
    assert f"{ok_target} posted" in out and f"{gone_target} no-comment" in out
    assert wait_for(world["pw"].context.pages[0] if world["pw"].context.pages else _page(world),
                    lambda: len(fb.replies) == 1)
    assert fb.replies[0]["comment_id"] == ok_target
    # the @mention Facebook puts in the box stays in front of the reply (owner's choice 2026-10-22)
    assert fb.replies[0]["text"] == f"{world['c1'][0].author} شكرًا على تعليقك"

    rows = {r["comment_id"]: r for r in sheet.rows("Comments")}
    assert rows[ok_target]["reply_status"] == "sent"
    assert rows[gone_target]["reply_status"] == "failed"                # failure before Enter is failed, not sent
    ledger = sheet.rows("_replies")
    sent_events = [e for e in ledger if e["status_to"] == "sent"]
    assert len(sent_events) == 1 and sent_events[0]["target_id"] == ok_target and sent_events[0]["sent_reply_id"]
    assert [e["event"] for e in ledger if e["target_id"] == ok_target][:2] == ["claim", "submit_authorized"]
    journal = [json.loads(l) for l in replier.JOURNAL.read_text(encoding="utf-8").splitlines()]
    assert {j["event"] for j in journal if j.get("id") == ok_target or True} >= {"intent", "authorized", "confirmed", "marked"}

    # ---- running again does not repost: sent is not eligible and failed needs an owner retry
    fb.replies.clear()
    reply(monkeypatch)
    assert "eligible 0" in capsys.readouterr().out
    assert fb.replies == []

    # ---- rescrape keeps replies and statuses, adds nothing twice
    scrape(monkeypatch)
    rows = {r["comment_id"]: r for r in sheet.rows("Comments")}
    assert rows[ok_target]["reply"] == "شكرًا على تعليقك" and rows[ok_target]["reply_status"] == "sent"
    assert rows[gone_target]["reply"] == "رد على تعليق اتمسح" and rows[gone_target]["reply_status"] == "failed"
    assert len(sheet.rows("Comments")) == len(expected)
    assert len(sheet.rows("Posts")) == 2


def _page(world):
    return world["pw"].context.new_page()


def test_dry_run_sends_nothing(monkeypatch, world):
    scrape(monkeypatch, "--dry")
    assert world["sheet"].rows("Posts") == [] and world["sheet"].rows("Comments") == []
    assert list(world["dumps"].glob(f"{G}-*.json"))


def test_reply_dry_run_claims_nothing_and_types_nothing(monkeypatch, world, capsys):
    scrape(monkeypatch)
    target = world["c1"][0].id
    world["sheet"].set_cell("Comments", target, "reply", "x")
    reply(monkeypatch, "--dry-run")
    assert f"{target} dry-ok" in capsys.readouterr().out
    assert world["fb"].replies == []
    assert {r["comment_id"]: r for r in world["sheet"].rows("Comments")}[target]["reply_status"] == "pending"
    assert not [e for e in world["sheet"].rows("_replies") if e["event"] == "claim"]


def test_sort_failure_stops_run_before_sending_and_debug_captures_it(monkeypatch, world):
    world["fb"].posts[(G, "1111111111111111")].sort_mode = "no-option"
    with pytest.raises(SystemExit) as exc:
        scrape(monkeypatch, "--debug")
    assert "no-option" in str(exc.value)
    assert world["sheet"].rows("Comments") == []                         # nothing half-sent for that run
    (folder,) = list(world["debug"].iterdir())
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert meta["exception"]["type"] == "SystemExit"
    assert "/posts/1111111111111111/" in meta["url"]
    assert (folder / "page.html").stat().st_size > 0


def test_reply_failures_are_dumped_with_debug_and_marked_failed(monkeypatch, world):
    scrape(monkeypatch)
    target = world["c1"][0].id
    world["sheet"].set_cell("Comments", target, "reply", "x")
    world["fb"].posts[(G, "1111111111111111")].reply_button = False
    reply(monkeypatch, "--debug")
    folders = [f.name for f in world["debug"].iterdir()]
    assert any(name.endswith(f"-replies-no-reply-button-{target}") for name in folders)
    assert {r["comment_id"]: r for r in world["sheet"].rows("Comments")}[target]["reply_status"] == "failed"


def test_wrong_ingest_key_stops_the_scraper(monkeypatch, world):
    """Fixed D-02 end to end: a refused write used to be printed and ignored for the whole run."""
    monkeypatch.setattr(scraper, "INGEST_KEY", "wrong-key")
    with pytest.raises(SystemExit) as exc:
        scrape(monkeypatch)
    assert "E_AUTH" in str(exc.value)
    assert world["sheet"].rows("Posts") == []


def test_wrong_admin_key_stops_reply_script_before_facebook(monkeypatch, world):
    monkeypatch.setattr(replier, "load_config", lambda: (world["sheet"].exec_url, "wrong", world["sheet"].SHEET_ID))
    with pytest.raises(ApiError) as exc:
        reply(monkeypatch)
    assert exc.value.code == "E_AUTH"
    assert world["fb"].replies == []
