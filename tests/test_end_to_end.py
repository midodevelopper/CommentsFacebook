"""Full offline workflow:

  launcher detects browser -> scrape group (real fb_groups_to_sheet.main) -> HTTP -> real
  facebook_sheet_receiver.gs (Node, in-memory sheet) -> owner types replies -> real
  fb_return_replies.main -> simulated Facebook -> mark_replied -> rescrape preserves replies.

Nothing here reaches Facebook, Google, or the real browser: the scripts get a
headless context with the Facebook simulator routed in, and WEBAPP points at loopback.
"""
from __future__ import annotations

import json
import sys

import pytest

import fb_groups_to_sheet as scraper
import fb_return_replies as replier
import launch_browser
from fake_facebook import Comment, Group, Post, comments, wait_for

pytestmark = pytest.mark.e2e

G = "4678007325765117"


@pytest.fixture
def world(monkeypatch, fake_fb, fake_playwright, webapp, isolated_output_dirs):
    import playwright.sync_api

    monkeypatch.setattr(playwright.sync_api, "sync_playwright", fake_playwright)
    monkeypatch.setattr(replier, "sync_playwright", fake_playwright)
    monkeypatch.setattr(scraper, "WEBAPP", webapp.exec_url)
    monkeypatch.setattr(replier, "WEBAPP", webapp.exec_url)
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
    monkeypatch.setattr(sys, "argv", ["fb_return_replies.py", "--cdp", "http://127.0.0.1:9999", *extra])
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
    assert posts[1]["post content"] == "منشور تاني"

    rows = {r["comment_id"]: r for r in sheet.rows("Comments")}
    expected = {c.id for c in world["c1"] + world["c2"]} | {"880000000000001"}
    assert set(rows) == expected                                        # every comment once, ids exact
    r = rows["880000000000001"]
    assert (r["is_reply"], r["parent_id"], r["reply_target_id"]) == ("yes", world["c1"][1].id, "880000000000001")
    assert r["post_id"] == "1111111111111111"
    assert r["permalink"].endswith(f"comment_id={world['c1'][1].id}&reply_comment_id=880000000000001")
    assert all(isinstance(v["comment_id"], str) for v in rows.values())

    out = capsys.readouterr().out
    assert "sheet batch 1/2 n=3" in out and "sheet batch 2/2 n=2" in out and "sheet batch 1/1 n=3" in out
    dump = json.loads(next(world["dumps"].glob(f"{G}-*.json")).read_text(encoding="utf-8"))
    assert dump["posts_found"] == 2 and dump["posts"][0]["comments_found"] == 5

    # ---- owner writes replies in the sheet; one target will have disappeared from Facebook
    ok_target = world["c1"][0].id
    gone_target = world["c2"][2].id
    sheet.set_cell("Comments", ok_target, "reply", "شكرًا على تعليقك")
    sheet.set_cell("Comments", gone_target, "reply", "رد على تعليق اتمسح")
    fb.posts[(G, "2222222222222222")].comments = world["c2"][:2]

    # ---- publish replies
    reply(monkeypatch)
    out = capsys.readouterr().out
    assert "pending 2" in out
    assert f"{ok_target} posted" in out and f"{gone_target} no-comment" in out
    assert wait_for(world["pw"].context.pages[0] if world["pw"].context.pages else _page(world),
                    lambda: len(fb.replies) == 1)
    assert fb.replies[0]["comment_id"] == ok_target and fb.replies[0]["text"] == "شكرًا على تعليقك"

    rows = {r["comment_id"]: r for r in sheet.rows("Comments")}
    assert rows[ok_target]["reply_status"] == "posted"
    assert rows[gone_target]["reply_status"] == ""                     # failure is not marked posted
    pending = replier.sheet_get("pending_replies")["replies"]
    assert [p["comment_id"] for p in pending] == [gone_target]         # failed reply stays pending

    # ---- running again does not repost the published reply
    fb.replies.clear()
    reply(monkeypatch)
    assert "pending 1" in capsys.readouterr().out
    assert fb.replies == []

    # ---- rescrape keeps replies and statuses, adds nothing twice
    scrape(monkeypatch)
    rows = {r["comment_id"]: r for r in sheet.rows("Comments")}
    assert rows[ok_target]["reply"] == "شكرًا على تعليقك" and rows[ok_target]["reply_status"] == "posted"
    assert rows[gone_target]["reply"] == "رد على تعليق اتمسح"
    assert len(sheet.rows("Comments")) == len(expected)
    assert len(sheet.rows("Posts")) == 2


def _page(world):
    return world["pw"].context.new_page()


def test_dry_run_sends_nothing(monkeypatch, world):
    scrape(monkeypatch, "--dry")
    assert world["sheet"].state() == {}
    assert list(world["dumps"].glob(f"{G}-*.json"))


def test_sort_failure_stops_run_before_sending_and_debug_captures_it(monkeypatch, world):
    world["fb"].posts[(G, "1111111111111111")].sort_mode = "no-option"
    with pytest.raises(SystemExit) as exc:
        scrape(monkeypatch, "--debug")
    assert "no-option" in str(exc.value)
    assert world["sheet"].state() == {}                                 # nothing half-sent for that run
    (folder,) = list(world["debug"].iterdir())
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert meta["exception"]["type"] == "SystemExit"
    assert "/posts/1111111111111111/" in meta["url"]
    assert (folder / "page.html").stat().st_size > 0


def test_reply_failures_are_dumped_with_debug(monkeypatch, world):
    scrape(monkeypatch)
    target = world["c1"][0].id
    world["sheet"].set_cell("Comments", target, "reply", "x")
    world["fb"].posts[(G, "1111111111111111")].reply_button = False
    reply(monkeypatch, "--debug")
    (folder,) = list(world["debug"].iterdir())
    assert folder.name.endswith(f"-replies-no-reply-button-{target}")
    assert world["sheet"].rows("Comments") and \
        {r["comment_id"]: r for r in world["sheet"].rows("Comments")}[target]["reply_status"] == ""


def test_bad_token_stops_the_scraper(monkeypatch, world):
    """Fixed D-02 end to end: a wrong token used to be printed and ignored for the whole run."""
    monkeypatch.setattr(scraper, "TOKEN", "wrong-token")
    with pytest.raises(SystemExit) as exc:
        scrape(monkeypatch)
    assert "bad token" in str(exc.value)
    assert world["sheet"].state() == {}


def test_bad_token_stops_reply_script(monkeypatch, world):
    monkeypatch.setattr(replier, "TOKEN", "wrong-token")
    with pytest.raises(SystemExit) as exc:
        reply(monkeypatch)
    assert exc.value.code == {"ok": False, "error": "bad token"}
