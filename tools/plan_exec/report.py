"""التقرير النهائي: حالة كل مرحلة + أعلام النتيجة (§13). مفيش أي قيمة خاصة، بصمات بس."""
from __future__ import annotations

import json
from pathlib import Path

from .state import PHASES, atomic_write

FLAGS = {
    "implementation_complete": ("E", "P1", "P2", "PX"),
    "rehearsal_verified": ("P4",),
    "migration_verified": ("P5",),
    "live_scraping_verified": ("P6",),
}


def outcome_flags(data: dict) -> dict:
    phases = data["phases"]
    out: dict = {}
    for flag, needed in FLAGS.items():
        states = [phases[p]["state"] for p in needed]
        out[flag] = True if all(s == "passed" for s in states) else (
            "not_run" if all(s == "not_started" for s in states) else False)
    p7 = phases["P7"]
    out["reply_dry_run_verified"] = p7.get("dry_run") if p7.get("dry_run") is not None else (
        "not_run" if p7["state"] == "not_started" else False)
    out["reply_sending_verified"] = p7.get("send", "not_run")
    out["nested_reply_targets"] = phases["P6"].get("nested_targets", "disabled")
    out["post_replies"] = "disabled"
    out["dashboard_visual"] = "unverified"
    return out


def write(data: dict, base: Path) -> Path:
    flags = outcome_flags(data)
    payload = {"run_id": data["run_id"], "flags": flags,
               "phases": {p: {k: v for k, v in data["phases"][p].items() if k != "steps"} for p in PHASES}}
    atomic_write(base / f"report-{data['run_id']}.json", payload)
    lines = [f"# Plan execution report {data['run_id']}", "", "| Phase | State | Reason |", "|---|---|---|"]
    lines += [f"| {p} | {data['phases'][p]['state']} | {data['phases'][p].get('reason', '')} |" for p in PHASES]
    lines += ["", "| Outcome | Value |", "|---|---|"] + [f"| {k} | {v} |" for k, v in flags.items()]
    out = base / f"report-{data['run_id']}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out
