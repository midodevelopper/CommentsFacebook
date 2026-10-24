"""قفل التنفيذ المحلي: PID + وقت بداية العملية + اسم الجهاز.

القفل «قديم» بس لو العملية مش شغالة، أو شغالة بوقت بداية مختلف (PID اتعاد استخدامه بعد إعادة تشغيل).
ده بيحمي من تشغيلين على نفس الجهاز بس؛ الناس اللي بتنادي الـ web app من برّه بيوقفهم الـ gate في السيرفر.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

from .state import atomic_write, now


def process_start_time(pid: int) -> float | None:
    """وقت بداية العملية بالثواني (أي مرجع ثابت)، أو None لو مش شغالة."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                            ctypes.byref(kernel), ctypes.byref(user)):
                return None
            code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value != 259:  # STILL_ACTIVE
                return None
            return ((created.dwHighDateTime << 32) | created.dwLowDateTime) / 1e7
        finally:
            kernel32.CloseHandle(handle)
    stat = Path(f"/proc/{pid}/stat")
    if not stat.exists():
        return None
    return float(stat.read_text().rsplit(")", 1)[1].split()[19])


class LockHeld(Exception):
    pass


def read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def is_stale(info: dict) -> bool:
    if info.get("hostname") != socket.gethostname():
        return False  # جهاز تاني: مانقدرش نحكم، فبنعتبره شغال
    started = process_start_time(int(info.get("pid", -1)))
    return started is None or abs(started - float(info.get("process_start_time", -1))) > 1.0


def acquire(path: Path, phase: str, pid: int | None = None) -> dict:
    pid = pid or os.getpid()
    current = read(path)
    if current and current.get("pid") != pid and not is_stale(current):
        raise LockHeld(f"القفل مع PID {current.get('pid')} ({current.get('phase')}) من {current.get('acquired_at')}")
    info = {"pid": pid, "process_start_time": process_start_time(pid), "hostname": socket.gethostname(),
            "phase": phase, "acquired_at": now()}
    atomic_write(path, info)
    return info


def release(path: Path, pid: int | None = None) -> bool:
    pid = pid or os.getpid()
    current = read(path)
    if current and current.get("pid") == pid:
        path.unlink(missing_ok=True)
        return True
    return False


def break_stale(path: Path) -> dict | None:
    current = read(path)
    if current and is_stale(current):
        path.unlink(missing_ok=True)
        return current
    return None
