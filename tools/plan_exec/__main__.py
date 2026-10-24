"""سطر أوامر المنفّذ.

  python -m tools.plan_exec status
  python -m tools.plan_exec next
  python -m tools.plan_exec start P1
  python -m tools.plan_exec step P1 build-fake passed --json details.json
  python -m tools.plan_exec finish P1 passed
  python -m tools.plan_exec report
  python -m tools.plan_exec lock show|break-stale
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

from . import EXEC_DIR
from . import lock as lockmod
from . import report as reportmod
from . import state as statemod


def paths(base: Path) -> tuple[Path, Path]:
    return base / "state.json", base / "run.lock"


def main(argv: list[str] | None = None, base: Path = EXEC_DIR) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(prog="plan_exec")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("next")
    p = sub.add_parser("start"); p.add_argument("phase")
    p = sub.add_parser("finish"); p.add_argument("phase"); p.add_argument("state"); p.add_argument("--json")
    p.add_argument("--reason", default="")
    p = sub.add_parser("step"); p.add_argument("phase"); p.add_argument("name"); p.add_argument("state")
    p.add_argument("--json")
    sub.add_parser("report")
    p = sub.add_parser("lock"); p.add_argument("action", choices=["show", "break-stale", "release"])
    p.add_argument("--pid", type=int)
    args = parser.parse_args(argv)
    state_path, lock_path = paths(base)
    data = statemod.load(state_path)

    def extra(path: str | None) -> dict:
        return json.loads(Path(path).read_text(encoding="utf-8")) if path else {}

    if args.cmd == "status":
        for phase in statemod.PHASES:
            rec = data["phases"][phase]
            print(f"{phase:3} {rec['state']:12} {rec.get('reason', '')}")
        print("next:", statemod.next_phase(data))
        return 0
    if args.cmd == "next":
        print(statemod.next_phase(data) or "done")
        return 0
    if args.cmd == "start":
        ok, why = statemod.can_start(data, args.phase)
        if not ok:
            print(why, file=sys.stderr)
            return 2
        statemod.set_phase(data, args.phase, "running")
        statemod.atomic_write(state_path, data)
        print(f"{args.phase} running (run {data['run_id']})")
        return 0
    if args.cmd == "finish":
        statemod.set_phase(data, args.phase, args.state, reason=args.reason, **extra(args.json))
        statemod.atomic_write(state_path, data)
        print(f"{args.phase} {args.state}")
        return 0
    if args.cmd == "step":
        statemod.add_step(data, args.phase, args.name, args.state, **extra(args.json))
        statemod.atomic_write(state_path, data)
        return 0
    if args.cmd == "report":
        out = reportmod.write(data, base)
        print(out)
        return 0
    if args.cmd == "lock":
        if args.action == "show":
            print(json.dumps(lockmod.read(lock_path), ensure_ascii=False))
        elif args.action == "break-stale":
            broken = lockmod.break_stale(lock_path)
            print("broken" if broken else "not stale / absent")
        else:
            print("released" if lockmod.release(lock_path, args.pid) else "not owner")
        return 0
    return 1


def interrupted(phase_getter, base: Path = EXEC_DIR):
    """Ctrl+C / Ctrl+Break: المرحلة الجارية تتعلّم interrupted والقفل يتساب، والخروج 130."""
    def handler(signum, frame):
        state_path, lock_path = paths(base)
        try:
            data = statemod.load(state_path)
            phase = phase_getter()
            if phase:
                statemod.set_phase(data, phase, "interrupted")
                statemod.atomic_write(state_path, data)
        finally:
            lockmod.release(lock_path)
        raise SystemExit(130)
    for sig in (signal.SIGINT, getattr(signal, "SIGBREAK", None)):
        if sig is not None:
            signal.signal(sig, handler)
    return handler


if __name__ == "__main__":
    raise SystemExit(main())
