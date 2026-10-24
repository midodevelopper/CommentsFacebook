"""Runs the Node test files for the Apps Script code once per session and reports every JS test as its own
pytest item:
  * receiver.test.mjs     — the v1 facebook_sheet_receiver.gs (still deployed until the v2 cut-over)
  * receiver_v2.test.mjs  — the v2 receiver in apps_script/src/ against the stricter gas_fake_v2.mjs

This executes the actual Apps Script logic against an in-memory Sheet. It does not touch the deployed web app
or the production spreadsheet, and the fake services are models — not Google's code.
"""
from __future__ import annotations

import re
import subprocess
import xml.etree.ElementTree as ET

import pytest

from conftest import TESTS, node_available

pytestmark = pytest.mark.gas

JS_FILES = [p for p in (TESTS / "apps_script" / "receiver.test.mjs", TESTS / "apps_script" / "receiver_v2.test.mjs")
            if p.exists()]
NAMES = [(p.name, n) for p in JS_FILES for n in re.findall(r"^test\(['`]([^'`]+)['`]", p.read_text(encoding="utf-8"), re.M)]
# أسماء متولّدة في حلقة (test(`... ${step} ...`)) بتتعد من نتيجة التشغيل نفسه
DYNAMIC = [(f, n) for f, n in NAMES if "${" in n]
STATIC = [(f, n) for f, n in NAMES if "${" not in n]


@pytest.fixture(scope="session")
def node_results(tmp_path_factory):
    if not node_available():
        pytest.skip("node is not installed")
    results, procs = {}, []
    for path in JS_FILES:
        report = tmp_path_factory.mktemp("node") / "junit.xml"
        proc = subprocess.run(
            ["node", "--test", "--test-reporter=junit", f"--test-reporter-destination={report}", str(path)],
            capture_output=True, text=True, encoding="utf-8", timeout=600,
        )
        procs.append(proc)
        for case in ET.parse(report).getroot().iter("testcase"):
            failure = case.find("failure")
            results[(path.name, case.get("name"))] = None if failure is None else (failure.get("message") or "") + "\n" + (failure.text or "")
    return results, procs


def test_js_test_names_are_unique():
    assert len(STATIC) == len(set(STATIC)) > 0


@pytest.mark.parametrize("file,name", STATIC, ids=[f"{f}::{n}" for f, n in STATIC])
def test_receiver(node_results, file, name):
    results, procs = node_results
    assert (file, name) in results, f"JS test did not run: {name}\n" + "\n".join(p.stderr[-1500:] for p in procs)
    assert results[(file, name)] is None, results[(file, name)]


def test_receiver_generated_cases(node_results):
    """الاختبارات المتولّدة (استكمال الترحيل بعد كل خطوة) كلها اشتغلت ونجحت."""
    results, _ = node_results
    for file, pattern in DYNAMIC:
        rx = re.compile("^" + re.escape(pattern).replace(re.escape("${step}"), ".+") + "$")
        ran = {k: v for k, v in results.items() if k[0] == file and rx.match(k[1])}
        assert len(ran) >= 10, f"{pattern}: only {len(ran)} generated cases ran"
        failed = {k[1]: v for k, v in ran.items() if v is not None}
        assert not failed, failed
