"""P4: سيناريوهات التجربة على نسخ حقيقية من الشيت (كل سيناريو على نسخة جديدة). النسخة بيعملها Claude بموصّل
Drive وبتتسجّل هنا بالاسم؛ السكربت ده بيسمح بيها، يشغّل السيناريو، يتحقق، ويقفل السماح ويمسح الـ namespace بتاعها.

  python -m tools.plan_exec.rehearse register R01 <fileId>
  python -m tools.plan_exec.rehearse run R01
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
import uuid

from . import EXEC_DIR, ROOT
from .client import ApiError
from .prod import admin_client, ids, save_ids

P4 = EXEC_DIR / "P4"
RESULTS = P4 / "results.json"
FAULT_STEPS = {"R02": "M1:Posts", "R03": "M2:Posts", "R04": "M3:Posts:partial", "R05": "M4:Posts",
               "R06": "M1:Comments", "R07": "M2:Comments", "R08": "M3:Comments:partial", "R09": "M4:Comments"}
EXPECTED = {"Posts": 499, "Comments": 946, "sent": 21, "held": 91, "is_reply_true": 66, "media_url_blank": 318}


def group_names() -> dict:
    """أسماء الجروبات من دَمبات RUN6 (من غير عداد الإشعارات «(20+)»)."""
    import fb_groups_to_sheet as scraper

    names = {}
    for path in glob.glob(str(ROOT / "dumps" / "*-20261022-125227.json")):
        if "run-" in path:
            continue
        data = json.load(open(path, encoding="utf-8"))
        name = scraper.clean_title(data.get("group_name") or "")
        if data.get("group_id") and name:
            names[data["group_id"]] = name
    return names


def load_results() -> dict:
    return json.loads(RESULTS.read_text(encoding="utf-8")) if RESULTS.exists() else {}


def save_result(name: str, result: dict) -> None:
    P4.mkdir(parents=True, exist_ok=True)
    data = load_results()
    data[name] = result
    RESULTS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class Run:
    def __init__(self, name: str):
        self.name = name
        self.file = ids()["files"][name]
        self.client = admin_client("rehearsal")
        self.log: list = []

    def api(self, action, payload=None, target=None, request_id=None, expect_ok=True):
        try:
            answer = self.client.call(action, payload or {}, target=target or self.file, request_id=request_id)
            if "result" not in answer:
                # رد ناجح من غير result = حاجة غريبة (مثلًا redirect اتحوّل GET): نسجّل شكله ونقف
                self.log.append({"action": action, "ok": False, "code": "E_SHAPE", "keys": sorted(answer)})
                raise ApiError("E_SHAPE", f"{action}: answer without result: {sorted(answer)}", answer)
            self.log.append({"action": action, "ok": True})
            return answer
        except ApiError as exc:
            self.log.append({"action": action, "ok": False, "code": exc.code})
            if expect_ok:
                raise
            return {"ok": False, "code": exc.code, "error": str(exc)}

    def allow(self, file_id=None, mode="rw"):
        return self.api("allow_target", {"target_id": file_id or self.file, "mode": mode}, target=ids()["sheet_id"])

    def revoke(self, file_id=None):
        return self.api("revoke_target", {"target_id": file_id or self.file, "clear_namespace": True}, target=ids()["sheet_id"])

    def data_hash(self, target=None):
        tabs = self.api("snapshot_hash", {"tabs": ["Posts", "Comments"]}, target=target)["result"]["tabs"]
        return {k: v["content"] for k, v in tabs.items()}

    def migrate(self, **extra):
        payload = dict({"run": f"rehearsal-{self.name}", "group_names": group_names()}, **extra)
        return self.api("migrate_step", payload, expect_ok="fault" not in extra)

    def verify(self):
        return self.api("verify", {"run": f"rehearsal-{self.name}", "group_names": group_names()})["result"]


def check(cond: bool, what: str, problems: list) -> None:
    if not cond:
        problems.append(what)


def run_r01(r: Run) -> dict:
    problems = []
    pre = r.data_hash()
    prod = r.data_hash(target=ids()["sheet_id"])
    check(pre == prod, "copy content differs from production at start", problems)
    st = r.api("selftest")["result"]
    check(st["formula_mode"] in ("literal", "apostrophe"), f"formula mode unsafe: {st}", problems)
    check(st["grid"]["wrote"] and st["grid"]["checkbox"] and st["grid"]["dropdown"], f"grid/validation: {st['grid']}", problems)
    r.api("set_flags", {"formula_mode": st["formula_mode"]})
    out = r.migrate()["result"]
    check(out.get("complete") is True, f"migration incomplete: {out}", problems)
    v = r.verify()
    check(v["ok"], f"verify: {v['issues'][:5]}", problems)
    for k, want in EXPECTED.items():
        check(v["counts"].get(k) == want, f"count {k}={v['counts'].get(k)} != {want}", problems)
    h1 = r.data_hash()
    again = r.migrate()["result"]
    check(again.get("complete") is True and r.data_hash() == h1, "second migration changed data", problems)
    r.api("format")
    r.api("build_dashboard")
    inv0 = {s["name"]: s for s in r.api("inventory")["result"]["sheets"]}
    payload = {"group_id": "1000000000000001", "posts": [{
        "post_id": "9990000000000001", "group_name": "rehearsal", "author": "R", "author_id": "100000000000099",
        "text": "rehearsal post", "media_url": "", "created_time": "2026-10-24T10:00:00+00:00",
        "url": "https://www.facebook.com/groups/1000000000000001/posts/9990000000000001/", "comment_sort": "الأحدث",
        "comments": [{"comment_id": f"999000000000000{i}", "parent_id": "", "author": "U", "author_id": "100000000000005",
                      "text": f"=1+{i}" if i == 1 else f"c{i}", "created_time": "2026-10-24T10:05:00+00:00",
                      "reply_comment_id": "", "is_reply": False, "reply_target_id": f"999000000000000{i}", "author_url": "",
                      "url": f"https://www.facebook.com/groups/1000000000000001/posts/9990000000000001/?comment_id=999000000000000{i}"}
                     for i in (1, 2)]}]}
    ing = r.api("ingest_as", payload)["result"]
    check(ing["posts_new"] == 1 and ing["comments_new"] == 2, f"ingest: {ing}", problems)
    inv1 = {s["name"]: s for s in r.api("inventory")["result"]["sheets"]}
    check(inv1["Comments"]["last_row"] == inv0["Comments"]["last_row"] + 2, "comments rows +2", problems)
    check(inv1["Comments"]["max_rows"] > inv0["Comments"]["last_row"], "grid grew before append", problems)
    check(inv1["Comments"]["validations"] >= inv0["Comments"]["validations"] + 4, "checkbox+dropdown on new comment rows", problems)
    check(inv1["Posts"]["validations"] >= inv0["Posts"]["validations"] + 1, "dropdown on new post row", problems)
    check(inv1["Comments"]["formulas"] == 0, "a '=…' text became a formula", problems)
    dash = r.api("check_dashboard")["result"]
    check(dash["pivots_ok"], f"pivots: {[p for p in dash['unfiltered'] if p['mismatches']][:2]}", problems)
    check(dash["charts_ok"], f"charts: {dash['charts']}", problems)
    save_result("_reference", {"pre": pre, "migrated": h1})
    return {"ok": not problems, "problems": problems, "formula_mode": st["formula_mode"], "counts": v["counts"],
            "slicer_behaviour": dash["slicer_behaviour"], "slicers": dash["slicers"], "charts": dash["charts"]}


def run_fault(r: Run) -> dict:
    problems = []
    ref = load_results()["_reference"]
    check(r.data_hash() == ref["pre"], "copy differs from production at start", problems)
    first = r.migrate(fault={"after_step": FAULT_STEPS[r.name], "mode": "throw"})
    check(first.get("ok") is False, f"fault did not interrupt: {first}", problems)
    second = r.migrate()["result"]
    check(second.get("complete") is True, f"resume incomplete: {second}", problems)
    check(r.data_hash() == ref["migrated"], "resumed result differs from the clean migration", problems)
    inv = r.api("inventory")["result"]["sheets"]
    check(not [s for s in inv if s["name"].startswith("__v2_build")], "build sheet left behind", problems)
    return {"ok": not problems, "problems": problems, "fault": FAULT_STEPS[r.name], "first_code": first.get("code")}


def run_r10(r: Run) -> dict:
    problems = []
    ref = load_results()["_reference"]
    rid = str(uuid.uuid4())
    first = r.api("migrate_step", {"run": f"rehearsal-{r.name}", "group_names": group_names(),
                                   "fault": {"after_step": "migrate_step", "mode": "drop_response"}}, request_id=rid, expect_ok=False)
    check(first.get("ok") is False, "response was not dropped", problems)
    retry = r.client.call("migrate_step", {"run": f"rehearsal-{r.name}", "group_names": group_names()}, target=r.file, request_id=rid)
    check(retry.get("replayed") is True, f"retry not replayed: {retry.get('replayed')}", problems)
    check(r.data_hash() == ref["migrated"], "data differs after replay", problems)
    led = r.api("inventory")["result"]["sheets"]
    rows = next((s["last_row"] for s in led if s["name"] == "_replies"), 0)
    check(rows == 1 + 21 + 91, f"ledger rows {rows} != 113 (no duplicate events)", problems)
    return {"ok": not problems, "problems": problems}


def run_r11(r: Run) -> dict:
    problems = []
    pre = r.data_hash()
    r.migrate()
    back = r.api("rollback_step", {"run": f"rehearsal-{r.name}"})["result"]
    check(r.data_hash() == pre, "rollback did not restore v1 content", problems)
    flags = r.api("flags")["result"]
    check(flags["schema_version"] == "1", "schema not back to 1", problems)
    inv = {s["name"]: s for s in r.api("inventory")["result"]["sheets"]}
    check(inv["Posts"]["gid"] == 318060874 and inv["Comments"]["gid"] == 2107666404, "gids changed", problems)
    check(inv.get("_replies", {}).get("last_row", 0) == 113, "ledger not kept after rollback", problems)
    return {"ok": not problems, "problems": problems, "restored": back.get("restored")}


def run_r12(r: Run) -> dict:
    problems = []
    pre = r.data_hash()
    r.migrate()
    broken = r.api("rollback_step", {"run": f"rehearsal-{r.name}", "fault": {"after_step": "R:Posts:partial"}}, expect_ok=False)
    check(broken.get("ok") is False, "rollback fault did not interrupt", problems)
    r.api("rollback_step", {"run": f"rehearsal-{r.name}"})
    check(r.data_hash() == pre, "resumed rollback did not restore v1", problems)
    return {"ok": not problems, "problems": problems}


def run_r13(r: Run) -> dict:
    problems = []
    backup = ids()["files"]["R13-backup"]
    r.allow(backup, mode="ro")
    want = r.data_hash(target=backup)
    r.migrate()
    check(r.data_hash() != want, "migration did not change the copy", problems)
    for tab in ("Posts", "Comments"):
        r.api("restore_tab_from", {"source": backup, "tab": tab})
    check(r.data_hash() == want, "restore did not reproduce the backup content", problems)
    r.revoke(backup)
    return {"ok": not problems, "problems": problems}


def run_r14(r: Run) -> dict:
    problems = []
    pre = r.data_hash()
    out = r.migrate(fault={"after_step": "M1:Posts", "mode": "corrupt"})
    if out.get("ok") is not False:
        out = r.migrate()
    check(out.get("ok") is False and out.get("code") == "E_VALIDATION", f"corruption not detected: {out}", problems)
    check(r.data_hash() == pre, "original changed despite failed validation", problems)
    return {"ok": not problems, "problems": problems, "code": out.get("code")}


SCENARIOS = {"R01": run_r01, "R10": run_r10, "R11": run_r11, "R12": run_r12, "R13": run_r13, "R14": run_r14,
             **{k: run_fault for k in FAULT_STEPS}}


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(prog="plan_exec.rehearse")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("register"); s.add_argument("name"); s.add_argument("file_id")
    s = sub.add_parser("run"); s.add_argument("name")
    args = p.parse_args(argv)
    if args.cmd == "register":
        data = ids()
        data.setdefault("files", {})[args.name] = args.file_id
        save_ids(data)
        print("registered", args.name)
        return 0
    r = Run(args.name)
    r.allow()
    try:
        result = SCENARIOS[args.name](r)
    except ApiError as exc:
        result = {"ok": False, "problems": [f"{exc.code}: {exc}"]}
    finally:
        try:
            r.revoke()
        except ApiError:
            pass
    result["calls"] = r.log
    save_result(args.name, result)
    print(json.dumps({k: v for k, v in result.items() if k != "calls"}, ensure_ascii=False)[:1500])
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
