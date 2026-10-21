"""fb_groups_to_sheet.py functions that drive the page, run against the offline
Facebook simulator in a throwaway headless Chromium.

These protect what the code does with the DOM it expects. They do not prove
Facebook's live DOM still looks like this — that is what
`browser_debug.py selectors` (and tests/test_live.py) are for.
"""
from __future__ import annotations

import re

import pytest

import fb_groups_to_sheet as scraper
from fake_facebook import Comment, Group, Post, comments

pytestmark = pytest.mark.browser

G = "4678007325765117"


def ids(cs):
    return [c.id for c in cs]


# ---------------------------------------------------------------- collect_post_ids

def test_collect_requests_chronological_feed(page, fake_fb):
    fake_fb.add_group(Group(G), [Post("1001"), Post("1002")])
    scraper.collect_post_ids(page, G, 2)
    assert fake_fb.navigations()[0] == f"https://www.facebook.com/groups/{G}?sorting_setting=CHRONOLOGICAL"


def test_collect_returns_tuple_of_ids_and_feed_label(page, fake_fb):
    """Characterization: annotated `-> list[str]` but returns (ids, label)."""
    fake_fb.add_group(Group(G, sort_label="فرز موجز المجموعة: الأحدث"), [Post("1001")])
    result = scraper.collect_post_ids(page, G, 1)
    assert result == (["1001"], "فرز موجز المجموعة: الأحدث")


def test_collect_dedupes_posts_and_permalink_links_in_dom_order(page, fake_fb):
    fake_fb.add_group(Group(G, page_size=10), [Post(str(2000 + i)) for i in range(5)])
    found, _ = scraper.collect_post_ids(page, G, 10)
    assert found == [str(2000 + i) for i in range(5)]


def test_collect_stops_at_max_posts(page, fake_fb):
    fake_fb.add_group(Group(G, page_size=10), [Post(str(2000 + i)) for i in range(8)])
    found, _ = scraper.collect_post_ids(page, G, 3)
    assert found == ["2000", "2001", "2002"]


def test_collect_scrolls_to_load_more(page, fake_fb):
    fake_fb.add_group(Group(G, page_size=2), [Post(str(3000 + i)) for i in range(7)])
    found, _ = scraper.collect_post_ids(page, G, 7)
    assert found == [str(3000 + i) for i in range(7)]


def test_collect_terminates_when_feed_is_exhausted(page, fake_fb):
    fake_fb.add_group(Group(G, page_size=2), [Post("1"), Post("2"), Post("3")])
    found, _ = scraper.collect_post_ids(page, G, 50)
    assert found == ["1", "2", "3"]


def test_collect_empty_group(page, fake_fb):
    fake_fb.add_group(Group(G, sort_label="غير ذلك"))
    assert scraper.collect_post_ids(page, G, 5) == ([], "")


def test_collect_ignores_other_groups_posts_in_feed(page, fake_fb):
    """Fixed D-08: links to another group's posts used to be collected as this group's."""
    fake_fb.add_group(Group(G, extra_links=["https://www.facebook.com/groups/999/posts/5555/"]), [Post("1")])
    found, _ = scraper.collect_post_ids(page, G, 5)
    assert found == ["1"]


def test_collect_ignores_notifications_panel(page, fake_fb):
    """D-08 as seen live 2026-10-21: the Notifications panel (banner > dialog) came first in the DOM
    and supplied 3 of 5 'post ids' from other groups; the run then failed on the first one."""
    fake_fb.add_group(Group(G, notification_links=[
        "https://www.facebook.com/groups/111111111111111/posts/222222222222222/",
        f"https://www.facebook.com/groups/{G}/posts/7777/",            # same group, but not in the feed
    ]), [Post("1"), Post("2")])
    found, _ = scraper.collect_post_ids(page, G, 5)
    assert found == ["1", "2"]


def test_collect_ignores_non_post_links(page, fake_fb):
    fake_fb.add_group(Group(G, extra_links=[
        "https://www.facebook.com/groups/G/user/123/",
        "https://www.facebook.com/groups/G/posts/notanumber/",
        "https://www.facebook.com/photo/?fbid=1",
    ]), [Post("1")])
    found, _ = scraper.collect_post_ids(page, G, 5)
    assert found == ["1"]


# ---------------------------------------------------------------- sort_comments_newest

def open_post(page, fake_fb, post: Post):
    fake_fb.add_group(Group(G), [post])
    page.goto(f"https://www.facebook.com/groups/{G}/posts/{post.post_id}/")
    return post


def clicks(page):
    return page.evaluate("window.__clicks")


@pytest.mark.parametrize("lang", ["ar", "en"])
def test_sort_selects_newest_from_most_relevant(page, fake_fb, lang):
    open_post(page, fake_fb, Post("1", comments=comments(3), lang=lang))
    assert scraper.sort_comments_newest(page) == "الأحدث"
    newest = "الأحدث" if lang == "ar" else "Newest"
    assert clicks(page) == ["sort:", f"pick:{newest}"]


def test_sort_selects_newest_from_all_comments(page, fake_fb):
    open_post(page, fake_fb, Post("1", sort_mode="all-comments"))
    assert scraper.sort_comments_newest(page) == "الأحدث"


def test_sort_already_newest_does_not_click(page, fake_fb):
    open_post(page, fake_fb, Post("1", sort_mode="newest"))
    assert scraper.sort_comments_newest(page) == "الأحدث"
    assert clicks(page) == []


def test_sort_missing_control_with_comments(page, fake_fb):
    open_post(page, fake_fb, Post("1", comments=comments(2), sort_mode="none"))
    assert scraper.sort_comments_newest(page) == "missing"


def test_sort_no_comments_post_has_no_control(page, fake_fb):
    """Fixed D-20: Facebook shows no sort control on a post with zero comments."""
    open_post(page, fake_fb, Post("1", comments=[], sort_mode="none"))
    assert scraper.sort_comments_newest(page) == "no-comments"


@pytest.mark.parametrize("lang", ["ar", "en"])
def test_sort_no_comments_text_in_both_languages(page, fake_fb, lang):
    open_post(page, fake_fb, Post("1", comments=[], sort_mode="none", lang=lang))
    assert scraper.no_comments_shown(page) is True


def test_sort_zero_comments_without_the_text_is_still_missing(page, fake_fb):
    """The strict guard stays: no control and no explicit 'no comments' text is a failure."""
    open_post(page, fake_fb, Post("1", comments=[], sort_mode="none", no_comments_text=False))
    assert scraper.sort_comments_newest(page) == "missing"


def test_sort_option_unavailable_closes_menu(page, fake_fb):
    open_post(page, fake_fb, Post("1", sort_mode="no-option"))
    assert scraper.sort_comments_newest(page) == "no-option"
    assert page.get_by_role("menuitem").count() == 0            # Escape closed it


def test_sort_unverified_when_label_does_not_change(page, fake_fb):
    open_post(page, fake_fb, Post("1", sort_mode="unverified"))
    assert scraper.sort_comments_newest(page) == "unverified"


def test_sort_falls_back_to_text_option_without_menuitem_role(page, fake_fb):
    open_post(page, fake_fb, Post("1", sort_mode="text-option"))
    assert scraper.sort_comments_newest(page) == "الأحدث"


def test_sort_uses_real_clicks_not_synthetic(page, fake_fb):
    """CLAUDE.md rule 5: menu opening must be locator.click(). Verified by event.isTrusted."""
    open_post(page, fake_fb, Post("1"))
    page.evaluate("""() => { window.__trusted = [];
        document.addEventListener('click', e => window.__trusted.push(e.isTrusted), true); }""")
    scraper.sort_comments_newest(page)
    assert page.evaluate("window.__trusted") == [True, True]


class _Loc:
    def __init__(self, n, click_error=None):
        self.n, self.click_error = n, click_error

    def count(self):
        return self.n

    @property
    def last(self):
        return self

    @property
    def first(self):
        return self

    def scroll_into_view_if_needed(self, timeout=None):
        if self.click_error:
            raise self.click_error

    def click(self, timeout=None):
        if self.click_error:
            raise self.click_error


class _StubPage:
    """For branches a real DOM can't reach quickly (detached element)."""

    def __init__(self, opener_error=None):
        self.opener_error = opener_error
        self.keys = []

    def wait_for_timeout(self, ms):
        pass

    def get_by_role(self, role, name=None):
        if role == "button" and name.search("الأكثر ملاءمة"):
            return _Loc(1, self.opener_error)
        return _Loc(0)

    def get_by_text(self, pattern):
        return _Loc(0)

    @property
    def keyboard(self):
        page = self

        class K:
            def press(self, key):
                page.keys.append(key)
        return K()


def test_sort_detached_opener():
    assert scraper.sort_comments_newest(_StubPage(opener_error=RuntimeError("detached"))) == "detached"


# ---------------------------------------------------------------- expand_comments

def test_expand_opens_more_comments_and_reply_threads(page, fake_fb):
    cs = comments(6)
    cs[0].replies = [Comment(id="801", text="رد")]
    cs[5].replies = [Comment(id="802", text="رد 2"), Comment(id="803", text="رد 3")]
    open_post(page, fake_fb, Post("1", comments=cs, page_size=2, sort_mode="newest"))
    scraper.expand_comments(page, rounds=30)
    article_ids = page.eval_on_selector_all("[role=article][data-cid]", "els => els.map(e => e.dataset.cid)")
    assert set(article_ids) == set(ids(cs)) | {"801", "802", "803"}
    assert page.get_by_role("button", name=re.compile("عرض")).count() == 0


def test_expand_never_clicks_reply_buttons(page, fake_fb):
    cs = comments(3)
    cs[1].replies = [Comment(id="801")]
    open_post(page, fake_fb, Post("1", comments=cs, page_size=1, sort_mode="newest"))
    scraper.expand_comments(page, rounds=30)
    assert not [c for c in clicks(page) if c.startswith("reply:")]
    assert page.locator("[role=textbox][data-for]:not([data-for=post])").count() == 0


def test_expand_clicks_at_most_8_buttons_per_round(page, fake_fb):
    cs = comments(12)
    for i, c in enumerate(cs):
        c.replies = [Comment(id=str(7000 + i))]
    open_post(page, fake_fb, Post("1", comments=cs, sort_mode="newest"))
    scraper.expand_comments(page, rounds=1)
    assert len([c for c in clicks(page) if c.startswith("replies:")]) == 8


def test_expand_english_ui(page, fake_fb):
    cs = comments(4)
    cs[0].replies = [Comment(id="801")]
    open_post(page, fake_fb, Post("1", comments=cs, page_size=2, sort_mode="newest", lang="en"))
    scraper.expand_comments(page, rounds=30)
    assert page.locator("[role=article][data-cid='801']").count() == 1
    assert page.locator("[role=article][data-cid]").count() == 5


def test_expand_returns_zero_when_nothing_to_expand(page, fake_fb):
    open_post(page, fake_fb, Post("1", comments=comments(2), sort_mode="newest"))
    assert scraper.expand_comments(page, rounds=30) == 0
    assert clicks(page) == []


def test_expand_returns_number_of_buttons_clicked(page, fake_fb):
    cs = comments(3)
    cs[0].replies = [Comment(id="801")]
    cs[2].replies = [Comment(id="802")]
    open_post(page, fake_fb, Post("1", comments=cs, sort_mode="newest"))
    assert scraper.expand_comments(page, rounds=30) == 2


# ---------------------------------------------------------------- visible_snapshot

def test_snapshot_extracts_post_header_and_comments(page, fake_fb):
    cs = [Comment(id="501", author="سارة", author_id="111", text="سطر 1\nسطر 2", time="٣ د")]
    open_post(page, fake_fb, Post("77", author="أحمد", author_id="222", message="رسالة المنشور",
                                  created="الأربعاء 21 أكتوبر", comments=cs, sort_mode="newest"))
    snap = scraper.visible_snapshot(page, G, "77")
    assert snap["post_content"] == snap["text"] == "رسالة المنشور"
    assert snap["content_kind"] == "text"
    assert snap["author"] == "أحمد" and snap["author_id"] == "222"
    assert snap["created_time"] == "الأربعاء 21 أكتوبر"
    (c,) = snap["comments"]
    assert c["author"] == "سارة" and c["author_id"] == "111"
    assert c["text"] == "سطر 1\nسطر 2"
    assert c["created_time"] == "٣ د"
    assert c["aria_label"] == "تعليق بواسطة سارة"
    assert "comment_id=501" in c["link"] and "__tn__" not in c["link"]


def test_snapshot_clean_strips_all_tracking_params(page, fake_fb):
    """Fixed D-10: clean() deleted '__cft__' but Facebook sends '__cft__[0]', which stayed."""
    open_post(page, fake_fb, Post("77", comments=[Comment(id="501", author_id="111")], sort_mode="newest"))
    c = scraper.visible_snapshot(page, G, "77")["comments"][0]
    assert c["author_url"] == f"https://www.facebook.com/groups/{G}/user/111/"
    assert "__" not in c["link"]


def test_snapshot_comment_without_user_link(page, fake_fb):
    open_post(page, fake_fb, Post("77", comments=[Comment(id="501", author="صفحة", author_id=None)],
                                  sort_mode="newest"))
    c = scraper.visible_snapshot(page, G, "77")["comments"][0]
    assert c["author"] == "" and c["author_id"] == "" and c["author_url"] == ""


def test_snapshot_prefers_widest_dialog_as_root(page, fake_fb):
    open_post(page, fake_fb, Post("77", comments=comments(2), dialog=True, sort_mode="newest"))
    assert len(scraper.visible_snapshot(page, G, "77")["comments"]) == 2


def test_snapshot_title_fallback_when_no_message(page, fake_fb):
    open_post(page, fake_fb, Post("77", message=None, title="الجروب | نص من العنوان | Facebook", sort_mode="newest"))
    snap = scraper.visible_snapshot(page, G, "77")
    assert snap["post_content"] == "نص من العنوان" and snap["content_kind"] == "text"


def test_snapshot_no_message_no_title(page, fake_fb):
    open_post(page, fake_fb, Post("77", message=None, title="Facebook", sort_mode="newest"))
    snap = scraper.visible_snapshot(page, G, "77")
    assert snap["post_content"] == "" and snap["content_kind"] == ""


def test_snapshot_external_link_keeps_text_separately(page, fake_fb):
    """Fixed: the link used to replace the post's text entirely."""
    open_post(page, fake_fb, Post("77", message="نص", external_url="https://example.org/a?b=1&fbclid=IwZX",
                                  sort_mode="newest"))
    snap = scraper.visible_snapshot(page, G, "77")
    assert snap["post_content"] == snap["media_url"] == "https://example.org/a?b=1"   # fbclid stripped
    assert snap["content_kind"] == "link" and snap["text"] == "نص"


def test_snapshot_video_wins_over_link(page, fake_fb):
    open_post(page, fake_fb, Post("77", external_url="https://example.org/", video_src="https://video.example/v.mp4",
                                  sort_mode="newest"))
    snap = scraper.visible_snapshot(page, G, "77")
    assert snap["post_content"] == "https://video.example/v.mp4" and snap["content_kind"] == "video"
    assert snap["text"] == "نص المنشور"


def test_snapshot_blob_video_is_still_a_video(page, fake_fb):
    """Live 2026-10-21: video src is a blob:, so the old code fell back to kind 'text'."""
    open_post(page, fake_fb, Post("77", video_src="blob:https://www.facebook.com/abc", sort_mode="newest"))
    snap = scraper.visible_snapshot(page, G, "77")
    assert snap["content_kind"] == "video" and snap["media_url"] == ""
    assert snap["post_content"] == "نص المنشور"


def test_snapshot_nested_reply_text_leaks_into_parent(page, fake_fb):
    """Characterization (defect D-11): when reply articles are nested inside the parent article,
    the parent's text includes the replies' text."""
    cs = [Comment(id="501", text="الأصل", replies=[Comment(id="601", text="الرد")])]
    open_post(page, fake_fb, Post("77", comments=cs, nested_replies=True, replies_hidden=False, sort_mode="newest"))
    by_link = {c["link"]: c for c in scraper.visible_snapshot(page, G, "77")["comments"]}
    parent = next(c for k, c in by_link.items() if "reply_comment_id" not in k)
    reply = next(c for k, c in by_link.items() if "reply_comment_id=601" in k)
    assert parent["text"] == "الأصل\nالرد"
    assert reply["text"] == "الرد"


def test_snapshot_on_unavailable_post(page, fake_fb):
    page.goto(f"https://www.facebook.com/groups/{G}/posts/404404/")
    snap = scraper.visible_snapshot(page, G, "404404")
    assert snap["comments"] == [] and snap["author"] == ""


# ---------------------------------------------------------------- scroll_comment_pane

def test_scroll_comment_pane_scrolls_dialog_container(page, fake_fb):
    open_post(page, fake_fb, Post("77", comments=comments(30), dialog=True, sort_mode="newest"))
    scraper.scroll_comment_pane(page)
    assert page.evaluate("document.querySelector('[role=dialog]').scrollTop") > 0


def test_scroll_comment_pane_scrolls_window_without_container(page, fake_fb):
    open_post(page, fake_fb, Post("77", comments=comments(30), sort_mode="newest"))
    before = page.evaluate("window.scrollY + document.scrollingElement.scrollTop")
    scraper.scroll_comment_pane(page)
    assert page.evaluate("window.scrollY + document.scrollingElement.scrollTop") > before


# ---------------------------------------------------------------- scrape_post

def scrape(page, fake_fb, post: Post, min_c=0, max_c=1000, **kw):
    fake_fb.add_group(Group(G), [post])
    return scraper.scrape_post(page, G, post.post_id, min_c, max_c, **kw)


def test_scrape_post_newest_order_and_contract(page, fake_fb):
    cs = comments(5)
    out = scrape(page, fake_fb, Post("77", comments=cs))
    assert [c["comment_id"] for c in out["comments"]] == ids(cs)          # newest-first, not "relevant"
    assert out["comment_sort"] == "الأحدث"
    assert list(out.keys()) == ["group_id", "post_id", "permalink", "author", "author_id", "author_url",
                                "text", "post_content", "content_kind", "media_url", "created_time",
                                "created_time_raw", "url",
                                "comment_sort", "comments_found", "replies_found", "comments_shortfall",
                                "comments_over_max", "capture_mode", "sheet_batches", "comments"]
    assert out["permalink"] == out["url"] == f"https://www.facebook.com/groups/{G}/posts/77/"
    assert out["capture_mode"] == "incremental" and out["comments_over_max"] is False
    assert out["sheet_batches"] == 0
    assert out["comments_found"] == 5 and out["replies_found"] == 0


def test_scrape_post_without_sorting_would_capture_relevant_order(page, fake_fb):
    """Negative control: the fixture's 'relevant' order really differs, so the test above means something."""
    cs = comments(5)
    open_post(page, fake_fb, Post("77", comments=cs))
    snap = scraper.visible_snapshot(page, G, "77")
    assert [scraper.normalize_comment(c, G, "77")["comment_id"] for c in snap["comments"]] == ids(cs)[::-1]


def test_scrape_post_replies_and_relationships(page, fake_fb):
    cs = comments(3)
    cs[1].replies = [Comment(id="880000000000001", text="رد 1"), Comment(id="880000000000002", text="رد 2")]
    out = scrape(page, fake_fb, Post("77", comments=cs))
    by_id = {c["comment_id"]: c for c in out["comments"]}
    assert set(by_id) == set(ids(cs)) | {"880000000000001", "880000000000002"}
    for rid in ("880000000000001", "880000000000002"):
        r = by_id[rid]
        assert r["is_reply"] and r["parent_id"] == cs[1].id and r["reply_comment_id"] == rid
        assert r["permalink"].endswith(f"?comment_id={cs[1].id}&reply_comment_id={rid}")
    assert out["replies_found"] == 2
    assert not by_id[cs[0].id]["is_reply"]


def test_scrape_post_respects_max_comments(page, fake_fb):
    out = scrape(page, fake_fb, Post("77", comments=comments(20), page_size=5), max_c=7)
    assert out["comments_found"] == 7
    assert [c["comment_id"] for c in out["comments"]] == ids(comments(7))


def test_scrape_post_min_comments_shortfall_flag(page, fake_fb):
    out = scrape(page, fake_fb, Post("77", comments=comments(2)), min_c=5)
    assert out["comments_shortfall"] is True
    out = scrape(page, fake_fb, Post("78", comments=comments(2)), min_c=2)
    assert out["comments_shortfall"] is False


def test_scrape_post_empty_comment_list(page, fake_fb):
    out = scrape(page, fake_fb, Post("77", comments=[]))
    assert out["comments"] == [] and out["comments_found"] == 0


def test_scrape_post_incremental_capture_survives_dom_virtualization(page, fake_fb):
    """Facebook drops old comments from the DOM on big threads; capture must happen before scrolling."""
    cs = comments(40)
    out = scrape(page, fake_fb, Post("77", comments=cs, page_size=5, virtualize=6))
    assert [c["comment_id"] for c in out["comments"]] == ids(cs)


def test_scrape_post_no_duplicate_comments_across_rounds(page, fake_fb):
    out = scrape(page, fake_fb, Post("77", comments=comments(15), page_size=4))
    got = [c["comment_id"] for c in out["comments"]]
    assert len(got) == len(set(got)) == 15


def test_scrape_post_large_collection(page, fake_fb):
    cs = comments(250)
    out = scrape(page, fake_fb, Post("77", comments=cs, page_size=50), max_c=1000)
    assert out["comments_found"] == 250


def test_scrape_post_english_ui(page, fake_fb):
    cs = comments(4)
    cs[0].replies = [Comment(id="801")]
    out = scrape(page, fake_fb, Post("77", comments=cs, lang="en", page_size=2))
    assert out["comment_sort"] == "الأحدث" and out["comments_found"] == 5


def test_scrape_post_unicode_and_long_text(page, fake_fb):
    text = "نص طويل جدًا 🌙✨ " * 400 + "\nEnglish mixed"
    out = scrape(page, fake_fb, Post("77", comments=[Comment(id="1", text=text)]))
    assert out["comments"][0]["text"] == text.strip()


def test_scrape_post_skips_articles_without_comment_link(page, fake_fb):
    open_post(page, fake_fb, Post("77", comments=comments(2), sort_mode="newest"))
    page.evaluate("""() => { const a = document.createElement('div'); a.setAttribute('role','article');
        a.setAttribute('aria-label','x'); a.innerHTML = '<a href="https://www.facebook.com/x">bad</a>';
        document.querySelector('.comments').append(a); }""")
    snap = scraper.visible_snapshot(page, G, "77")
    assert len(snap["comments"]) == 3
    assert len([c for c in snap["comments"] if scraper.normalize_comment(c, G, "77")]) == 2


@pytest.mark.parametrize("mode, state", [("none", "missing"), ("no-option", "no-option"), ("unverified", "unverified")])
def test_scrape_post_refuses_when_newest_cannot_be_established(page, fake_fb, mode, state):
    with pytest.raises(SystemExit) as exc:
        scrape(page, fake_fb, Post("77", comments=comments(3), sort_mode=mode))
    assert str(exc.value) == f"فرز التعليقات مش «الأحدث» على المنشور 77: {state}"


def test_scrape_post_sort_attempted_three_times_before_failing(page, fake_fb, monkeypatch):
    calls = []
    monkeypatch.setattr(scraper, "sort_comments_newest", lambda p: calls.append(1) or "unverified")
    with pytest.raises(SystemExit):
        scrape(page, fake_fb, Post("77"))
    assert len(calls) == 3


def test_scrape_post_sort_exception_is_reported_in_state(page, fake_fb, monkeypatch):
    def boom(p):
        raise RuntimeError("element detached")
    monkeypatch.setattr(scraper, "sort_comments_newest", boom)
    with pytest.raises(SystemExit) as exc:
        scrape(page, fake_fb, Post("77"))
    assert str(exc.value).endswith("error:element detached")


def test_scrape_post_sort_succeeds_on_retry(page, fake_fb, monkeypatch):
    answers = ["detached", "الأحدث"]
    monkeypatch.setattr(scraper, "sort_comments_newest", lambda p: answers.pop(0))
    assert scrape(page, fake_fb, Post("77", sort_mode="newest"))["comment_sort"] == "الأحدث"
    assert answers == []


def test_scrape_post_on_batch_callback(page, fake_fb):
    """Characterization: on_batch works when given, though archive_group always passes None."""
    batches = []
    out = scrape(page, fake_fb, Post("77", comments=comments(7)), on_batch=lambda b, i, d: batches.append((len(b), i, d)),
                 batch_size=3)
    assert batches == [(3, 1, False), (3, 2, False), (1, 3, True)]
    assert out["sheet_batches"] == 3


def test_scrape_post_is_read_only(page, fake_fb):
    """No reply is typed or submitted and no reply button is pressed while scraping."""
    cs = comments(4)
    cs[0].replies = [Comment(id="801")]
    scrape(page, fake_fb, Post("77", comments=cs, page_size=2))
    assert fake_fb.replies == []
    assert page.evaluate("window.__sent") == []
    assert not [c for c in clicks(page) if c.startswith("reply:")]
    assert all(u.startswith("https://www.facebook.com/groups/") for u in fake_fb.navigations())


def test_scrape_post_survives_escape_closing_the_post_dialog(page, fake_fb):
    """Fixed D-19 (seen live 2026-10-21): scrape_post pressed Escape before each sort attempt; where
    Facebook shows the post as a dialog, Escape closed it and went to the home page."""
    cs = comments(3)
    out = scrape(page, fake_fb, Post("77", comments=cs, escape_closes=True))
    assert [c["comment_id"] for c in out["comments"]] == ids(cs)
    assert page.url.endswith("/posts/77/")


def test_scrape_post_stops_with_clear_reason_if_facebook_leaves_the_post(page, fake_fb, monkeypatch):
    def leave(p):
        p.goto("https://www.facebook.com/")
        return "missing"
    monkeypatch.setattr(scraper, "sort_comments_newest", leave)
    with pytest.raises(SystemExit) as exc:
        scrape(page, fake_fb, Post("77", comments=comments(2)))
    assert "نقل الصفحة بعيد عن المنشور 77" in str(exc.value)


def test_scrape_post_without_comments_is_archived(page, fake_fb):
    """Fixed D-20 (seen live: 4 of 5 newest posts had no comments, each one stopped the run)."""
    out = scrape(page, fake_fb, Post("77", comments=[], sort_mode="none", message="منشور جديد"))
    assert out["comment_sort"] == "no-comments"
    assert out["comments"] == [] and out["text"] == "منشور جديد"


def test_scrape_post_without_comments_is_fast(page, fake_fb, monkeypatch):
    """No 5-round scroll loop when Facebook says there are no comments: one header snapshot."""
    calls = []
    real = scraper.visible_snapshot
    monkeypatch.setattr(scraper, "visible_snapshot", lambda *a: calls.append(1) or real(*a))
    scrape(page, fake_fb, Post("77", comments=[], sort_mode="none"))
    assert len(calls) == 1


def test_scrape_post_zero_comments_without_text_still_stops(page, fake_fb):
    with pytest.raises(SystemExit) as exc:
        scrape(page, fake_fb, Post("77", comments=[], sort_mode="none", no_comments_text=False))
    assert str(exc.value).endswith(": missing")



def test_snapshot_shared_post_text_kind_and_original_link(page, fake_fb):
    """Fixed D-27 (seen live): a shared post has no message element and was stored with empty text."""
    open_post(page, fake_fb, Post("77", message=None, shared_text="سيدة محتاجة سيارة إسعاف", sort_mode="newest",
                                  comments=[Comment(id="501", text="ربنا يستر")]))
    snap = scraper.visible_snapshot(page, G, "77")
    assert snap["content_kind"] == "share"
    assert snap["text"] == "سيدة محتاجة سيارة إسعاف"                      # not doubled, not a comment
    assert snap["media_url"] == snap["post_content"] == "https://www.facebook.com/somepage/posts/pfbid0SharedOriginal"


def test_snapshot_own_group_links_are_not_shares(page, fake_fb):
    open_post(page, fake_fb, Post("77", message="منشور عادي", sort_mode="newest",
                                  comments=[Comment(id="501")]))
    assert scraper.visible_snapshot(page, G, "77")["content_kind"] == "text"



# ---------------------------------------------------------------- round 3 (live run 5)

def test_sort_comments_off(page, fake_fb):
    """Fixed D-34 (live run 5): «تم إيقاف تشغيل التعليقات» — no sort control and no none-text."""
    open_post(page, fake_fb, Post("1", comments=[], sort_mode="none", comments_off=True))
    assert scraper.sort_comments_newest(page) == "comments-off"


def test_scrape_post_comments_off_is_archived(page, fake_fb):
    out = scrape(page, fake_fb, Post("77", comments=[], sort_mode="none", comments_off=True, message="نص"))
    assert out["comment_sort"] == "comments-off" and out["comments"] == [] and out["text"] == "نص"


def test_scrape_post_unavailable_post_is_not_a_failure(page, fake_fb):
    """Fixed D-33 (live run 5): the post was deleted between feed and visit."""
    fake_fb.add_group(Group(G))
    out = scraper.scrape_post(page, G, "404404", 0, 20)
    assert out["unavailable"] is True and out["comment_sort"] == "unavailable" and out["comments"] == []


def test_collect_private_group_not_member(page, fake_fb, capsys):
    """Live run 5: a private group the account isn't in has no feed; say so instead of scrolling."""
    fake_fb.add_group(Group(G, private=True))
    assert scraper.collect_post_ids(page, G, 5) == ([], "")
    assert "خاص" in capsys.readouterr().err


def test_member_group_is_not_reported_private(page, fake_fb):
    fake_fb.add_group(Group(G), [Post("1")])
    page.goto(f"https://www.facebook.com/groups/{G}")
    assert scraper.group_is_private_for_us(page) is False


def test_snapshot_reel_caption_and_link(page, fake_fb):
    """Fixed D-37 (live run 5): a reel/video caption is not in data-ad-preview; it was stored empty,
    and the video's media_url was its photo thumbnail."""
    open_post(page, fake_fb, Post("77", message=None, video_src="blob:https://www.facebook.com/x",
                                  video_caption="وكنتُ أظنني الأقسى", reel_link="https://www.facebook.com/reel/157766/",
                                  sort_mode="newest"))
    snap = scraper.visible_snapshot(page, G, "77")
    assert snap["content_kind"] == "video"
    assert snap["text"] == "وكنتُ أظنني الأقسى"                    # no "... عرض المزيد", no audio credit line
    assert snap["media_url"] == "https://www.facebook.com/reel/157766/"


def test_snapshot_comment_sticker_counts_as_media(page, fake_fb):
    open_post(page, fake_fb, Post("77", comments=[Comment(id="501", text="")], sort_mode="newest"))
    page.evaluate("""() => { const a = document.querySelector("[data-cid='501']");
        const img = document.createElement('img'); img.src = 'https://static.xx.fbcdn.net/sticker.png'; a.append(img); }""")
    assert scraper.visible_snapshot(page, G, "77")["comments"][0]["has_media"] is True


def test_snapshot_emoji_inside_text_is_not_media(page, fake_fb):
    open_post(page, fake_fb, Post("77", comments=[Comment(id="501", text="تمام")], sort_mode="newest"))
    page.evaluate("""() => { const t = document.querySelector("[data-cid='501'] div[dir='auto']");
        const img = document.createElement('img'); img.alt = '😀'; t.append(img); }""")
    assert scraper.visible_snapshot(page, G, "77")["comments"][0]["has_media"] is False


def test_scrape_post_no_comments_waits_for_late_video(page, fake_fb, monkeypatch):
    """Fixed D-36 (live run 5): the single header snapshot came before the video rendered."""
    real = scraper.visible_snapshot
    seen = []

    def late(p, g, pid):
        snap = real(p, g, pid)
        if len(seen) < 1:                        # first snapshot: player not rendered yet
            snap = {**snap, "content_kind": "", "post_content": "", "media_url": ""}
        seen.append(snap["content_kind"])
        return snap
    monkeypatch.setattr(scraper, "visible_snapshot", late)
    out = scrape(page, fake_fb, Post("77", comments=[], sort_mode="none", video_src="https://v.example/v.mp4"))
    assert seen == ["", "video"]
    assert out["content_kind"] == "video"


def test_scrape_post_stops_early_at_end_of_list(page, fake_fb, monkeypatch):
    """Perf (live run 5: 14 rounds for 16 comments): with no expanders and no scroll movement,
    two empty rounds end the loop instead of five."""
    calls = []
    real = scraper.visible_snapshot
    monkeypatch.setattr(scraper, "visible_snapshot", lambda *a: calls.append(1) or real(*a))
    monkeypatch.setattr(scraper, "scroll_comment_pane", lambda p: False)
    monkeypatch.setattr(scraper, "expand_comments", lambda p, rounds=1: 0)
    out = scrape(page, fake_fb, Post("77", comments=comments(4)))
    assert out["comments_found"] == 4
    assert len(calls) == 3                      # 1 round with new comments + 2 empty


def test_scrape_post_keeps_going_while_scrolling_moves(page, fake_fb, monkeypatch):
    calls = []
    real = scraper.visible_snapshot
    monkeypatch.setattr(scraper, "visible_snapshot", lambda *a: calls.append(1) or real(*a))
    monkeypatch.setattr(scraper, "scroll_comment_pane", lambda p: True)
    monkeypatch.setattr(scraper, "expand_comments", lambda p, rounds=1: 0)
    scrape(page, fake_fb, Post("77", comments=comments(4)))
    assert len(calls) == 6                      # 1 + 5 stale: the old safety net still applies


def test_scroll_comment_pane_reports_movement(page, fake_fb):
    open_post(page, fake_fb, Post("77", comments=comments(30), dialog=True, sort_mode="newest"))
    assert scraper.scroll_comment_pane(page) is True
    page.evaluate("document.querySelector('[role=dialog]').scrollTop = 1e9")
    assert scraper.scroll_comment_pane(page) is False
