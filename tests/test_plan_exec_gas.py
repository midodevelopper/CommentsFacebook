"""tools/plan_exec/gas.py against a fake clasp: staging allowlist, readback, idempotent version/deployment
creation, resumable re-pointing of every deployment, the v1-exposure check, and the drain rule. Offline only."""
from __future__ import annotations

import json
import re

import pytest

from tools.plan_exec import gas

pytestmark = pytest.mark.unit

H = "a" * 64


class FakeClasp:
    """Answers like clasp 3.4.1; can fail after N update calls to simulate an interrupted cut-over."""

    def __init__(self, deployments, versions=None, fail_after=None):
        self.deployments = deployments          # [{"id","version","description"}]
        self.versions = versions or [(n, f"v{n}") for n in range(1, 7)]
        self.calls = []
        self.updates = 0
        self.fail_after = fail_after

    def __call__(self, args):
        self.calls.append(args)
        if args[0] == "-P":
            args = args[2:]
        cmd = args[0]
        if cmd == "list-versions":
            return f"Found {len(self.versions)} versions.\n" + "\n".join(f"{n} - {d}" for n, d in self.versions)
        if cmd == "create-version":
            n = len(self.versions) + 1
            self.versions.append((n, args[1]))
            return f"Created version {n}."
        if cmd == "list-deployments":
            return f"Found {len(self.deployments)} deployments.\n" + "\n".join(
                f"- {d['id']} @{d['version']}" + (f" - {d['description']}" if d["description"] else "") for d in self.deployments)
        if cmd == "create-deployment":
            dep = {"id": f"AKfyNEW{len(self.deployments)}", "version": args[args.index("-V") + 1], "description": args[args.index("-d") + 1]}
            self.deployments.append(dep)
            return f"Deployed {dep['id']} @{dep['version']}."
        if cmd == "update-deployment":
            if self.fail_after is not None and self.updates >= self.fail_after:
                raise gas.GasError("network dropped")
            self.updates += 1
            dep = next(d for d in self.deployments if d["id"] == args[1])
            dep["version"] = args[args.index("-V") + 1]
            return f"Deployed {dep['id']} @{dep['version']}."
        return ""


def seven():
    return [{"id": "AKfyHEAD", "version": "HEAD", "description": ""}] + [
        {"id": f"AKfyV{n}", "version": str(n), "description": f"publish {n}"} for n in range(1, 7)]


def test_every_clasp_call_names_the_project_file_explicitly():
    fake = FakeClasp(seven())
    c = gas.Clasp(project=gas.PROJECT, runner=fake)
    c.push(); c.list_versions(); c.create_version("x")
    assert all(call[:2] == ["-P", str(gas.PROJECT)] for call in fake.calls)


def test_staging_holds_only_allowlisted_files_and_hashes_not_keys(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for name in gas.ALLOWLIST:
        (src / name).write_text("{}" if name.endswith(".json") else "// " + name, encoding="utf-8")
    (src / "notes.txt").write_text("private", encoding="utf-8")
    hashes = gas.build_staging("prod-id", H, "b" * 64, src=src, staging=tmp_path / "push")
    assert sorted(p.name for p in (tmp_path / "push").iterdir()) == sorted([*gas.ALLOWLIST, gas.GENERATED])
    cfg = (tmp_path / "push" / gas.GENERATED).read_text(encoding="utf-8")
    assert H in cfg and '"POST_REPLIES": false' in cfg and "prod-id" in cfg
    assert set(hashes) == {*gas.ALLOWLIST, gas.GENERATED}


def test_config_refuses_anything_but_a_hash():
    with pytest.raises(gas.GasError):
        gas.config_source("p", "raw-key-value", H)


def test_readback_compares_content_and_inventory(tmp_path):
    staging, remote = tmp_path / "push", tmp_path / "remote"
    staging.mkdir(); remote.mkdir()
    (staging / "facebook_sheet_receiver.gs").write_text("const A = 1;\n", encoding="utf-8")
    (staging / "appsscript.json").write_text('{"b": 1, "a": 2}', encoding="utf-8")
    (remote / "facebook_sheet_receiver.js").write_text("const A = 1;\r\n", encoding="utf-8")
    (remote / "appsscript.json").write_text('{"a": 2, "b": 1}', encoding="utf-8")
    (remote / ".clasp.json").write_text("{}", encoding="utf-8")
    assert gas.readback_matches(remote, staging) == (True, [])
    (remote / "الرمز.js").write_text("old", encoding="utf-8")
    ok, problems = gas.readback_matches(remote, staging)
    assert not ok and any("unexpected remote file" in p for p in problems)


def test_version_and_deployment_creation_reuse_existing_by_description():
    fake = FakeClasp(seven())
    c = gas.Clasp(runner=fake)
    n = gas.ensure_version(c, "cf-v2-run1")
    assert n == 7 and gas.ensure_version(c, "cf-v2-run1") == 7
    assert sum(1 for call in fake.calls if "create-version" in call) == 1
    d = gas.ensure_deployment(c, "sid", 7, "cf-rehearsal-run1")
    assert gas.ensure_deployment(c, "sid", 7, "cf-rehearsal-run1") == d
    assert sum(1 for call in fake.calls if "create-deployment" in call) == 1


def test_repoint_all_moves_every_versioned_deployment_in_order_and_skips_head():
    fake = FakeClasp(seven() + [{"id": "AKfyREH", "version": "7", "description": "cf-rehearsal-run1"}])
    c = gas.Clasp(runner=fake)
    results = gas.repoint_all(c, "sid", 7, order=["AKfyV5", "AKfyV6"], keep_ids=set())
    assert [r["id"] for r in results][:2] == ["AKfyV5", "AKfyV6"]
    assert all(d["version"] in ("7", "HEAD") for d in fake.deployments)
    assert gas.exposed_v1(c, "sid", 7) == []


def test_repoint_interrupted_after_some_deployments_resumes_without_redoing():
    fake = FakeClasp(seven(), fail_after=2)
    c = gas.Clasp(runner=fake)
    with pytest.raises(gas.GasError):
        gas.repoint_all(c, "sid", 7, order=["AKfyV5", "AKfyV6"], keep_ids=set())
    exposed = gas.exposed_v1(c, "sid", 7)
    assert {d["id"] for d in exposed} == {"AKfyV1", "AKfyV2", "AKfyV3", "AKfyV4"}
    fake.fail_after = None
    before = fake.updates
    gas.repoint_all(c, "sid", 7, order=["AKfyV5", "AKfyV6"], keep_ids=set())
    assert fake.updates - before == 4 and gas.exposed_v1(c, "sid", 7) == []


def test_exposed_v1_ignores_kept_and_head():
    fake = FakeClasp(seven())
    c = gas.Clasp(runner=fake)
    assert len(gas.exposed_v1(c, "sid", 7, keep_ids={"AKfyV1"})) == 5


@pytest.mark.parametrize("prints, expected, sleeps", [
    (["a", "a"], True, 2),
    (["a", "b", "b", "b"], True, 4),
    (["a", "b", "c", "d", "e", "f"], False, 6),
])
def test_drain_needs_two_equal_fingerprints_after_settling(prints, expected, sleeps):
    it, slept = iter(prints), []
    assert gas.drain(lambda: next(it), sleep=slept.append, settle_s=420, recheck_s=60, cycles=3) is expected
    assert len(slept) == sleeps and slept[0] == 420


def test_project_file_points_at_the_staging_folder(tmp_path):
    p = gas.write_project_file("script-x", tmp_path / ".clasp.json")
    assert json.loads(p.read_text(encoding="utf-8")) == {"scriptId": "script-x", "rootDir": "push"}


def test_staging_and_project_file_are_git_ignored():
    from conftest import ROOT

    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "apps_script/push/" in text and "apps_script/.clasp.json" in text and ".secrets/" in text
