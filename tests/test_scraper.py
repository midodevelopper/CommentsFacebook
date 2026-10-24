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
    assert scraper.CDP == os.environ.get("FB_CDP", "http://127.0.0.1:9333")
    assert str(scraper.DUMP_DIR).endswith("dumps")


def test_dump_dir_is_relative_to_project_folder():
    """DUMP_DIR follows the project folder, so the project keeps working after a move."""
    source = (ROOT / "fb_groups_to_sheet.py").read_text(encoding="utf-8")
    assert 'ROOT = Path(__file__).resolve().parent' in source and 'DUMP_DIR = ROOT / "dumps"' in source


def test_no_deployment_url_or_token_in_source():
    """v2: the web-app URL and the write key come from the environment or .secrets/, never from the public repo."""
    source = (ROOT / "fb_groups_to_sheet.py").read_text(encoding="utf-8")
    assert "script.google.com/macros" not in source and "CommentSecrets" not in source and "AKfy" not in source


@pytest.mark.cli
def test_webapp_ingest_key_and_cdp_read_from_environment_at_import():
    code = "import fb_groups_to_sheet as m, json; print(json.dumps([m.WEBAPP, m.INGEST_KEY, m.CDP]))"
    env = {**os.environ, "FB_SHEET_WEBAPP": "https://example.test/exec", "FB_SHEET_INGEST_KEY": "k-env",
           "FB_CDP": "http://127.0.0.1:9555"}
    out = subprocess.run([PYTHON, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == ["https://example.test/exec", "k-env", "http://127.0.0.1:9555"]


def test_sheet_post_refuses_without_configuration(monkeypatch):
    monkeypatch.setattr(scraper, "WEBAPP", "")
    with pytest.raises(SystemExit) as exc:
        scraper.sheet_post({"auth": {"key": "k"}})
    assert "FB_SHEET_WEBAPP" in str(exc.value)


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
    # live 2026-10-24: none of these matched before, so those reply threads were never opened
    "عرض رد واحد", "عرض ردين", "عرض ٣ ردود", "عرض ١٢ ردًا", "عرض كل الردود البالغ عددها ١٢", "عرض الرد",
    "عرض التعليقات المخفية", "View hidden comments",
])
def test_expand_re_matches_expanders(text):
    assert scraper.EXPAND_RE.search(text)


@pytest.mark.parametrize("text", ["رد", "Reply", "إعجاب", "Like", "عرض الترجمة", "عرض المزيد"])
def test_expand_re_does_not_match_reply_or_like(text):
    assert not scraper.EXPAND_RE.search(text)


def test_sort_regexes():
    assert scraper.NEWEST_RE.match("الأحدث")
    assert scraper.NEWEST_RE.match("Newest first")
    assert not scraper.NEWEST_RE.match("Most relevant")
    for label in ("الأكثر ملاءمة", "كل التعليقات", "الأحدث", "Most relevant", "All comments", "Newest"):
        assert scraper.SORT_OPEN_RE.match(label)


def test_module_regex_constants_are_not_used_by_code():
    """Characterization: NEWEST_RE / SORT_OPEN_RE are defined but the code re-declares the same patterns inline
    (local re.compile). EXPAND_RE is used since 2026-10-24: expand_comments sends it to JS as EXPAND_JS, because
    its two inline copies had drifted and missed «عرض رد واحد» / «عرض ردين»."""
    source = (ROOT / "fb_groups_to_sheet.py").read_text(encoding="utf-8")
    for name in ("NEWEST_RE", "SORT_OPEN_RE"):
        assert source.count(name) == 1, name
    assert source.count("EXPAND_JS") >= 3


def test_expand_pattern_has_no_backslash_d_for_js():
    """In JS (no u flag) a digit class is ASCII only; Facebook writes Arabic-Indic digits."""
    assert r"\d" not in scraper.EXPAND_JS


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
        "nest_parent_id": "", "mentions": [], "moderation": "",
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


# ---------------------------------------------------------------- sheet_comment / sheet_post_payload (API v2)

SHEET_COMMENT_KEYS = ["comment_id", "parent_id", "reply_comment_id", "is_reply", "reply_target_id",
                      "author", "author_id", "author_url", "text", "created_time", "url",
                      "nest_parent_id", "mentions", "moderation"]


def test_sheet_comment_contract_keys_and_order():
    assert list(scraper.sheet_comment({}).keys()) == SHEET_COMMENT_KEYS


def test_sheet_comment_never_carries_reply_fields():
    """v2: the scraper owns no reply state; even a stale dump with reply fields sends none."""
    out = scraper.sheet_comment({"reply": "شكرا", "reply_status": "posted"})
    assert "reply" not in out and "reply_status" not in out


def test_sheet_comment_empty_input():
    out = scraper.sheet_comment({})
    assert out["is_reply"] is False and out["mentions"] == []
    assert all(v == "" for k, v in out.items() if k not in ("is_reply", "mentions"))


def test_sheet_comment_from_normalized_reply():
    normalized = scraper.normalize_comment({"link": link("901", "902"), "author": "x", "text": "t",
                                            "created_time": "21 أكتوبر 2026 الساعة 3:38 م"}, G, P)
    out = scraper.sheet_comment(normalized)
    assert out["comment_id"] == "902" and out["parent_id"] == "901" and out["reply_comment_id"] == "902"
    assert out["is_reply"] is True and out["reply_target_id"] == "902"
    assert out["url"] == normalized["permalink"]
    assert re.fullmatch(r"2026-10-21T15:38:00[+-]\d{2}:\d{2}", out["created_time"])
    assert "aria_label" not in out and "group_id" not in out and "post_id" not in out


def test_sheet_comment_fallbacks():
    assert scraper.sheet_comment({"parent_comment_id": "5"})["parent_id"] == "5"
    assert scraper.sheet_comment({"comment_id": "9"})["reply_target_id"] == "9"
    assert scraper.sheet_comment({"is_reply": "yes"})["is_reply"] is True
    assert scraper.sheet_comment({"mentions": ["123", "", "x"]})["mentions"] == ["123"]
    assert scraper.sheet_comment({"created_time": "٥ د"})["created_time"] == ""


def test_iso_time_keeps_local_wall_clock_with_offset():
    import datetime as _dt

    iso = scraper.iso_time("2026-10-22 13:05")
    parsed = _dt.datetime.fromisoformat(iso)
    assert parsed.utcoffset() is not None
    assert parsed.replace(tzinfo=None) == _dt.datetime(2026, 10, 22, 13, 5)
    assert scraper.iso_time("garbage") == "" and scraper.iso_time("") == ""


def test_sheet_post_payload_contract():
    post = {"post_id": P, "author": "A", "author_id": "1", "post_content": "content", "media_url": "https://m",
            "text": "t", "created_time": "2026-10-22 13:05", "url": "u", "permalink": "p", "comment_sort": "الأحدث"}
    comments = [scraper.normalize_comment({"link": link("1")}, G, P)]
    payload = scraper.sheet_post_payload(G, post, comments, group_name="اسم", account_id="777")
    assert list(payload.keys()) == ["api_version", "action", "auth", "payload"]
    assert payload["api_version"] == 2 and payload["action"] == "ingest"
    assert payload["auth"] == {"kind": "ingest", "key": scraper.INGEST_KEY}
    body = payload["payload"]
    assert body["group_id"] == G and body["account_id"] == "777"
    (p,) = body["posts"]
    assert list(p.keys()) == ["post_id", "group_name", "author", "author_id", "text", "media_url", "created_time",
                              "url", "comment_sort", "comments"]
    assert p["text"] == "t" and p["media_url"] == "https://m" and p["group_name"] == "اسم"
    assert "T13:05:00" in p["created_time"]
    assert list(p["comments"][0].keys()) == SHEET_COMMENT_KEYS


def test_sheet_post_payload_defaults_and_no_caption_copy_in_media_url():
    p = scraper.sheet_post_payload(G, {"post_id": P, "text": "t", "post_content": "t", "permalink": "pl"}, [])["payload"]["posts"][0]
    assert p["comment_sort"] == "الأحدث"
    assert p["media_url"] == ""
    assert p["url"] == "pl"
    assert p["author"] == p["author_id"] == p["created_time"] == ""
    assert p["comments"] == []


def test_sheet_post_payload_does_not_send_author_url_or_content_kind():
    p = scraper.sheet_post_payload(G, {"post_id": P, "author_url": "x", "content_kind": "video"}, [])["payload"]["posts"][0]
    assert "author_url" not in p and "content_kind" not in p


# ---------------------------------------------------------------- sheet_post transport (real loopback HTTP)

def env_body(**extra):
    return dict({"api_version": 2, "request_id": "r1", "action": "ingest", "auth": {"kind": "ingest", "key": "k"}, "payload": {}}, **extra)


OK1 = '{"ok":true,"request_id":"r1","posts_new":1}'


@pytest.fixture
def sheet(http_server, monkeypatch):
    monkeypatch.setattr(scraper, "WEBAPP", http_server.url + "/exec")
    return http_server


def test_sheet_post_sends_utf8_json_post_with_a_request_id(sheet):
    sheet.respond(body='{"ok":true,"request_id":"fixed","posts_new":1}')
    body = env_body(text="عربي 🌙")
    del body["request_id"]
    with pytest.raises(SystemExit):           # the generated id does not match the canned answer -> unknown outcome
        scraper.sheet_post(dict(body))
    sheet.requests.clear()
    sheet.respond(body=OK1)
    answer = scraper.sheet_post(env_body(text="عربي 🌙"))
    assert answer == OK1
    req = sheet.requests[-1]
    assert req["method"] == "POST" and req["path"] == "/exec"
    assert req["headers"]["Content-Type"] == "application/json"
    assert "عربي 🌙".encode("utf-8") in req["body"]          # ensure_ascii=False
    body = json.loads(req["body"])
    assert body["text"] == "عربي 🌙" and body["request_id"] == "r1"


def test_sheet_post_generates_a_request_id_when_missing(sheet, no_sleep):
    sheet.respond(body=OK1)
    body = env_body()
    del body["request_id"]
    with pytest.raises(SystemExit):
        scraper.sheet_post(body)
    assert re.fullmatch(r"[0-9a-f-]{36}", json.loads(sheet.requests[0]["body"])["request_id"])


def test_sheet_post_retries_an_answer_that_is_not_for_this_request(sheet, no_sleep):
    """P4: a doGet ping came back for a POST. That is not proof the rows were written: ask again, same id."""
    sheet.queue(body='{"ok":true,"api_version":2,"schema_version":"2"}')
    sheet.queue(body=OK1)
    assert scraper.sheet_post(env_body()) == OK1
    assert len(sheet.requests) == 2 and sheet.requests[0]["body"] == sheet.requests[1]["body"]


def test_sheet_post_stops_when_apps_script_says_not_ok(sheet):
    """Fixed D-02: {"ok": false} used to be printed and ignored."""
    sheet.respond(body='{"ok":false,"code":"E_AUTH","error":"bad key"}')
    with pytest.raises(SystemExit) as exc:
        scraper.sheet_post(env_body())
    assert "E_AUTH" in str(exc.value)


@pytest.mark.parametrize("code", ["E_MAINTENANCE", "E_SCHEMA", "E_VERSION", "E_VALIDATION"])
def test_sheet_post_gate_codes_stop_without_retry(sheet, monkeypatch, code):
    monkeypatch.setattr(scraper.time, "sleep", lambda s: None)
    sheet.respond(body=json.dumps({"ok": False, "code": code, "error": "x"}))
    with pytest.raises(SystemExit):
        scraper.sheet_post(env_body())
    assert len(sheet.requests) == 1


def test_sheet_post_stops_on_non_json_answer(sheet):
    sheet.respond(body="<html>Google error page</html>", content_type="text/html")
    with pytest.raises(SystemExit) as exc:
        scraper.sheet_post(env_body())
    assert "Google error page" in str(exc.value)


@pytest.mark.parametrize("body", ['{"posts_new": 1}', "[]", '"ok"'])
def test_sheet_post_stops_without_explicit_ok_true(sheet, body):
    sheet.respond(body=body)
    with pytest.raises(SystemExit):
        scraper.sheet_post(env_body())


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(scraper.time, "sleep", slept.append)
    return slept


def test_sheet_post_http_5xx_retried_then_systemexit(sheet, no_sleep):
    """Fixed D-30: one transient Apps Script HTTP 500 used to abort the whole run (seen live)."""
    sheet.respond(status=500, body="x" * 2000)
    with pytest.raises(SystemExit) as exc:
        scraper.sheet_post(env_body())
    msg = str(exc.value)
    assert "HTTP 500" in msg and "محاولة 3" in msg
    assert msg.count("x") == 300
    assert len(sheet.requests) == scraper.SHEET_ATTEMPTS == 3
    assert no_sleep == [5, 15]


def test_sheet_post_5xx_then_success_reuses_the_request_id(sheet, no_sleep):
    sheet.queue(status=500, body="<html>We're sorry, a server error occurred.</html>", content_type="text/html")
    sheet.queue(status=200, body=OK1)
    assert scraper.sheet_post(env_body()) == OK1
    assert len(sheet.requests) == 2 and sheet.requests[0]["body"] == sheet.requests[1]["body"]
    assert no_sleep == [5]


@pytest.mark.parametrize("status", [404, 429])
def test_sheet_post_transient_404_and_429_are_retried(sheet, no_sleep, status):
    """Seen in RUN6: Google answered 404 «يتعذر فتح الملف في الوقت الحالي» after 110 good requests."""
    sheet.queue(status=status, body="<html>عذرًا، يتعذر فتح الملف في الوقت الحالي.</html>", content_type="text/html")
    sheet.queue(status=200, body=OK1)
    assert scraper.sheet_post(env_body()) == OK1
    assert len(sheet.requests) == 2 and no_sleep == [5]


def test_sheet_post_4xx_not_retried(sheet, no_sleep):
    sheet.respond(status=403, body="forbidden")
    with pytest.raises(SystemExit) as exc:
        scraper.sheet_post(env_body())
    assert "HTTP 403" in str(exc.value) and len(sheet.requests) == 1 and no_sleep == []


def test_sheet_post_busy_lock_retried(sheet, no_sleep):
    sheet.queue(body='{"ok":false,"code":"E_LOCK_BUSY","error":"script lock busy"}')
    sheet.queue(body=OK1)
    assert scraper.sheet_post(env_body()) == OK1
    assert no_sleep == [5]


def test_apps_script_error_extracts_readable_text():
    page = ("<!DOCTYPE html><html><head><script nonce='x'>window['ppConfig'] = {a: 1};</script>"
            "<style>.x{}</style></head><body><div>Exception: Service Spreadsheets timed out &amp; more</div></body></html>")
    assert scraper.apps_script_error(page) == "Exception: Service Spreadsheets timed out & more"


def test_sheet_post_network_error_retried_then_propagates(monkeypatch, no_sleep):
    from conftest import free_port

    monkeypatch.setattr(scraper, "WEBAPP", f"http://127.0.0.1:{free_port()}/exec")
    with pytest.raises(urllib.error.URLError):
        scraper.sheet_post(env_body())
    assert no_sleep == [5, 15]


def test_sheet_post_timeout_is_90_seconds_and_retried(monkeypatch, no_sleep):
    seen = []

    def fake_urlopen(req, timeout):
        seen.append(timeout)
        raise TimeoutError("timed out")

    monkeypatch.setattr(scraper, "WEBAPP", "http://127.0.0.1:1/exec")
    monkeypatch.setattr(scraper.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(TimeoutError):
        scraper.sheet_post(env_body())
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
    assert args.batch_size == 200 and args.workers == 1


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
    assert calls["sleep"] == []                                      # no fixed pause between posts


def test_archive_group_return_contract(flow):
    calls, data = flow
    data.update({"11": 1})
    out = scraper.archive_group(FakeTitlePage(), f"https://www.facebook.com/groups/{G}", 2, 5, 1, 10)
    assert list(out.keys()) == ["group_id", "group_url", "group_name", "sort_posts",
                                "feed_sort_label", "sort_comments", "limits", "posts_found",
                                "posts_shortfall", "posts", "posts_unavailable",   # + D-33: skipped deleted posts
                                "posts_sort_skipped"]                               # + no sort control, 2+ comments
    assert out["group_url"] == f"https://www.facebook.com/groups/{G}"
    assert out["sort_posts"] == "CHRONOLOGICAL" and out["sort_comments"] == "الأحدث"
    assert out["feed_sort_label"] == "label"
    assert out["limits"] == {"min_posts": 2, "max_posts": 5, "min_comments": 1, "max_comments": 10}
    assert out["posts_shortfall"] is True


def test_archive_group_name_falls_back_to_page_title(flow):
    """No h1 (FakeTitlePage has no DOM): group_name is the first '|' part of the page title."""
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
    assert [len(p["payload"]["posts"][0]["comments"]) for p in calls["sheet"]] == expected_sizes
    assert all(p["auth"]["kind"] == "ingest" and p["payload"]["group_id"] == G for p in calls["sheet"])
    assert all(p["payload"]["posts"][0]["post_id"] == "11" for p in calls["sheet"])
    assert out["posts"][0]["sheet_batches"] == len(expected_sizes)
    sent_ids = [c["comment_id"] for p in calls["sheet"] for c in p["payload"]["posts"][0]["comments"]]
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
    def archive(page, url, min_posts, max_posts, min_c, max_c, dry=True, batch_size=80, progress=None, sender=None):
        gid = scraper.group_key(url)
        return {"group_id": gid, "posts": posts_by_group.get(gid, [])}
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
        assert (scraper.INGEST_KEY or "no-key-configured") not in f.read_text(encoding="utf-8"), f.name
    assert json.loads((main_env["dumps"] / "1-20261021-120000.json").read_text(encoding="utf-8"))["group_id"] == "1"


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


def test_main_never_sends_the_group_result_itself(monkeypatch, main_env):
    """v2: posts go out only through archive_group's ingest payloads; the old whole-group fallback is gone."""
    monkeypatch.setattr(scraper, "archive_group", fake_archive({"1": [{"post_id": "9", "sheet_batches": 0}]}))
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
        return {"group_id": gid, "posts": []}
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
    # live 2026-10-22: RUN6 stored «(20+) name» for 30 groups — the counter can carry a «+»
    ("(20+) اسم الجروب | Facebook", "اسم الجروب"),
    ("(20+) Facebook", ""),                       # unavailable group: no name, not «Facebook»
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
        progress({"group_id": gid, "posts": [{"post_id": "9"}]})
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
    assert [p["payload"]["posts"][0]["post_id"] for p in calls["sheet"]] == ["11", "13"]


# ---------------------------------------------------------------- speed: polling, background Sheet sender, workers

class TickPage:
    """page.wait_for_timeout بيعدّ الوقت بس، من غير انتظار حقيقي."""
    def __init__(self):
        self.waited = []

    def wait_for_timeout(self, ms):
        self.waited.append(ms)


def test_wait_until_returns_as_soon_as_ready():
    page, checks = TickPage(), iter([False, False, True])
    assert scraper.wait_until(page, lambda: next(checks), 4000) is True
    assert page.waited == [scraper.POLL_MS, scraper.POLL_MS]


def test_wait_until_never_waits_longer_than_the_old_fixed_wait():
    page = TickPage()
    assert scraper.wait_until(page, lambda: False, 1100) is False
    assert sum(page.waited) == 1100 and max(page.waited) <= scraper.POLL_MS


def test_wait_until_ready_at_once_does_not_wait():
    page = TickPage()
    assert scraper.wait_until(page, lambda: True, 4000) is True and page.waited == []


def test_max_comments_all_means_no_limit(monkeypatch):
    args = parse(monkeypatch, "--group", "https://www.facebook.com/groups/1", "--min-posts", "1",
                 "--max-posts", "20", "--min-comments", "0", "--max-comments", "all")
    assert args.max_comments == scraper.NO_LIMIT


def test_max_comments_rejects_words_other_than_all(monkeypatch):
    with pytest.raises(SystemExit):
        parse(monkeypatch, "--group", "https://www.facebook.com/groups/1", "--min-posts", "1",
              "--max-posts", "20", "--min-comments", "0", "--max-comments", "many")


def test_workers_limit_is_six(monkeypatch):
    """Owner raised the cap from 4 to 6 on 2026-10-24; more is still refused."""
    assert scraper.MAX_WORKERS == 6
    assert parse(monkeypatch, *BASE, "--workers", "6").workers == 6
    with pytest.raises(SystemExit, match="workers"):
        parse(monkeypatch, *BASE, "--workers", "7")


def test_workers_cannot_combine_with_debug(monkeypatch):
    with pytest.raises(SystemExit, match="workers 1"):
        parse(monkeypatch, *BASE, "--workers", "4", "--debug")


def test_child_args_run_one_group_in_a_new_window_with_the_same_limits(monkeypatch):
    args = parse(monkeypatch, *BASE, "--workers", "3", "--dry", "--batch-size", "50")
    cmd = scraper.child_args(args, "https://www.facebook.com/groups/7", "20261022-120000")
    assert cmd[cmd.index("--group") + 1] == "https://www.facebook.com/groups/7" and cmd.count("--group") == 1
    assert cmd[cmd.index("--max-posts") + 1] == "5" and cmd[cmd.index("--batch-size") + 1] == "50"
    assert "--new-window" in cmd and "--dry" in cmd and "--workers" not in cmd
    assert cmd[cmd.index("--run-stamp") + 1] == "20261022-120000"


def sender_payload(post_id, n, group=G):
    return scraper.sheet_post_payload(group, {"post_id": post_id}, [{"comment_id": f"{post_id}{i}"} for i in range(n)])


def test_sheet_sender_merges_queued_posts_within_the_batch_size():
    sender = scraper.SheetSender(batch_size=5)
    merged = sender._merged([sender_payload("1", 2), sender_payload("2", 3), sender_payload("3", 1),
                             sender_payload("4", 0, group="other")])
    sender.close()
    assert [[p["post_id"] for p in m["payload"]["posts"]] for m in merged] == [["1", "2"], ["3"], ["4"]]
    assert all(scraper.SheetSender._comments(m) <= 5 for m in merged)


def test_sheet_sender_joins_chunks_of_the_same_post_into_one_entry():
    """الشيت بياخد أول ظهور للمنشور في الطلب بس، فالتعليقات لازم تبقى في entry واحد."""
    sender = scraper.SheetSender(batch_size=10)
    merged = sender._merged([sender_payload("1", 2), sender_payload("1", 3)])
    sender.close()
    assert len(merged) == 1 and len(merged[0]["payload"]["posts"]) == 1
    assert len(merged[0]["payload"]["posts"][0]["comments"]) == 5


def test_sheet_sender_does_not_mutate_the_callers_payload():
    first = sender_payload("1", 2)
    sender = scraper.SheetSender(batch_size=10)
    sender._merged([first, sender_payload("1", 3)])
    sender.close()
    assert len(first["payload"]["posts"][0]["comments"]) == 2


def test_sheet_sender_sends_everything_before_close_returns(monkeypatch):
    sent = []
    monkeypatch.setattr(scraper, "sheet_post", lambda payload: sent.append(payload) or '{"ok":true}')
    sender = scraper.SheetSender(batch_size=3)
    for i in range(5):
        sender.put(sender_payload(str(i), 2))
    sender.close()
    assert sorted(c["comment_id"] for p in sent for post in p["payload"]["posts"] for c in post["comments"]) == \
        sorted(f"{i}{j}" for i in range(5) for j in range(2))
    assert all(scraper.SheetSender._comments(p) <= 3 for p in sent)


def test_sheet_sender_failure_stops_the_scrape(monkeypatch):
    def refuse(payload):
        raise SystemExit("الشيت رفض البيانات")
    monkeypatch.setattr(scraper, "sheet_post", refuse)
    sender = scraper.SheetSender(batch_size=3)
    sender.put(sender_payload("1", 1))
    with pytest.raises(SystemExit, match="الشيت رفض"):
        sender.close()
    with pytest.raises(SystemExit, match="الإرسال للشيت وقف"):
        sender.put(sender_payload("2", 1))


def test_archive_group_hands_sheet_payloads_to_the_sender(flow):
    calls, data = flow
    data.update({"11": 3, "12": 0})

    class Sender:
        def __init__(self):
            self.payloads = []

        def put(self, payload):
            self.payloads.append(payload)

        def raise_if_failed(self):
            pass

    sender = Sender()
    out = scraper.archive_group(FakeTitlePage(), f"https://www.facebook.com/groups/{G}", 1, 5, 0, 10,
                                dry=False, sender=sender)
    assert calls["sheet"] == []                                       # nothing sent on the scraping thread
    assert [p["payload"]["posts"][0]["post_id"] for p in sender.payloads] == ["11", "12"]
    assert [p["sheet_batches"] for p in out["posts"]] == [1, 1]


def test_archive_group_lists_sort_skipped_posts_without_sending_them(flow, monkeypatch):
    calls, data = flow
    data.update({"11": 2, "12": 1})
    real = scraper.scrape_post

    def scrape(page, group_id, post_id, *a, **k):
        if post_id == "11":
            return scraper.unavailable_post(group_id, post_id, "u", reason=scraper.SORT_SKIPPED)
        return real(page, group_id, post_id, *a, **k)
    monkeypatch.setattr(scraper, "scrape_post", scrape)
    out = scraper.archive_group(FakeTitlePage(), f"https://www.facebook.com/groups/{G}", 1, 5, 0, 10, dry=False)
    assert out["posts_sort_skipped"] == ["11"] and out["posts_unavailable"] == []
    assert [p["post_id"] for p in out["posts"]] == ["12"]
    assert [p["payload"]["posts"][0]["post_id"] for p in calls["sheet"]] == ["12"]


# ---------------------------------------------------------------- no sort control helpers (2026-10-22)

def c(cid, time, reply_of=""):
    return {"comment_id": cid, "created_time": time, "is_reply": bool(reply_of), "parent_id": reply_of}


def test_newest_first_threads_orders_top_level_by_time_and_keeps_replies_under_their_comment():
    ordered = scraper.newest_first_threads([
        c("1", "2026-10-22 10:00"), c("1r", "2026-10-22 12:00", reply_of="1"),
        c("2", "2026-10-22 11:00"), c("3", "2026-10-22 09:00"), c("2r", "2026-10-22 11:30", reply_of="2"),
    ])
    assert [x["comment_id"] for x in ordered] == ["2", "2r", "1", "1r", "3"]


def test_newest_first_threads_keeps_orphan_replies_and_ties_in_page_order():
    ordered = scraper.newest_first_threads([c("a", "2026-10-22 10:00"), c("b", "2026-10-22 10:00"),
                                            c("x", "2026-10-22 09:00", reply_of="gone")])
    assert [x["comment_id"] for x in ordered] == ["a", "b", "x"]


class CounterPage:
    def __init__(self, text):
        self.text = text

    def evaluate(self, script, arg=None):
        return self.text


@pytest.mark.parametrize("text, total", [("٦", 6), ("12", 12), ("١٥", 15), (None, None), ("1.2 ألف", None),
                                         ("1.2K", None), ("", None)])
def test_comment_total_reads_exact_counts_only(text, total):
    assert scraper.comment_total(CounterPage(text)) == total


# ---------------------------------------------------------------- account blocked (S7) + --resume (S5)

class UrlPage:
    def __init__(self, url, cookies=("c_user", "xs")):
        self.url = url
        self.context = self
        self._cookies = [{"name": n} for n in cookies]

    def cookies(self, _url):
        return self._cookies


@pytest.mark.parametrize("url, blocked", [
    ("https://www.facebook.com/groups/1/posts/2/", False),
    ("https://www.facebook.com/login/?next=https%3A%2F%2Fwww.facebook.com%2Fgroups%2F1", True),
    ("https://www.facebook.com/login.php?next=x", True),
    ("https://www.facebook.com/checkpoint/1501092823525282/?next=x", True),
    ("https://www.facebook.com/two_step_verification/authentication/", True),
    ("https://www.facebook.com/groups/loginhelp", False),        # a group id that only starts with "login"
    ("https://www.facebook.com/loginhelp", False),
])
def test_account_problem_by_url(url, blocked):
    assert bool(scraper.account_problem(UrlPage(url))) is blocked


def test_account_problem_when_the_session_cookie_is_gone():
    assert "c_user" in scraper.account_problem(UrlPage("https://www.facebook.com/groups/1", cookies=("datr",)))


def test_stop_if_account_blocked_raises_a_distinct_exit_code():
    with pytest.raises(scraper.AccountBlocked) as info:
        scraper.stop_if_account_blocked(UrlPage("https://www.facebook.com/checkpoint/1/"))
    assert info.value.code == scraper.ACCOUNT_EXIT and isinstance(info.value, SystemExit)


def worker_cmd(code, sleep=0.0):
    return [sys.executable, "-c", f"import time; print('w'); time.sleep({sleep}); raise SystemExit({code})"]


def run_workers(monkeypatch, tmp_path, plan, workers):
    """plan: group id -> (exit code, seconds). Real child processes, no browser."""
    monkeypatch.setattr(scraper, "DUMP_DIR", tmp_path)
    monkeypatch.setattr(scraper, "child_args", lambda args, url, stamp: worker_cmd(*plan[scraper.group_key(url)]))
    args = parse(monkeypatch, *BASE, "--workers", str(workers))
    args.group = [f"https://www.facebook.com/groups/{g}" for g in plan]
    code = scraper.run_parallel(args, "20261023-000000")
    return code, json.loads((tmp_path / "run-20261023-000000.json").read_text(encoding="utf-8"))


def test_workers_one_blocked_account_stops_the_whole_run(monkeypatch, tmp_path):
    t = __import__("time").time()
    code, summary = run_workers(monkeypatch, tmp_path, {"1": (scraper.ACCOUNT_EXIT, 0.5), "2": (0, 60),
                                                         "3": (0, 0)}, workers=2)
    by = {o["group"]: o for o in summary["groups"]}
    assert code == scraper.ACCOUNT_EXIT and summary["account_blocked"] is True
    assert by["1"]["exit"] == scraper.ACCOUNT_EXIT
    assert by["2"]["exit"] not in (0, None) and "another worker" in by["2"]["last_line"]   # terminated, not 60 s
    assert by["3"]["exit"] is None and "not started" in by["3"]["last_line"]
    assert __import__("time").time() - t < 30


def test_workers_an_ordinary_group_failure_does_not_stop_the_others(monkeypatch, tmp_path):
    code, summary = run_workers(monkeypatch, tmp_path, {"1": (1, 0), "2": (0, 0), "3": (0, 0)}, workers=2)
    assert code == 1 and summary["account_blocked"] is False
    assert [o["exit"] for o in summary["groups"]] == [1, 0, 0]


def test_finished_groups_reads_a_workers_summary(monkeypatch, tmp_path):
    monkeypatch.setattr(scraper, "DUMP_DIR", tmp_path)
    (tmp_path / "run-S.json").write_text(json.dumps({"groups": [
        {"group": "1", "exit": 0}, {"group": "2", "exit": 1}, {"group": "3", "exit": None}]}), encoding="utf-8")
    assert scraper.finished_groups("S") == {"1"}


def test_finished_groups_reads_a_sequential_summary(monkeypatch, tmp_path):
    monkeypatch.setattr(scraper, "DUMP_DIR", tmp_path)
    (tmp_path / "run-S.json").write_text(json.dumps([{"group_id": "1", "posts": []},
                                                     {"failed": "SystemExit: x"}]), encoding="utf-8")
    assert scraper.finished_groups("S") == {"1"}


def test_finished_groups_missing_summary_stops(monkeypatch, tmp_path):
    monkeypatch.setattr(scraper, "DUMP_DIR", tmp_path)
    with pytest.raises(SystemExit, match="--resume"):
        scraper.finished_groups("nope")


def test_resume_skips_finished_groups_and_returns_when_nothing_is_left(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(scraper, "DUMP_DIR", tmp_path)
    (tmp_path / "run-S.json").write_text(json.dumps({"groups": [{"group": "1", "exit": 0}]}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["fb_groups_to_sheet.py", *BASE, "--resume", "S"])
    scraper.main()                                    # group 1 is the only group and it finished: no browser
    assert "فاضل 0" in capsys.readouterr().err
