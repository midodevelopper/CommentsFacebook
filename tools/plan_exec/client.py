"""عميل الـ web app v2: ظرف الطلب، request_id، وإعادة المحاولة المحدودة (§5).

المفتاح بيتقري من .secrets/ ويتبعت في جسم POST بس — عمره ما بيتحط في رابط ولا سطر أوامر ولا لوج.
نفس request_id في كل إعادة، فالسيرفر يرجّع النتيجة الأصلية لو الطلب الأول كان نجح.
"""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import SECRETS

RETRY_WAITS = (5, 15, 45)          # قبل المحاولة 2 و 3 و 4: أول محاولة + 3 إعادات
RETRY_HTTP = (404, 429, 500, 502, 503, 504)
RETRYABLE_CODES = ("E_LOCK_BUSY", "E_INTERNAL")
TIMEOUT = 330
MAX_CONTINUATIONS = 50


class ApiError(Exception):
    def __init__(self, code: str, message: str, response: dict | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.response = response or {}


def read_secret(name: str, root: Path = SECRETS) -> str:
    return (root / name).read_text(encoding="utf-8").strip()


def read_config(root: Path = SECRETS) -> dict:
    return json.loads((root / "config.json").read_text(encoding="utf-8"))


class Client:
    def __init__(self, url: str, key: str, kind: str = "admin", sleep=time.sleep, opener=None):
        self.url = url
        self.key = key
        self.kind = kind
        self.sleep = sleep
        self.opener = opener or urllib.request.urlopen
        self.attempts_log: list[dict] = []

    def _send(self, body: bytes) -> tuple[int, str]:
        req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self.opener(req, timeout=TIMEOUT) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")

    def call(self, action: str, payload: dict | None = None, target: str | None = None,
             request_id: str | None = None, budget_ms: int = 240000) -> dict:
        """طلب واحد بإعادة محدودة. E_BUDGET بيكمّل بالـ cursor (مش فشل)."""
        request_id = request_id or str(uuid.uuid4())
        cursor = None
        merged: list = []
        for _ in range(MAX_CONTINUATIONS + 1):
            envelope = {"api_version": 2, "request_id": request_id if cursor is None else f"{request_id}:{len(merged)}",
                        "action": action, "auth": {"kind": self.kind, "key": self.key},
                        "payload": payload or {}, "cursor": cursor, "budget_ms": budget_ms}
            if target:
                envelope["target"] = target
            answer = self._one(json.dumps(envelope, ensure_ascii=False).encode("utf-8"), action)
            if answer.get("code") == "E_BUDGET" and answer.get("cursor") is not None:
                cursor = answer["cursor"]
                merged.append(answer.get("result"))
                continue
            if merged:
                answer.setdefault("continued", merged)
            return answer
        raise ApiError("E_BUDGET", f"{action}: أكتر من {MAX_CONTINUATIONS} استكمال")

    def _one(self, body: bytes, action: str) -> dict:
        sent_id = json.loads(body.decode("utf-8")).get("request_id")
        problem = ""
        internal_retries = 0
        for attempt in range(len(RETRY_WAITS) + 1):
            if attempt:
                wait = RETRY_WAITS[attempt - 1] * random.uniform(0.8, 1.2)
                self.sleep(wait)
            try:
                status, text = self._send(body)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                problem = f"network: {exc}"
                self.attempts_log.append({"action": action, "attempt": attempt + 1, "problem": problem})
                continue
            if status in RETRY_HTTP:
                problem = f"HTTP {status}"
                self.attempts_log.append({"action": action, "attempt": attempt + 1, "problem": problem})
                continue
            if status >= 400:
                raise ApiError("E_HTTP", f"HTTP {status}: {text[:200]}")
            try:
                answer = json.loads(text)
            except ValueError:
                raise ApiError("E_NOT_JSON", text[:200]) from None
            # رد ناجح لازم يرجّع نفس request_id. اتشاف في P4: Google ساعات بترجّع رد doGet (ping) بدل doPost بعد الـ
            # redirect — يعني نتيجة الطلب مش معروفة، فبنعيده بنفس الـ id (السيرفر بيرجّع النتيجة الأصلية لو كان اتنفّذ)
            if answer.get("ok") is True and sent_id and answer.get("request_id") != sent_id:
                problem = "E_SHAPE: answer is not for this request"
                self.attempts_log.append({"action": action, "attempt": attempt + 1, "problem": problem})
                continue
            if answer.get("ok") is True:
                return answer
            code = answer.get("code") or "E_UNKNOWN"
            if code == "E_BUDGET":
                return answer
            # E_INTERNAL مرة واحدة بس في الطلب كله، مهما جه في أنهي محاولة
            retryable = code in RETRYABLE_CODES and (code != "E_INTERNAL" or internal_retries == 0)
            if code == "E_INTERNAL":
                internal_retries += 1
            if not retryable:
                raise ApiError(code, str(answer.get("error") or answer.get("message") or ""), answer)
            problem = code
            self.attempts_log.append({"action": action, "attempt": attempt + 1, "problem": problem})
        raise ApiError("E_RETRY_EXHAUSTED", f"{action}: {problem}")
