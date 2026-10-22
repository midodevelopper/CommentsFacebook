"""fb_groups_to_sheet.py: pure functions, Sheets transport, CLI parsing, orchestration.

Browser-dependent functions are in test_scraper_browser.py.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error

import pytest

import fb_groups_to_sheet as scraper
from conftest import PYTHON, ROOT

pytestmark = pytest.mark.unit

G = "4678007325765117"
P = "1234567890123456"


# ---------------------------------------------------------------- constants / contracts

def test_constants():
    assert scraper.WEBAPP.startswith("https://script.google.com/macros/s/")
    assert scraper.WEBAPP.endswith("/exec")
    assert scraper.CDP == os.environ.get("FB_CDP", "http://127.0.0.1:9333")
    assert str(scraper.DUMP_DIR).endswith("dumps")


def test_dump_dir_is_absolute_hardcoded_path():
    """Characterization: DUMP_DIR is an absolute literal, unlike ROOT-relative paths elsewhere."""
    source = (ROOT / "fb_groups_to_sheet.py").read_text(encoding="utf-8")
    assert 'DUMP_DIR = Path(r"D:\\Hermes Browser\\dumps")' in source


@pytest.mark.cli
def test_token_and_cdp_read_from_environment_at_import():
    code = "import fb_groups_to_sheet as m, json; print(json.dumps([m.TOKEN, m.CDP]))"
    env = {**os.environ, "FB_SHEET_TOKEN": "tok-from-env", "FB_CDP": "http://127.0.0.1:9555"}
    out = subprocess.run([PYTHON, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == ["tok-from-env", "http://127.0.0.1:9555"]


@pytest.mark.cli
def test_token_default_when_env_missing():
    code = "import fb_groups_to_sheet as m; print(m.TOKEN == 'CommentSecrets')"
    env = {k: v for k, v in os.environ.items() if k != "FB_SHEET_TOKEN"}
    out = subprocess.run([PYTHON, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "True"


# ---------------------------------------------------------------- group_key / regexes

@pytest.mark.parametrize("url, expected", [
    ("https://www.facebook.com/groups/4678007325765117", "4678007325765117"),
    ("https://www.facebook.com/groups/4678007325765117/", "4678007325765117"),
    ("https://www.facebook.com/groups/4678007325765117?sorting_setting=CHRONOLOGICAL", "4678007325765117"),
    ("https://www.facebook.com/groups/4678007325765117#top", "4678007325765117"),
    ("https://m.facebook.com/groups/4678007325765117/posts/1/", "4678007325765117"),
    ("  https://facebook.com/groups/my.group-name/  ", "my.group-name"),
    ("HTTPS://WWW.FACEBOOK.COM/GROUPS/ABC", "ABC"),
    ("facebook.com/groups/123", "123"),
])
def test_group_key_extracts_id_or_slug(url, expected):
    assert scraper.group_key(url) == expected


@pytest.mark.parametrize("url", [
    "https://www.facebook.com/profile.php?id=1",
    "https://example.com/groups/123",
    "https://www.facebook.com/groups/",
    "",
])
def test_group_key_rejects_non_group_urls(url):
    with pytest.raises(SystemExit) as exc:
        scraper.group_key(url)
    assert "مش رابط جروب فيسبوك" in str(exc.value)


def test_group_key_accepts_lookalike_domain():
    """Characterization: the regex is not anchored to the host."""
    assert scraper.group_key("https://notfacebook.com/groups/999") == "999"


@pytest.mark.parametrize("href, expected", [
    (f"https://www.facebook.com/groups/{G}/posts/{P}/", P),
    (f"https://www.facebook.com/groups/{G}/permalink/{P}/", P),
    (f"https://www.facebook.com/groups/{G}/posts/{P}/?comment_id=5&__cft__[0]=x", P),
    (f"https://www.facebook.com/groups/slug/posts/{P}", P),
    (f"https://www.facebook.com/groups/{G}/posts/abc/", None),
    (f"https://www.facebook.com/{G}/posts/{P}/", None),
])
def test_post_re(href, expected):
    match = scraper.POST_RE.search(href)
    assert (match.group(1) if match else None) == expected


def test_post_re_matches_posts_of_other_groups():
    """Characterization (defect D-08): feed links to OTHER groups' posts are also collected."""
    assert scraper.POST_RE.search("https://www.facebook.com/groups/999/posts/111/").group(1) == "111"


def test_comment_id_regexes():
    link = f"https://www.facebook.com/groups/{G}/posts/{P}/?comment_id=111&reply_comment_id=222&__tn__=R"
    assert scraper.CID_RE.search(link).group(1) == "111"
    assert scraper.RCID_RE.search(link).group(1) == "222"


def test_cid_re_anchored_to_parameter_boundary():
    """Fixed D-07: CID_RE used to match inside reply_comment_id= when the params were reversed."""
    link = "https://x/?reply_comment_id=222&comment_id=111"
    assert scraper.CID_RE.search(link).group(1) == "111"
    assert scraper.RCID_RE.search(link).group(1) == "222"
    assert scraper.CID_RE.search("https://x/?xcomment_id=5") is None


@pytest.mark.parametrize("text", [
    "عرض المزيد من التعليقات", "عرض التعليقات السابقة", "عرض المزيد من الردود", "عرض كل الردود",
    "عرض الردود", "عرض 12 من الردود", "عرض 3 رد", "View more comments", "View previous comments",
    "View more replies", "View all 14 replies", "View 1 reply", "View 2 replies",
])
def test_expand_re_matches_expanders(text):
    assert scraper.EXPAND_RE.search(text)


@pytest.mark.parametrize("text", ["رد", "Reply", "إعجاب", "Like"])
def test_expand_re_does_not_match_reply_or_like(text):
    assert not scraper.EXPAND_RE.search(text)


def test_sort_regexes():
    assert scraper.NEWEST_RE.match("الأحدث")
    assert scraper.NEWEST_RE.match("Newest first")
    assert not scraper.NEWEST_RE.match("Most relevant")
    for label in ("الأكثر ملاءمة", "كل التعليقات", "الأحدث", "Most relevant", "All comments", "Newest"):
        assert scraper.SORT_OPEN_RE.match(label)


def test_module_regex_constants_are_not_used_by_code():
    """Characterization: EXPAND_RE / NEWEST_RE / SORT_OPEN_RE are defined but the code
    re-declares the same patterns inline (JS strings and local re.compile)."""
    source = (ROOT / "fb_groups_to_sheet.py").read_text(encoding="utf-8")
    for name in ("EXPAND_RE", "NEWEST_RE", "SORT_OPEN_RE"):
        assert source.count(name) == 1, name


# ---------------------------------------------------------------- normalize_comment

def link(cid, rcid=None, extra="&__cft__[0]=AZ&__tn__=R"):
    url = f"https://www.facebook.com/groups/{G}/posts/{P}/?comment_id={cid}"
    if rcid:
        url += f"&reply_comment_id={rcid}"
    return url + extra


def test_normalize_top_level_comment():
    raw = {"link": link("901"), "author": "  أحمد  ", "author_id": "1001",
           "author_url": "https://www.facebook.com/groups/G/user/1001/", "text": "  مرحبا \n",
           "created_time": "الأربعاء، 21 أكتوبر 2026 الساعة 3:38 م", "aria_label": "تعليق بواسطة أحمد"}
    assert scraper.normalize_comment(raw, G, P) == {
        "group_id": G, "post_id": P, "comment_id": "901",
        "parent_comment_id": "", "parent_id": "", "reply_comment_id": "", "is_reply": False,
        "reply_target_id": "901", "author": "أحمد", "author_id": "1001",
        "author_url": "https://www.facebook.com/groups/G/user/1001/", "text": "مرحبا",
        "created_time": "2026-10-21 15:38",
        "created_time_raw": "الأربعاء، 21 أكتوبر 2026 الساعة 3:38 م",
        "has_media": False, "aria_label": "تعليق بواسطة أحمد",
        "permalink": f"https://www.facebook.com/groups/{G}/posts/{P}/?comment_id=901",
    }


def test_normalize_relative_time_becomes_timestamp_and_keeps_raw():
    c = scraper.normalize_comment({"link": link("901"), "created_time": " ٥ د "}, G, P)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", c["created_time"])
    assert c["created_time_raw"] == "٥ د"


def test_normalize_reply_comment():
    c = scraper.normalize_comment({"link": link("901", "902")}, G, P)
    assert c["comment_id"] == "902"
    assert c["parent_id"] == c["parent_comment_id"] == "901"
    assert c["reply_comment_id"] == "902"
    assert c["is_reply"] is True
    assert c["reply_target_id"] == "902"
    assert c["permalink"] == f"https://www.facebook.com/groups/{G}/posts/{P}/?comment_id=901&reply_comment_id=902"


@pytest.mark.parametrize("raw", [{}, {"link": ""}, {"link": None},
                                 {"link": "https://www.facebook.com/groups/1/posts/2/"},
                                 {"link": "https://x/?comment_id=abc"}])
def test_normalize_without_comment_id_returns_none(raw):
    assert scraper.normalize_comment(raw, G, P) is None


def test_normalize_missing_fields_become_empty_strings():
    c = scraper.normalize_comment({"link": link("7"), "author": None, "text": None,
                                   "created_time": None, "author_id": None}, G, P)
    assert (c["author"], c["text"], c["created_time"], c["author_id"], c["author_url"], c["aria_label"]) == ("",) * 6


def test_normalize_preserves_long_ids_exactly():
    long_id = "1234567890123456789012"
    c = scraper.normalize_comment({"link": link(long_id, long_id + "1")}, "00012", "000345")
    assert c["parent_id"] == long_id and c["comment_id"] == long_id + "1"
    assert c["group_id"] == "00012" and c["post_id"] == "000345"
    assert isinstance(c["comment_id"], str)


def test_normalize_permalink_uses_given_group_not_link_group():
    c = scraper.normalize_comment({"link": "https://www.facebook.com/groups/OTHER/posts/9/?comment_id=5"}, G, P)
    assert c["permalink"] == f"https://www.facebook.com/groups/{G}/posts/{P}/?comment_id=5"


def test_normalize_long_unicode_text_is_kept_whole():
    text = ("سطر عربي طويل 🌙 " * 2000) + "end"
    assert scraper.normalize_comment({"link": link("1"), "text": text}, G, P)["text"] == text.strip()


# ---------------------------------------------------------------- sheet_comment / sheet_post_payload

SHEET_COMMENT_KEYS = ["comment_id", "parent_id", "reply_comment_id", "is_reply", "reply_target_id",
                      "author", "author_id", "author_url", "text", "created_time", "permalink",
                      "post content", "reply", "reply_status"]


def test_sheet_comment_contract_keys_and_order():
    assert list(scraper.sheet_comment({}).keys()) == SHEET_COMMENT_KEYS


def test_sheet_comment_empty_input():
    out = scraper.sheet_comment({})
    assert out["is_reply"] is False
    assert all(v == "" for k, v in out.items() if k != "is_reply")


def test_sheet_comment_from_normalized_reply():
    normalized = scraper.normalize_comment({"link": link("901", "902"), "author": "x", "text": "t"}, G, P)
    out = scraper.sheet_comment(normalized, "محتوى")
    assert out["comment_id"] == "902" and out["parent_id"] == "901" and out["reply_comment_id"] == "902"
    assert out["is_reply"] is True and out["reply_target_id"] == "902"
    assert out["post content"] == "محتوى"
    assert "aria_label" not in out and "group_id" not in out and "post_id" not in out


def test_sheet_comment_fallbacks():
    assert scraper.sheet_comment({"parent_comment_id": "5"})["parent_id"] == "5"
    assert scraper.sheet_comment({"comment_id": "9"})["reply_target_id"] == "9"
    assert scraper.sheet_comment({"post_content": "a"})["post content"] == "a"
    assert scraper.sheet_comment({"post content": "b", "post_content": "a"})["post content"] == "b"
    assert scraper.sheet_comment({"post content": "b"}, "arg")["post content"] == "arg"
    assert scraper.sheet_comment({"is_reply": "yes"})["is_reply"] is True


def test_sheet_comment_carries_existing_reply_fields():
    out = scraper.sheet_comment({"reply": "شكرا", "reply_status": "posted"})
    assert (out["reply"], out["reply_status"]) == ("شكرا", "posted")


def test_sheet_post_payload_contract():
    post = {"post_id": P, "author": "A", "author_id": "1", "post_content": "content", "text": "t",
            "created_time": "ct", "url": "u", "permalink": "p", "comment_sort": "الأحدث"}
    comments = [scraper.normalize_comment({"link": link("1")}, G, P)]
    payload = scraper.sheet_post_payload(G, post, comments)
    assert list(payload.keys()) == ["token", "group_id", "posts"]
    assert payload["token"] == scraper.TOKEN and payload["group_id"] == G
    (p,) = payload["posts"]
    assert list(p.keys()) == ["post_id", "author", "author_id", "text", "post content", "created_time",
                              "url", "permalink", "comment_sort", "comments"]
    assert p["text"] == "t"                    # the post's own words
    assert p["post content"] == "content"      # media/link when present, else the text
    assert p["comments"][0]["post content"] == "content"
    assert list(p["comments"][0].keys()) == SHEET_COMMENT_KEYS


def test_sheet_post_payload_defaults():
    p = scraper.sheet_post_payload(G, {"post_id": P, "text": "t", "permalink": "pl"}, [])["posts"][0]
    assert p["comment_sort"] == "الأحدث"
    assert p["text"] == p["post content"] == "t"
    assert p["url"] == "pl" and p["permalink"] == "pl"
    assert p["author"] == p["author_id"] == p["created_time"] == ""
    assert p["comments"] == []


def test_sheet_post_payload_does_not_send_author_url_or_content_kind():
    p = scraper.sheet_post_payload(G, {"post_id": P, "author_url": "x", "content_kind": "video"}, [])["posts"][0]
    assert "author_url" not in p and "content_kind" not in p


# ---------------------------------------------------------------- sheet_post transport (real loopback HTTP)

@pytest.fixture
def sheet(http_server, monkeypatch):
    monkeypatch.setattr(scraper, "WEBAPP", http_server.url + "/exec")
    return http_server


def test_sheet_post_sends_utf8_json_post(sheet):
    sheet.respond(body='{"ok":true,"posts_new":1}')
    answer = scraper.sheet_post({"token": "t", "text": "عربي 🌙"})
    assert answer == '{"ok":true,"posts_new":1}'
    req = sheet.requests[-1]
    assert req["method"] == "POST" and req["path"] == "/exec"
    assert req["headers"]["Content-Type"] == "application/json"
    assert "عربي 🌙".encode("utf-8") in req["body"]          # ensure_ascii=False
    assert json.loads(req["body"]) == {"token": "t", "text": "عربي 🌙"}


def test_sheet_post_stops_when_apps_script_says_not_ok(sheet):
    """Fixed D-02: {"ok": false} (bad token, script error) used to be printed and ignored."""
    sheet.respond(body='{"ok":false,"error":"bad token"}')
    with pytest.raises(SystemExit) as exc:
        scraper.sheet_post({"token": "wrong"})
    assert "bad token" in str(exc.value)


def test_sheet_post_stops_on_non_json_answer(sheet):
    """An HTML page from Apps Script (uncaught error, busy lock) is a failure, not a success."""
    sheet.respond(body="<html>Google error page</html>", content_type="text/html")
    with pytest.raises(SystemExit) as exc:
        scraper.sheet_post({})
    assert "Google error page" in str(exc.value)


@pytest.mark.parametrize("body", ['{"posts_new": 1}', "[]", '"ok"'])
def test_sheet_post_stops_without_explicit_ok_true(sheet, body):
    sheet.respond(body=body)
    with pytest.raises(SystemExit):
        scraper.sheet_post({})


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(scraper.time, "sleep", slept.append)
    return slept


def test_sheet_post_http_5xx_retried_then_systemexit(sheet, no_sleep):
    """Fixed D-30: one transient Apps Script HTTP 500 used to abort the whole run (seen live)."""
    sheet.respond(status=500, body="x" * 2000)
    with pytest.raises(SystemExit) as exc:
        scraper.sheet_post({})
    msg = str(exc.value)
    assert "HTTP 500" in msg and "محاولة 3" in msg
    assert msg.count("x") == 300
    assert len(sheet.requests) == scraper.SHEET_ATTEMPTS == 3
    assert no_sleep == [5, 15]


def test_sheet_post_5xx_then_success(sheet, no_sleep):
    sheet.queue(status=500, body="<html>We're sorry, a server error occurred.</html>", content_type="text/html")
    sheet.queue(status=200, body='{"ok":true,"posts_new":1}')
    assert scraper.sheet_post({"token": "t"}) == '{"ok":true,"posts_new":1}'
    assert len(sheet.requests) == 2 and sheet.requests[0]["body"] == sheet.requests[1]["body"]
    assert no_sleep == [5]


def test_sheet_post_4xx_not_retried(sheet, no_sleep):
    sheet.respond(status=403, body="forbidden")
    with pytest.raises(SystemExit) as exc:
        scraper.sheet_post({})
    assert "HTTP 403" in str(exc.value) and len(sheet.requests) == 1 and no_sleep == []


def test_sheet_post_bad_token_not_retried(sheet, no_sleep):
    sheet.respond(body='{"ok":false,"error":"bad token"}')
    with pytest.raises(SystemExit):
        scraper.sheet_post({})
    assert len(sheet.requests) == 1 and no_sleep == []


def test_sheet_post_busy_lock_retried(sheet, no_sleep):
    sheet.queue(body='{"ok":false,"error":"Exception: Lock timeout: another process was holding the lock"}')
    sheet.queue(body='{"ok":true}')
    assert scraper.sheet_post({}) == '{"ok":true}'
    assert no_sleep == [5]


def test_apps_script_error_extracts_readable_text():
    page = ("<!DOCTYPE html><html><head><script nonce='x'>window['ppConfig'] = {a: 1};</script>"
            "<style>.x{}</style></head><body><div>Exception: Service Spreadsheets timed out &amp; more</div></body></html>")
    assert scraper.apps_script_error(page) == "Exception: Service Spreadsheets timed out & more"


def test_sheet_post_network_error_retried_then_propagates(monkeypatch, no_sleep):
    from conftest import free_port

    monkeypatch.setattr(scraper, "WEBAPP", f"http://127.0.0.1:{free_port()}/exec")
    with pytest.raises(urllib.error.URLError):
        scraper.sheet_post({})
    assert no_sleep == [5, 15]


def test_sheet_post_timeout_is_90_seconds_and_retried(monkeypatch, no_sleep):
    seen = []

    def fake_urlopen(req, timeout):
        seen.append(timeout)
        raise TimeoutError("timed out")

    monkeypatch.setattr(scraper.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(TimeoutError):
        scraper.sheet_post({})
    assert seen == [90, 90, 90]


# ---------------------------------------------------------------- groups.txt

LIMITS = ["--min-posts", "1", "--max-posts", "5", "--min-comments", "0", "--max-comments", "30"]


def lines(*rows):
    return "".join(r + "\n" for r in rows)


def test_without_group_reads_groups_file(monkeypatch, tmp_path, capsys):
    """2026-10-22: the group list lives in groups.txt at the project root and is read on every run."""
    f = tmp_path / "groups.txt"
    f.write_text("\ufeff" + lines("# comment", "", "https://www.facebook.com/groups/111/", "222",
                                  "- https://www.facebook.com/groups/some.name/?ref=share   # inline note",
                                  "https://www.facebook.com/groups/111"), encoding="utf-8")
    monkeypatch.setattr(scraper, "GROUPS_FILE", f)
    args = parse(monkeypatch, *LIMITS)
    assert args.group == ["https://www.facebook.com/groups/111/", "https://www.facebook.com/groups/222/",
                          "https://www.facebook.com/groups/some.name/?ref=share"]
    assert "3 جروب من" in capsys.readouterr().err


def test_groups_file_option_overrides_default(monkeypatch, tmp_path):
    f = tmp_path / "other.md"
    f.write_text(lines("# list", "- 333"), encoding="utf-8")
    monkeypatch.setattr(scraper, "GROUPS_FILE", tmp_path / "missing.txt")
    assert parse(monkeypatch, *LIMITS, "--groups-file", str(f)).group == ["https://www.facebook.com/groups/333/"]


def test_group_flag_wins_over_groups_file(monkeypatch, tmp_path):
    f = tmp_path / "groups.txt"
    f.write_text(lines("111"), encoding="utf-8")
    monkeypatch.setattr(scraper, "GROUPS_FILE", f)
    assert parse(monkeypatch, *BASE).group == ["https://www.facebook.com/groups/1"]


@pytest.mark.parametrize("content, message", [
    (None, "مفيش --group ومفيش ملف جروبات"),
    (lines("# only comments", ""), "ملف الجروبات فاضي"),
    (lines("111", "https://www.facebook.com/SomeNewsPage"), "سطر 2: مش رابط جروب"),
])
def test_groups_file_problems_stop_with_reason(monkeypatch, tmp_path, content, message):
    f = tmp_path / "groups.txt"
    if content is not None:
        f.write_text(content, encoding="utf-8")
    monkeypatch.setattr(scraper, "GROUPS_FILE", f)
    with pytest.raises(SystemExit) as exc:
        parse(monkeypatch, *LIMITS)
    assert message in str(exc.value)


def test_groups_file_default_is_project_root():
    assert scraper.GROUPS_FILE == ROOT / "groups.txt"


def test_example_groups_file_parses():
    assert len(scraper.read_groups_file(ROOT / "groups.example.txt")) == 3


# ---------------------------------------------------------------- parse_args

def parse(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["fb_groups_to_sheet.py", *argv])
    return scraper.parse_args()


BASE = ["--group", "https://www.facebook.com/groups/1", "--min-posts", "1", "--max-posts", "5",
        "--min-comments", "0", "--max-comments", "30"]


def test_parse_args_defaults(monkeypatch):
    args = parse(monkeypatch, *BASE)
    assert args.group == ["https://www.facebook.com/groups/1"]
    assert (args.min_posts, args.max_posts, args.min_comments, args.max_comments) == (1, 5, 0, 30)
    assert args.cdp == scraper.CDP
    assert args.dry is False and args.debug is False and args.trace is False
    assert args.batch_size == 80


def test_parse_args_multiple_groups_and_flags(monkeypatch):
    args = parse(monkeypatch, *BASE, "--group", "https://www.facebook.com/groups/2", "--dry", "--debug",
                 "--trace", "--batch-size", "10", "--cdp", "http://127.0.0.1:1")
    assert args.group == ["https://www.facebook.com/groups/1", "https://www.facebook.com/groups/2"]
    assert args.dry and args.debug and args.trace and args.batch_size == 10 and args.cdp == "http://127.0.0.1:1"


@pytest.mark.parametrize("override, message", [
    (["--min-posts", "0"], "min-posts"),
    (["--min-posts", "3", "--max-posts", "2"], "min-posts"),
    (["--min-comments", "-1"], "min-comments"),
    (["--min-comments", "5", "--max-comments", "4"], "min-comments"),
])
def test_parse_args_range_validation(monkeypatch, override, message):
    argv = list(BASE)
    for i in range(0, len(override), 2):
        argv[argv.index(override[i]) + 1] = override[i + 1]
    with pytest.raises(SystemExit) as exc:
        parse(monkeypatch, *argv)
    assert message in str(exc.value)


@pytest.mark.parametrize("size", ["0", "-5"])
def test_parse_args_rejects_batch_size_below_1(monkeypatch, size):
    """Fixed: --batch-size 0 used to be accepted and crash later on range(..., 0)."""
    with pytest.raises(SystemExit) as exc:
        parse(monkeypatch, *BASE, "--batch-size", size)
    assert "batch-size" in str(exc.value)


@pytest.mark.parametrize("missing", ["--min-posts", "--max-posts", "--min-comments", "--max-comments"])
def test_parse_args_required(monkeypatch, missing, capsys):
    argv = list(BASE)
    i = argv.index(missing)
    del argv[i:i + 2]
    with pytest.raises(SystemExit) as exc:
        parse(monkeypatch, *argv)
    assert exc.value.code == 2
    assert missing in capsys.readouterr().err


# ---------------------------------------------------------------- archive_group (browser steps replaced)

class FakeTitlePage:
    def __init__(self, title="آخر منشور | الجروب | Facebook", raises=False):
        self._title, self._raises = title, raises

    def title(self):
        if self._raises:
            raise RuntimeError("page gone")
        return self._title


@pytest.fixture
def flow(monkeypatch):
    calls = {"collect": [], "scrape": [], "sheet": [], "sleep": []}
    posts_comments: dict[str, int] = {}

    def collect(page, group_id, max_posts):
        calls["collect"].append((group_id, max_posts))
        return list(posts_comments)[:max_posts], "label"

    def scrape(page, group_id, post_id, min_c, max_c, on_batch=None, batch_size=80):
        calls["scrape"].append({"post_id": post_id, "min": min_c, "max": max_c,
                                "on_batch": on_batch, "batch_size": batch_size})
        cs = [scraper.normalize_comment({"link": link(str(1000 + i))}, group_id, post_id)
              for i in range(posts_comments[post_id])]
        return {"post_id": post_id, "group_id": group_id, "post_content": "c", "text": "c",
                "permalink": "p", "url": "u", "comment_sort": "الأحدث", "comments": cs,
                "comments_found": len(cs), "replies_found": 0, "sheet_batches": 0}

    def sheet_post(payload):
        calls["sheet"].append(payload)
        return '{"ok":true}'

    monkeypatch.setattr(scraper, "collect_post_ids", collect)
    monkeypatch.setattr(scraper, "scrape_post", scrape)
    monkeypatch.setattr(scraper, "sheet_post", sheet_post)
    monkeypatch.setattr(scraper.time, "sleep", calls["sleep"].append)
    return calls, posts_comments


def test_archive_group_dry_never_sends(flow):
    calls, data = flow
    data.update({"11": 3, "12": 0})
    out = scraper.archive_group(FakeTitlePage(), f"https://www.facebook.com/groups/{G}", 1, 5, 0, 10, dry=True)
    assert calls["sheet"] == []
    assert calls["collect"] == [(G, 5)]
    assert [s["post_id"] for s in calls["scrape"]] == ["11", "12"]
    assert all(s["on_batch"] is None for s in calls["scrape"])        # incremental batching is never used
    assert out["posts_found"] == 2 and out["posts_shortfall"] is False
    assert calls["sleep"] == [1.5, 1.5]


def test_archive_group_return_contract(flow):
    calls, data = flow
    data.update({"11": 1})
    out = scraper.archive_group(FakeTitlePage(), f"https://www.facebook.com/groups/{G}", 2, 5, 1, 10)
    assert list(out.keys()) == ["token", "group_id", "group_url", "group_name", "sort_posts",
                                "feed_sort_label", "sort_comments", "limits", "posts_found",
                                "posts_shortfall", "posts", "posts_unavailable"]   # + D-33: skipped deleted posts
    assert out["token"] == scraper.TOKEN
    assert out["group_url"] == f"https://www.facebook.com/groups/{G}"
    assert out["sort_posts"] == "CHRONOLOGICAL" and out["sort_comments"] == "الأحدث"
    assert out["feed_sort_label"] == "label"
    assert out["limits"] == {"min_posts": 2, "max_posts": 5, "min_comments": 1, "max_comments": 10}
    assert out["posts_shortfall"] is True


def test_archive_group_name_comes_from_last_visited_page_title(flow):
    """Characterization (defect D-09): group_name is the first '|' part of the LAST POST page title."""
    calls, data = flow
    data.update({"11": 0})
    out = scraper.archive_group(FakeTitlePage("آخر منشور | الجروب | Facebook"),
                                f"https://www.facebook.com/groups/{G}", 1, 5, 0, 10)
    assert out["group_name"] == "آخر منشور"
    out = scraper.archive_group(FakeTitlePage(raises=True), f"https://www.facebook.com/groups/{G}", 1, 5, 0, 10)
    assert out["group_name"] == ""


@pytest.mark.parametrize("n_comments, batch_size, expected_sizes", [
    (0, 80, [0]),
    (5, 80, [5]),
    (5, 2, [2, 2, 1]),
    (160, 80, [80, 80]),
])
def test_archive_group_sends_comments_in_batches(flow, n_comments, batch_size, expected_sizes):
    calls, data = flow
    data.update({"11": n_comments})
    out = scraper.archive_group(FakeTitlePage(), f"https://www.facebook.com/groups/{G}", 1, 5, 0, 1000,
                                dry=False, batch_size=batch_size)
    assert [len(p["posts"][0]["comments"]) for p in calls["sheet"]] == expected_sizes
    assert all(p["token"] == scraper.TOKEN and p["group_id"] == G for p in calls["sheet"])
    assert all(p["posts"][0]["post_id"] == "11" for p in calls["sheet"])
    assert out["posts"][0]["sheet_batches"] == len(expected_sizes)
    sent_ids = [c["comment_id"] for p in calls["sheet"] for c in p["posts"][0]["comments"]]
    assert sent_ids == [str(1000 + i) for i in range(n_comments)]      # no duplicates, order kept


def test_archive_group_passes_limits_to_steps(flow):
    calls, data = flow
    data.update({"11": 0})
    scraper.archive_group(FakeTitlePage(), f"https://www.facebook.com/groups/{G}", 1, 7, 3, 9, batch_size=4)
    assert calls["collect"] == [(G, 7)]
    assert calls["scrape"][0] | {"on_batch": None} == {"post_id": "11", "min": 3, "max": 9,
                                                       "on_batch": None, "batch_size": 4}


def test_archive_group_invalid_url_raises_before_browser(flow):
    calls, _ = flow
    with pytest.raises(SystemExit):
        scraper.archive_group(FakeTitlePage(), "https://example.com", 1, 1, 0, 1)
    assert calls["collect"] == []


# ---------------------------------------------------------------- main() with browser + archive replaced

class FakePage:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeRecorder:
    instances: list["FakeRecorder"] = []

    def __init__(self, page, label="page", trace=False, har=False, **_):
        self.page, self.label, self.trace, self.har = page, label, trace, har
        self.marks, self.dumps, self.detached = [], [], False
        FakeRecorder.instances.append(self)

    def mark(self, note):
        self.marks.append(note)

    def dump(self, reason="dump", exc=None):
        self.dumps.append((reason, exc))

    def detach(self):
        self.detached = True


@pytest.fixture
def main_env(monkeypatch, isolated_output_dirs):
    import playwright.sync_api
    import browser_debug

    page = FakePage()

    class Ctx:
        def new_page(self):
            return page

    class Browser:
        contexts = [Ctx()]

    class PW:
        cdp = []

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        class chromium:
            @staticmethod
            def connect_over_cdp(url):
                PW.cdp.append(url)
                return Browser()

    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: PW())
    FakeRecorder.instances = []
    monkeypatch.setattr(browser_debug, "PageRecorder", FakeRecorder)
    sent = []
    monkeypatch.setattr(scraper, "sheet_post", lambda payload: sent.append(payload) or '{"ok":true}')
    monkeypatch.setattr(scraper.time, "strftime", lambda fmt: "20261021-120000")
    return {"page": page, "pw": PW, "sent": sent, "dumps": isolated_output_dirs / "dumps"}


def run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["fb_groups_to_sheet.py", *argv])
    scraper.main()


def fake_archive(posts_by_group):
    def archive(page, url, min_posts, max_posts, min_c, max_c, dry=True, batch_size=80, progress=None):
        gid = scraper.group_key(url)
        return {"token": scraper.TOKEN, "group_id": gid, "posts": posts_by_group.get(gid, [])}
    return archive


def test_main_writes_group_dump_and_run_summary(monkeypatch, main_env):
    monkeypatch.setattr(scraper, "archive_group", fake_archive({"1": [{"post_id": "9", "sheet_batches": 0}]}))
    run_main(monkeypatch, *BASE, "--dry", "--cdp", "http://127.0.0.1:1111")
    group_file = main_env["dumps"] / "1-20261021-120000.json"
    run_file = main_env["dumps"] / "run-20261021-120000.json"
    assert json.loads(group_file.read_text(encoding="utf-8"))["group_id"] == "1"
    assert json.loads(run_file.read_text(encoding="utf-8"))[0]["group_id"] == "1"
    assert main_env["pw"].cdp == ["http://127.0.0.1:1111"]
    assert main_env["page"].closed
    assert main_env["sent"] == []


def test_main_dump_files_do_not_contain_the_sheet_token(monkeypatch, main_env):
    """Fixed S-02: the Apps Script token used to be written into every local dump."""
    monkeypatch.setattr(scraper, "archive_group", fake_archive({}))
    run_main(monkeypatch, *BASE, "--dry")
    for f in main_env["dumps"].iterdir():
        assert scraper.TOKEN not in f.read_text(encoding="utf-8"), f.name
    assert json.loads((main_env["dumps"] / "1-20261021-120000.json").read_text(encoding="utf-8"))["group_id"] == "1"


def test_main_still_sends_token_to_sheet(monkeypatch, main_env):
    monkeypatch.setattr(scraper, "archive_group", fake_archive({"1": [{"post_id": "9", "sheet_batches": 0}]}))
    run_main(monkeypatch, *BASE)
    assert main_env["sent"][0]["token"] == scraper.TOKEN


def test_main_dump_keeps_unicode_unescaped(monkeypatch, main_env):
    monkeypatch.setattr(scraper, "archive_group",
                        fake_archive({"1": [{"post_id": "9", "text": "نص 🌙", "sheet_batches": 1}]}))
    run_main(monkeypatch, *BASE, "--dry")
    assert "نص 🌙" in (main_env["dumps"] / "1-20261021-120000.json").read_text(encoding="utf-8")


def test_main_sends_nothing_for_a_group_without_posts(monkeypatch, main_env):
    """Fixed: a group with zero posts (private, empty) used to send its whole empty payload."""
    monkeypatch.setattr(scraper, "archive_group", fake_archive({}))
    run_main(monkeypatch, *BASE)
    assert main_env["sent"] == []


def test_main_fallback_send_when_posts_exist_but_none_were_batched(monkeypatch, main_env):
    monkeypatch.setattr(scraper, "archive_group", fake_archive({"1": [{"post_id": "9", "sheet_batches": 0}]}))
    run_main(monkeypatch, *BASE)
    assert len(main_env["sent"]) == 1 and main_env["sent"][0]["posts"][0]["post_id"] == "9"


def test_main_no_fallback_send_when_posts_were_batched(monkeypatch, main_env):
    monkeypatch.setattr(scraper, "archive_group", fake_archive({"1": [{"post_id": "9", "sheet_batches": 1}]}))
    run_main(monkeypatch, *BASE)
    assert main_env["sent"] == []


def test_main_multiple_groups_each_get_a_dump(monkeypatch, main_env):
    monkeypatch.setattr(scraper, "archive_group", fake_archive({}))
    run_main(monkeypatch, *BASE, "--group", "https://www.facebook.com/groups/2", "--dry")
    assert (main_env["dumps"] / "1-20261021-120000.json").exists()
    assert (main_env["dumps"] / "2-20261021-120000.json").exists()
    assert len(json.loads((main_env["dumps"] / "run-20261021-120000.json").read_text(encoding="utf-8"))) == 2


def test_main_debug_dumps_on_systemexit_and_reraises(monkeypatch, main_env):
    def boom(*a, **k):
        raise SystemExit("فرز التعليقات مش «الأحدث»")
    monkeypatch.setattr(scraper, "archive_group", boom)
    with pytest.raises(SystemExit):
        run_main(monkeypatch, *BASE, "--debug", "--trace")
    (rec,) = FakeRecorder.instances
    assert rec.label == "scrape" and rec.trace and rec.har
    assert rec.marks == ["group https://www.facebook.com/groups/1"]
    assert rec.dumps[0][0] == "failed" and isinstance(rec.dumps[0][1], SystemExit)
    assert rec.detached and main_env["page"].closed
    # fixed D-18: the run summary is written even when the run fails, with the reason
    summary = json.loads((main_env["dumps"] / "run-20261021-120000.json").read_text(encoding="utf-8"))
    assert summary == [{"failed": "SystemExit: فرز التعليقات مش «الأحدث»"}]


def test_main_successful_traced_run_makes_a_final_dump(monkeypatch, main_env):
    """Fixed D-16: with --debug --trace and no failure, the trace used to be discarded."""
    monkeypatch.setattr(scraper, "archive_group", fake_archive({}))
    run_main(monkeypatch, *BASE, "--dry", "--debug", "--trace")
    (rec,) = FakeRecorder.instances
    assert [d[0] for d in rec.dumps] == ["complete"]


def test_main_successful_untraced_debug_run_makes_no_dump(monkeypatch, main_env):
    monkeypatch.setattr(scraper, "archive_group", fake_archive({}))
    run_main(monkeypatch, *BASE, "--dry", "--debug")
    assert FakeRecorder.instances[0].dumps == []


def test_main_without_debug_creates_no_recorder(monkeypatch, main_env):
    monkeypatch.setattr(scraper, "archive_group", fake_archive({}))
    run_main(monkeypatch, *BASE, "--dry")
    assert FakeRecorder.instances == []


def test_main_failure_without_debug_still_closes_page(monkeypatch, main_env):
    monkeypatch.setattr(scraper, "archive_group", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        run_main(monkeypatch, *BASE)
    assert main_env["page"].closed


def test_main_one_failing_group_stops_the_run_but_summary_is_kept(monkeypatch, main_env):
    """Groups run in one loop; a failure in group 2 still skips group 3 (strict stop kept).
    Fixed D-18: run-*.json is now written with group 1's result and the failure reason."""
    seen = []

    def archive(page, url, *a, **k):
        gid = scraper.group_key(url)
        seen.append(gid)
        if gid == "2":
            raise SystemExit("sort failed")
        return {"token": scraper.TOKEN, "group_id": gid, "posts": []}
    monkeypatch.setattr(scraper, "archive_group", archive)
    with pytest.raises(SystemExit):
        run_main(monkeypatch, *BASE, "--group", "https://www.facebook.com/groups/2",
                 "--group", "https://www.facebook.com/groups/3", "--dry")
    assert seen == ["1", "2"]
    assert (main_env["dumps"] / "1-20261021-120000.json").exists()
    summary = json.loads((main_env["dumps"] / "run-20261021-120000.json").read_text(encoding="utf-8"))
    assert summary[0]["group_id"] == "1" and summary[1] == {"failed": "SystemExit: sort failed"}


def test_main_page_url_rejected_before_any_navigation(monkeypatch, main_env):
    """Facebook Pages (facebook.com/<name>) are not supported: group_key raises SystemExit."""
    monkeypatch.setattr(sys, "argv", ["fb_groups_to_sheet.py", "--group", "https://www.facebook.com/SomeNewsPage",
                                      "--min-posts", "1", "--max-posts", "5", "--min-comments", "0",
                                      "--max-comments", "20", "--dry"])
    with pytest.raises(SystemExit) as exc:
        scraper.main()
    assert "مش رابط جروب فيسبوك" in str(exc.value)


# ---------------------------------------------------------------- helpers added by the 2026-10-21 fixes

import datetime as _dt  # noqa: E402

NOW = _dt.datetime(2026, 10, 21, 16, 4)


@pytest.mark.parametrize("text, expected", [
    ("الأربعاء، 21 أكتوبر 2026 الساعة 3:38 م", "2026-10-21 15:38"),
    ("الأربعاء، 21 أكتوبر 2026 الساعة 12:05 ص", "2026-10-21 00:05"),
    ("الأربعاء، 21 أكتوبر 2026 الساعة 12:30 م", "2026-10-21 12:30"),
    ("Wednesday, October 21, 2026 at 3:38 PM", "2026-10-21 15:38"),
    ("21 أكتوبر 2026", "2026-10-21 00:00"),
    ("٢٥ د", "2026-10-21 15:39"),
    ("٤٦ دقيقة", "2026-10-21 15:18"),
    ("٢ س", "2026-10-21 14:04"),
    ("منذ ٤ أيام", "2026-10-17 16:04"),
    ("3h", "2026-10-21 13:04"),
    ("20m", "2026-10-21 15:44"),
    ("1w", "2026-10-14 16:04"),
    ("الآن", "2026-10-21 16:04"),
    ("Just now", "2026-10-21 16:04"),
    ("أمس الساعة 9:15 م", "2026-10-20 21:15"),
    ("Yesterday at 9:15 PM", "2026-10-20 21:15"),
])
def test_normalize_fb_time(text, expected):
    assert scraper.normalize_fb_time(text, NOW) == expected


@pytest.mark.parametrize("text", ["", "   ", "not a time", "٢٥", "31 فبراير 2026"])
def test_normalize_fb_time_unparseable_kept_as_is(text):
    assert scraper.normalize_fb_time(text, NOW) == text.strip()


@pytest.mark.parametrize("title, expected", [
    ("(2) اسم الجروب _ Group ✨️📚 | Facebook", "اسم الجروب _ Group ✨️📚"),
    ("اسم الجروب | Facebook", "اسم الجروب"),
    ("(12) Group", "Group"),
    ("Facebook", ""),
    ("", ""),
])
def test_clean_title(title, expected):
    assert scraper.clean_title(title) == expected


@pytest.mark.parametrize("href, expected", [
    (f"https://www.facebook.com/groups/{G}/posts/{P}/", P),
    (f"https://www.facebook.com/groups/{G}/permalink/{P}/?x=1", P),
    (f"https://www.facebook.com/groups/999/posts/{P}/", None),           # another group: notifications panel
    (f"https://www.facebook.com/groups/{G}/user/1/", None),
    ("", None),
])
def test_own_group_post_id(href, expected):
    assert scraper.own_group_post_id(href, G) == expected


def test_own_group_post_id_accepts_any_group_when_given_a_slug():
    assert scraper.own_group_post_id("https://www.facebook.com/groups/123/posts/9/", "my.group") == "9"


def test_without_token():
    assert scraper.without_token({"token": "s", "a": 1}) == {"a": 1}



def test_archive_group_reports_progress_after_every_post(flow):
    calls, data = flow
    data.update({"11": 1, "12": 0, "13": 2})
    seen = []
    scraper.archive_group(FakeTitlePage(), f"https://www.facebook.com/groups/{G}", 1, 5, 0, 10,
                          progress=lambda r: seen.append([p["post_id"] for p in r["posts"]]))
    assert seen == [["11"], ["11", "12"], ["11", "12", "13"]]


def test_main_keeps_finished_posts_when_a_later_post_fails(monkeypatch, main_env):
    """Fixed D-31 (seen live): a failure mid-group used to lose every post already scraped."""
    def archive(page, url, *a, progress=None, **k):
        gid = scraper.group_key(url)
        progress({"token": scraper.TOKEN, "group_id": gid, "posts": [{"post_id": "9"}]})
        raise SystemExit("الشيت رفض الإرسال HTTP 500")
    monkeypatch.setattr(scraper, "archive_group", archive)
    with pytest.raises(SystemExit):
        run_main(monkeypatch, *BASE)
    saved = json.loads((main_env["dumps"] / "1-20261021-120000.json").read_text(encoding="utf-8"))
    assert saved == {"group_id": "1", "posts": [{"post_id": "9"}]}



@pytest.mark.parametrize("text, expected", [
    ("11 أكتوبر الساعة 5:38 ص", "2026-10-11 05:38"),          # live run 5: no year when older than ~a day
    ("8 أكتوبر الساعة 10:53 ص", "2026-10-08 10:53"),
    ("October 11 at 5:38 AM", "2026-10-11 05:38"),
    ("28 ديسمبر الساعة 9:00 م", "2025-12-28 21:00"),          # would be in the future -> last year
    ("14 أغسطس", "2026-08-14 00:00"),
])
def test_normalize_fb_time_without_year(text, expected):
    assert scraper.normalize_fb_time(text, NOW) == expected


def test_archive_group_skips_unavailable_posts(flow, monkeypatch):
    """Fixed D-33 (live run 5): a post deleted between feed and visit used to abort the whole group."""
    calls, data = flow
    data.update({"11": 1, "12": 0, "13": 2})
    real = scraper.scrape_post

    def scrape(page, group_id, post_id, *a, **k):
        if post_id == "12":
            return scraper.unavailable_post(group_id, post_id, "u")
        return real(page, group_id, post_id, *a, **k)
    monkeypatch.setattr(scraper, "scrape_post", scrape)
    out = scraper.archive_group(FakeTitlePage(), f"https://www.facebook.com/groups/{G}", 1, 5, 0, 10, dry=False)
    assert [p["post_id"] for p in out["posts"]] == ["11", "13"]
    assert out["posts_unavailable"] == ["12"]
    assert [p["posts"][0]["post_id"] for p in calls["sheet"]] == ["11", "13"]
