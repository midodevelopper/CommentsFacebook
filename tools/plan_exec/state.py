"""حالة التنفيذ: state.json بيتكتب ذرّي (ملف مؤقت + fsync + replace)، وبنسخة schema بتتأكد عند القراءة."""
from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from pathlib import Path

SCHEMA = 1
STATES = ("not_started", "running", "passed", "failed", "rolled_back", "blocked", "interrupted")
TERMINAL = ("passed", "failed", "rolled_back", "blocked")

# ترتيب المراحل وشروط دخول كل واحدة (§7.1). F بتشتغل في الآخر مهما حصل.
PHASES = ("B0", "E", "P0", "P1", "P2", "PX", "P3", "P4", "P5", "P6", "P7", "F")
REQUIRES = {
    "B0": (), "E": ("B0",), "P0": ("E",), "P1": ("P0",), "P2": ("P1",), "PX": ("P2",),
    "P3": ("PX",), "P4": ("P3",), "P5": ("P4",), "P6": ("P5",), "P7": ("P6",), "F": (),
}


class StateError(Exception):
    pass


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def new_state() -> dict:
    return {
        "schema": SCHEMA,
        "run_id": dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6],
        "created_utc": now(),
        "phases": {p: {"state": "not_started", "steps": []} for p in PHASES},
    }


def load(path: Path) -> dict:
    if not path.exists():
        return new_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise StateError(f"state.json مش JSON سليم: {exc}") from None
    if data.get("schema") != SCHEMA:
        raise StateError(f"state.json schema {data.get('schema')!r} مش {SCHEMA}")
    missing = [p for p in PHASES if p not in data.get("phases", {})]
    if missing:
        raise StateError(f"state.json ناقصه مراحل: {missing}")
    for name, phase in data["phases"].items():
        if phase.get("state") not in STATES:
            raise StateError(f"حالة مش معروفة للمرحلة {name}: {phase.get('state')!r}")
    return data


def can_start(data: dict, phase: str) -> tuple[bool, str]:
    if phase not in PHASES:
        return False, f"مرحلة مش معروفة: {phase}"
    if phase == "F":
        return True, ""
    for need in REQUIRES[phase]:
        state = data["phases"][need]["state"]
        if state != "passed":
            return False, f"{phase} محتاجة {need} passed (هي {state})"
    return True, ""


def next_phase(data: dict) -> str | None:
    """أول مرحلة مش passed. لو في مرحلة وقفت (failed/blocked/rolled_back)، اللي بعدها F."""
    for phase in PHASES:
        state = data["phases"][phase]["state"]
        if phase == "F":
            return None if state == "passed" else "F"
        if state in ("failed", "blocked", "rolled_back"):
            return "F"
        if state != "passed":
            return phase
    return None


def set_phase(data: dict, phase: str, state: str, **fields) -> dict:
    if state not in STATES:
        raise StateError(f"حالة مش معروفة: {state}")
    rec = data["phases"][phase]
    if state == "running" and rec["state"] == "not_started":
        rec["started_utc"] = now()
    if state in TERMINAL:
        rec["ended_utc"] = now()
    rec["state"] = state
    rec.update(fields)
    return data


def add_step(data: dict, phase: str, name: str, state: str, **fields) -> dict:
    steps = data["phases"][phase]["steps"]
    existing = next((s for s in steps if s["name"] == name), None)
    rec = existing or {"name": name}
    rec.update(state=state, at=now(), **fields)
    if not existing:
        steps.append(rec)
    return data
