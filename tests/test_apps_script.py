"""Runs tests/apps_script/receiver.test.mjs (the real facebook_sheet_receiver.gs under Node)
once per session and reports every JS test as its own pytest item.

This executes the actual Apps Script logic against an in-memory Sheet. It does not
touch the deployed web app or the production spreadsheet, and the fake services
(SpreadsheetApp, LockService, ContentService) are models — not Google's code.
"""
from __future__ import annotations

import re
import subprocess
import xml.etree.ElementTree as ET

import pytest

from conftest import TESTS, node_available

pytestmark = pytest.mark.gas

JS_TESTS = TESTS / "apps_script" / "receiver.test.mjs"
NAMES = re.findall(r"^test\('([^']+)'", JS_TESTS.read_text(encoding="utf-8"), re.M)


@pytest.fixture(scope="session")
def node_results(tmp_path_factory):
    if not node_available():
        pytest.skip("node is not installed")
    report = tmp_path_factory.mktemp("node") / "junit.xml"
    proc = subprocess.run(
        ["node", "--test", "--test-reporter=junit", f"--test-reporter-destination={report}", str(JS_TESTS)],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
    )
    results = {}
    for case in ET.parse(report).getroot().iter("testcase"):
        failure = case.find("failure")
        results[case.get("name")] = None if failure is None else (failure.get("message") or "") + "\n" + (failure.text or "")
    return results, proc


def test_js_test_names_are_unique():
    assert len(NAMES) == len(set(NAMES)) > 0


@pytest.mark.parametrize("name", NAMES)
def test_receiver(node_results, name):
    results, proc = node_results
    assert name in results, f"JS test did not run: {name}\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    assert results[name] is None, results[name]
