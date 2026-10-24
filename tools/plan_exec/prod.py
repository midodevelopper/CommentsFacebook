"""خطوات الإنتاج (P3–P5) كأوامر مسجّلة. المعرفات من .secrets/ids.json، والمفاتيح من .secrets/*.key،
والمخرجات في dumps/plan-exec/<phase>/ — ومفيش قيمة سرية بتتطبع (بصمات بس).

  python -m tools.plan_exec.prod pull-check             # HEAD والنسخ 5 و 6 = الكود اللي في الريبو؟
  python -m tools.plan_exec.prod push --run RUN          # staging + push + قراءة راجعة
  python -m tools.plan_exec.prod version --run RUN       # نسخة cf-v2-RUN (idempotent)
  python -m tools.plan_exec.prod rehearsal-deploy --run RUN
  python -m tools.plan_exec.prod api ACTION --target prod|ID [--json FILE] [--via rehearsal|production]
  python -m tools.plan_exec.prod deployments
  python -m tools.plan_exec.prod repoint --run RUN       # كل نشر بنسخة -> cf-v2-RUN (قابل للاستكمال)
  python -m tools.plan_exec.prod restore-head            # يرجّع ملفات HEAD القديمة (P3/P4 rollback)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

from . import EXEC_DIR, ROOT, SECRETS
from . import gas
from .client import ApiError, Client

IDS = SECRETS / "ids.json"


def ids() -> dict:
    return json.loads(IDS.read_text(encoding="utf-8"))


def save_ids(data: dict) -> None:
    IDS.write_text(json.dumps(data, indent=2), encoding="utf-8")


def fp(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]


def out_dir(phase: str) -> Path:
    d = EXEC_DIR / phase
    d.mkdir(parents=True, exist_ok=True)
    return d


def exec_url(deployment_id: str) -> str:
    return f"https://script.google.com/macros/s/{deployment_id}/exec"


def admin_client(via: str = "rehearsal") -> Client:
    data = ids()
    dep = data.get("rehearsal_deployment") if via == "rehearsal" else data["production_deployment"]
    if not dep:
        raise SystemExit("no rehearsal deployment recorded yet")
    return Client(exec_url(dep), (SECRETS / "admin.key").read_text(encoding="utf-8").strip())


def resolve_target(name: str) -> str:
    data = ids()
    if name == "prod":
        return data["sheet_id"]
    return data.get("files", {}).get(name, name)


def cmd_pull_check(args) -> int:
    data = ids()
    c = gas.Clasp()
    repo = hashlib.sha256((ROOT / "facebook_sheet_receiver.gs").read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    report = {}
    base = out_dir("P3") / "pulled"
    for label, version in (("head", None), ("v5", 5), ("v6", 6)):
        dest = c.clone(data["script_id"], base / label, version)
        code = [p for p in dest.iterdir() if p.suffix in (".js", ".gs")]
        hashes = [hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest() for p in code]
        report[label] = {"files": sorted(p.name for p in dest.iterdir() if p.name != ".clasp.json"),
                         "equals_repo": hashes == [repo]}
    (out_dir("P3") / "pull-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if all(r["equals_repo"] for r in report.values()) else 2


def cmd_push(args) -> int:
    data = ids()
    gas.write_project_file(data["script_id"])
    hashes = gas.build_staging(data["sheet_id"], gas.key_hash("admin"), gas.key_hash("ingest"))
    c = gas.Clasp()
    c.push()
    dest = c.clone(data["script_id"], out_dir("P3") / "readback-head")
    ok, problems = gas.readback_matches(dest)
    result = {"staging": hashes, "readback_ok": ok, "problems": problems}
    (out_dir("P3") / "push.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"readback_ok": ok, "problems": problems}))
    return 0 if ok else 2


def cmd_version(args) -> int:
    c = gas.Clasp()
    n = gas.ensure_version(c, f"cf-v2-{args.run}")
    data = ids()
    data["v2_version"] = n
    save_ids(data)
    dest = c.clone(data["script_id"], out_dir("P3") / f"readback-v{n}", n)
    ok, problems = gas.readback_matches(dest)
    print(json.dumps({"version": n, "readback_ok": ok, "problems": problems}))
    return 0 if ok else 2


def cmd_rehearsal_deploy(args) -> int:
    data = ids()
    dep = gas.ensure_deployment(gas.Clasp(), data["script_id"], data["v2_version"], f"cf-rehearsal-{args.run}")
    data["rehearsal_deployment"] = dep
    save_ids(data)
    print(json.dumps({"rehearsal_deployment_fp": fp(dep), "version": data["v2_version"]}))
    return 0


def cmd_deployments(args) -> int:
    rows = gas.Clasp().list_deployments(ids()["script_id"])
    known = {v: k for k, v in ids()["deployments_by_version"].items()}
    known[ids().get("rehearsal_deployment", "")] = "rehearsal"
    print(json.dumps([{"label": known.get(r["id"], "?" + fp(r["id"])), "version": r["version"],
                       "description": r["description"]} for r in rows], ensure_ascii=False))
    return 0


def cmd_repoint(args) -> int:
    data = ids()
    c = gas.Clasp()
    order = [data["python_v1_deployment"], data["production_deployment"]]
    keep = {data.get("rehearsal_deployment", "")}
    log = out_dir("P5") / "repoint.jsonl"

    def write(row):
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id_fp": fp(row["id"]), "from": row["from"], "to": row["to"]}) + "\n")
    results = gas.repoint_all(c, data["script_id"], data["v2_version"], order, keep, log=write)
    exposed = gas.exposed_v1(c, data["script_id"], data["v2_version"], keep)
    print(json.dumps({"repointed": len(results), "exposed_v1": len(exposed)}))
    return 0 if not exposed else 2


def cmd_restore_head(args) -> int:
    data = ids()
    src = out_dir("P3") / "pulled" / "head"
    stage = gas.STAGING
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    for p in src.iterdir():
        if p.name != ".clasp.json":
            shutil.copy2(p, stage / p.name)
    gas.write_project_file(data["script_id"])
    c = gas.Clasp()
    c.push()
    dest = c.clone(data["script_id"], out_dir("P3") / "readback-restored")
    ok, problems = gas.readback_matches(dest, stage)
    print(json.dumps({"restored_ok": ok, "problems": problems}))
    return 0 if ok else 2


def cmd_api(args) -> int:
    payload = json.loads(Path(args.json).read_text(encoding="utf-8")) if args.json else {}
    client = admin_client(args.via)
    try:
        answer = client.call(args.action, payload, target=resolve_target(args.target), request_id=args.request_id)
    except ApiError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "error": str(exc)[:500]}, ensure_ascii=False))
        return 2
    text = json.dumps(answer, ensure_ascii=False)
    if args.save:
        (out_dir(args.save.split("/")[0]) / args.save.split("/", 1)[1]).write_text(text, encoding="utf-8")
    print(text[: args.max_chars])
    return 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(prog="plan_exec.prod")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pull-check")
    for name in ("push", "version", "rehearsal-deploy", "repoint"):
        s = sub.add_parser(name)
        s.add_argument("--run", default="")
    sub.add_parser("deployments")
    sub.add_parser("restore-head")
    s = sub.add_parser("api")
    s.add_argument("action")
    s.add_argument("--target", default="prod")
    s.add_argument("--json")
    s.add_argument("--via", default="rehearsal", choices=["rehearsal", "production"])
    s.add_argument("--request-id")
    s.add_argument("--save")
    s.add_argument("--max-chars", type=int, default=3000)
    args = p.parse_args(argv)
    return {"pull-check": cmd_pull_check, "push": cmd_push, "version": cmd_version,
            "rehearsal-deploy": cmd_rehearsal_deploy, "deployments": cmd_deployments, "repoint": cmd_repoint,
            "restore-head": cmd_restore_head, "api": cmd_api}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
