"""منفّذ الخطة (tools/plan_exec): حالة ذرّية، ترتيب المراحل، القفل، إعادة المحاولة، والتقرير. كله offline."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import urllib.error

import pytest

from tools.plan_exec import client as clientmod
from tools.plan_exec import lock as lockmod
from tools.plan_exec import report as reportmod
from tools.plan_exec import state as statemod
from tools.plan_exec.__main__ import main as cli

pytestmark = pytest.mark.unit


# ------------------------------------------------------------------ state

def test_new_state_has_every_phase_not_started():
    data = statemod.new_state()
    assert data["schema"] == statemod.SCHEMA
    assert list(data["phases"]) == list(statemod.PHASES)
    assert all(p["state"] == "not_started" for p in data["phases"].values())


def test_atomic_write_leaves_no_temp_and_survives_crash_before_replace(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    statemod.atomic_write(path, {"a": 1})
    real = os.replace

    def boom(src, dst):
        raise OSError("power cut")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        statemod.atomic_write(path, {"a": 2})
    monkeypatch.setattr(os, "replace", real)
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}


def test_load_rejects_unknown_schema_and_bad_json(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(statemod.StateError):
        statemod.load(path)
    data = statemod.new_state()
    data["schema"] = 99
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(statemod.StateError):
        statemod.load(path)


def test_load_rejects_unknown_phase_state(tmp_path):
    path = tmp_path / "state.json"
    data = statemod.new_state()
    data["phases"]["P1"]["state"] = "half-done"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(statemod.StateError):
        statemod.load(path)


def test_graph_requires_previous_phase_passed():
    data = statemod.new_state()
    assert statemod.can_start(data, "B0")[0]
    assert not statemod.can_start(data, "E")[0]
    statemod.set_phase(data, "B0", "passed")
    assert statemod.can_start(data, "E")[0]
    assert not statemod.can_start(data, "P5")[0]


def test_finalizer_can_always_start():
    assert statemod.can_start(statemod.new_state(), "F")[0]


def test_next_phase_goes_to_finalizer_after_a_stop():
    data = statemod.new_state()
    for p in ("B0", "E", "P0", "P1", "P2", "PX", "P3", "P4", "P5"):
        statemod.set_phase(data, p, "passed")
    assert statemod.next_phase(data) == "P6"
    statemod.set_phase(data, "P6", "blocked")
    assert statemod.next_phase(data) == "F"
    assert not statemod.can_start(data, "P7")[0]


def test_next_phase_done_when_finalizer_passed():
    data = statemod.new_state()
    for p in statemod.PHASES:
        statemod.set_phase(data, p, "passed")
    assert statemod.next_phase(data) is None


def test_steps_are_upserted_by_name():
    data = statemod.new_state()
    statemod.add_step(data, "P3", "push", "running")
    statemod.add_step(data, "P3", "push", "passed", sha="x")
    steps = data["phases"]["P3"]["steps"]
    assert len(steps) == 1 and steps[0]["state"] == "passed" and steps[0]["sha"] == "x"


# ------------------------------------------------------------------ lock

def test_lock_acquire_release_and_reentry(tmp_path):
    path = tmp_path / "run.lock"
    info = lockmod.acquire(path, "P1")
    assert info["pid"] == os.getpid() and info["process_start_time"]
    lockmod.acquire(path, "P2")   # نفس العملية: مسموح
    assert lockmod.release(path)
    assert not path.exists()


def test_lock_held_by_live_other_process_is_refused(tmp_path):
    path = tmp_path / "run.lock"
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        lockmod.acquire(path, "P1", pid=proc.pid)
        with pytest.raises(lockmod.LockHeld):
            lockmod.acquire(path, "P2")
        assert lockmod.break_stale(path) is None
    finally:
        proc.kill()
        proc.wait()


def test_lock_of_dead_process_is_stale_and_breakable(tmp_path):
    path = tmp_path / "run.lock"
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    lockmod.acquire(path, "P1", pid=proc.pid)
    assert lockmod.is_stale(lockmod.read(path))
    lockmod.acquire(path, "P2")   # القديم مات: الجديد ياخده
    assert lockmod.read(path)["pid"] == os.getpid()


def test_lock_with_reused_pid_but_different_start_time_is_stale(tmp_path):
    path = tmp_path / "run.lock"
    info = lockmod.acquire(path, "P1")
    info["process_start_time"] -= 1000
    path.write_text(json.dumps(info), encoding="utf-8")
    assert lockmod.is_stale(lockmod.read(path))


def test_lock_from_another_host_is_never_stale(tmp_path):
    info = {"pid": 1, "process_start_time": 0, "hostname": "other-machine"}
    assert not lockmod.is_stale(info)


# ------------------------------------------------------------------ client

class FakeResp:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self):
        return self._body.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def scripted(responses, seen):
    """opener بيرجّع ردود متسجّلة بالترتيب، وبيحفظ كل جسم طلب."""
    def opener(req, timeout):
        seen.append(json.loads(req.data.decode()))
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        status, body = item
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "x", {}, io.BytesIO(body.encode()))
        return FakeResp(status, body)
    return opener


def test_client_retries_transient_http_with_same_request_id_and_three_waits():
    seen, waits = [], []
    responses = [(503, "busy"), (429, "slow"), (404, "يتعذر فتح الملف"), (200, json.dumps({"ok": True, "result": 1}))]
    c = clientmod.Client("https://x/exec", "k", sleep=waits.append, opener=scripted(responses, seen))
    answer = c.call("verify", request_id="r-1")
    assert answer["result"] == 1
    assert len(seen) == 4 and {s["request_id"] for s in seen} == {"r-1"}
    assert len(waits) == 3
    for wait, base in zip(waits, clientmod.RETRY_WAITS):
        assert base * 0.8 <= wait <= base * 1.2


def test_client_gives_up_after_one_plus_three_attempts():
    seen = []
    c = clientmod.Client("https://x/exec", "k", sleep=lambda s: None,
                         opener=scripted([(500, "")] * 4, seen))
    with pytest.raises(clientmod.ApiError) as err:
        c.call("verify")
    assert err.value.code == "E_RETRY_EXHAUSTED" and len(seen) == 4


def test_client_does_not_retry_non_retryable_codes():
    seen = []
    body = json.dumps({"ok": False, "code": "E_DNC", "error": "blocked"})
    c = clientmod.Client("https://x/exec", "k", sleep=lambda s: None, opener=scripted([(200, body)], seen))
    with pytest.raises(clientmod.ApiError) as err:
        c.call("claim")
    assert err.value.code == "E_DNC" and len(seen) == 1


def test_client_retries_lock_busy_and_internal_only_once():
    seen = []
    busy = json.dumps({"ok": False, "code": "E_LOCK_BUSY"})
    internal = json.dumps({"ok": False, "code": "E_INTERNAL"})
    c = clientmod.Client("https://x/exec", "k", sleep=lambda s: None,
                         opener=scripted([(200, busy), (200, internal), (200, internal)], seen))
    with pytest.raises(clientmod.ApiError) as err:
        c.call("claim")
    assert err.value.code == "E_INTERNAL" and len(seen) == 3


def test_client_continues_budget_cursor_without_counting_failures():
    seen = []
    part = json.dumps({"ok": False, "code": "E_BUDGET", "cursor": 500, "result": {"rows": 500}})
    done = json.dumps({"ok": True, "result": {"rows": 946}})
    c = clientmod.Client("https://x/exec", "k", sleep=lambda s: None, opener=scripted([(200, part), (200, done)], seen))
    answer = c.call("migrate_step", request_id="m")
    assert answer["ok"] and answer["continued"] == [{"rows": 500}]
    assert seen[1]["cursor"] == 500 and seen[1]["request_id"] != seen[0]["request_id"]


def test_client_puts_key_only_in_post_body():
    seen = []
    c = clientmod.Client("https://x/exec", "secret-k", sleep=lambda s: None,
                         opener=scripted([(200, json.dumps({"ok": True}))], seen))
    c.call("ping")
    assert seen[0]["auth"] == {"kind": "admin", "key": "secret-k"}
    assert "secret-k" not in c.url


def test_client_network_error_is_retried():
    seen = []
    c = clientmod.Client("https://x/exec", "k", sleep=lambda s: None,
                         opener=scripted([urllib.error.URLError("down"), (200, json.dumps({"ok": True}))], seen))
    assert c.call("ping")["ok"]


# ------------------------------------------------------------------ report + CLI

def test_outcome_flags_never_claim_untested_features():
    data = statemod.new_state()
    flags = reportmod.outcome_flags(data)
    assert flags["implementation_complete"] == "not_run"
    assert flags["reply_sending_verified"] == "not_run"
    assert flags["post_replies"] == "disabled" and flags["dashboard_visual"] == "unverified"
    for p in ("E", "P1", "P2", "PX"):
        statemod.set_phase(data, p, "passed")
    statemod.set_phase(data, "P7", "passed", dry_run=True, send="blocked: F-B2")
    flags = reportmod.outcome_flags(data)
    assert flags["implementation_complete"] is True
    assert flags["reply_sending_verified"] == "blocked: F-B2"


def test_cli_start_refuses_out_of_order_and_records_progress(tmp_path, capsys):
    assert cli(["start", "E"], base=tmp_path) == 2
    assert cli(["start", "B0"], base=tmp_path) == 0
    assert cli(["finish", "B0", "passed"], base=tmp_path) == 0
    assert cli(["start", "E"], base=tmp_path) == 0
    data = statemod.load(tmp_path / "state.json")
    assert data["phases"]["B0"]["state"] == "passed" and data["phases"]["E"]["state"] == "running"
    assert cli(["report"], base=tmp_path) == 0
    assert (tmp_path / f"report-{data['run_id']}.md").exists()
