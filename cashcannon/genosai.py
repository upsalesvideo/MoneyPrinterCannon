"""Genosai Public API client.

Rules baked in (see CONTRACT.md):
  * createTask is dispatched with a global >= 1.0 s gap (lock shared by all threads/clients);
  * poll every 5 s, watchdog 300 s -> create a NEW task, max 3 attempts;
  * 429 -> sleep the seconds the server asks for, does not consume an attempt;
  * 402 -> InsufficientCredits, 400 VALIDATION_ERROR -> GenosaiValidationError (no retries);
  * fresh requests.Session + `Connection: close` per call (fixes SSLEOFError on some pythons);
  * media is downloaded WITHOUT the Authorization header (presigned S3).
The key is never logged.
"""
from __future__ import annotations

import json
import mimetypes
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import requests
from loguru import logger

from .config import settings

DISPATCH_GAP = 1.0
WATCHDOG = 300
MAX_ATTEMPTS = 3
POLL_EVERY = 5
RATE_LIMIT_WAITS = 12  # how many consecutive 429s we tolerate before giving up

_dispatch_lock = threading.Lock()
_last_dispatch = [0.0]
_models_cache: dict[str, Any] = {}
_models_lock = threading.Lock()


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------
class GenosaiError(Exception):
    """Any API-level failure."""

    def __init__(self, message: str, status: int = 0, code: str = "", payload: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.payload = payload


class InsufficientCredits(GenosaiError):
    """402 — top up the balance."""


class GenosaiValidationError(GenosaiError):
    """400 VALIDATION_ERROR — retrying is useless, change the input."""


class TaskTimeout(GenosaiError):
    """Task did not finish within watchdog × attempts."""


class RateLimited(GenosaiError):
    """429 — carries retry_after seconds."""

    def __init__(self, message: str, retry_after: float):
        super().__init__(message, status=429, code="RATE_LIMITED")
        self.retry_after = retry_after


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------
@dataclass
class TaskResult:
    urls: list[str]
    cost: float
    task_id: str
    info: dict = field(default_factory=dict)

    @property
    def url(self) -> str:
        return self.urls[0] if self.urls else ""


@dataclass
class ChatResult:
    content: str
    cost: float
    usage: dict = field(default_factory=dict)
    model: str = ""

    def __str__(self) -> str:  # so `str(client.chat(...))` is the content
        return self.content


def _parse_retry_after(text: str, default: float = 10.0) -> float:
    m = re.search(r"[Tt]ry again in (\d+(?:\.\d+)?)\s*second", text)
    if m:
        return float(m.group(1)) + 1.0
    m = re.search(r"retry.after[^0-9]*(\d+)", text, re.I)
    if m:
        return float(m.group(1)) + 1.0
    return default


def _pick_message(body: Any, fallback: str) -> tuple[str, str]:
    """Return (code, message) from either the Kie-style {error, message} envelope or
    the OpenAI-style {error: {message, type, code}}."""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return str(err.get("code") or err.get("type") or ""), str(err.get("message") or fallback)
        if isinstance(err, str):
            return err, str(body.get("message") or fallback)
        if body.get("message"):
            return str(body.get("code") or ""), str(body["message"])
    return "", fallback


class GenosaiClient:
    """Thin, thread-safe client. One instance can be shared by many threads."""

    def __init__(self, api_key: Optional[str] = None, host: Optional[str] = None, timeout: int = 60):
        self.api_key = api_key if api_key is not None else settings.genosai_api_key
        self.host = (host or settings.genosai_api_host).rstrip("/")
        self.timeout = timeout
        if not self.api_key:
            raise GenosaiError("GENOSAI_API_KEY is not set (put it in .env or the environment)", status=401)

    # ---- plumbing -------------------------------------------------------
    def _session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update({"Authorization": f"Bearer {self.api_key}", "Connection": "close"})
        return s

    def _request(self, method: str, path: str, *, retries: int = 3, **kw: Any) -> Any:
        """HTTP call with error mapping. Network errors / 5xx are retried with backoff;
        429 raises RateLimited (callers decide how to wait)."""
        url = f"{self.host}{path}"
        kw.setdefault("timeout", self.timeout)
        last: Exception | None = None
        for i in range(1, retries + 1):
            try:
                with self._session() as s:
                    r = s.request(method, url, **kw)
            except (requests.ConnectionError, requests.Timeout) as e:
                last = e
                logger.warning("genosai {} {}: network error ({}), retry {}/{}", method, path, type(e).__name__, i, retries)
                time.sleep(2 * i)
                continue
            if r.status_code >= 500:
                last = GenosaiError(f"{method} {path} -> {r.status_code}: {r.text[:200]}", status=r.status_code)
                logger.warning("genosai {} {}: {} retry {}/{}", method, path, r.status_code, i, retries)
                time.sleep(2 * i)
                continue
            return self._handle(r, f"{method} {path}")
        raise GenosaiError(f"{method} {path}: gave up after {retries} tries: {last}")

    @staticmethod
    def _handle(r: requests.Response, what: str) -> Any:
        try:
            body: Any = r.json()
        except ValueError:
            body = None
        if r.status_code < 400:
            return body if body is not None else r.text
        code, msg = _pick_message(body, r.text[:300])
        if r.status_code == 429:
            raise RateLimited(f"{what}: 429 {msg}", _parse_retry_after(msg))
        if r.status_code == 402 or code == "INSUFFICIENT_CREDITS":
            raise InsufficientCredits(f"Not enough Genosai credits: {msg}", status=402, code=code, payload=body)
        if r.status_code == 400 and (code == "VALIDATION_ERROR" or not code):
            raise GenosaiValidationError(msg, status=400, code=code or "VALIDATION_ERROR", payload=body)
        if r.status_code == 401:
            raise GenosaiError(f"Unauthorized (check GENOSAI_API_KEY / host): {msg}", status=401, code=code)
        raise GenosaiError(f"{what}: {r.status_code} {code} {msg}", status=r.status_code, code=code, payload=body)

    # ---- account / catalog ----------------------------------------------
    def balance(self) -> dict[str, float]:
        d = self._request("GET", "/v1/balance")
        d = d.get("data", d) if isinstance(d, dict) else {}
        out = {k: float(d.get(k) or 0) for k in ("main", "bonus", "total")}
        if not out["total"] and d.get("balance") is not None:
            out["total"] = float(d["balance"])
        return out

    def models(self, refresh: bool = False) -> dict[str, list[dict]]:
        """{photo:[], video:[], tts:[], music:[], text:[], ...} — cached in memory."""
        with _models_lock:
            if _models_cache and not refresh:
                return _models_cache
            d = self._request("GET", "/v1/models")
            d = d.get("data", d) if isinstance(d, dict) else {}
            _models_cache.clear()
            _models_cache.update({k: v for k, v in d.items() if isinstance(v, list)})
            return _models_cache

    def model_info(self, model_id: str) -> Optional[dict]:
        for cat in self.models().values():
            for m in cat:
                if m.get("id") == model_id:
                    return m
        return None

    def model_category(self, model_id: str) -> str:
        for cat, items in self.models().items():
            if any(m.get("id") == model_id for m in items):
                return cat
        return ""

    def filter_input(self, model: str, payload: dict) -> dict:
        """Drop fields the model does not declare in input_options (API answers 400 on
        unknown params) and values outside a declared enum. Unknown model -> unchanged."""
        info = self.model_info(model)
        if not info:
            return dict(payload)
        opts = info.get("input_options") or {}
        if not isinstance(opts, dict) or not opts:
            return dict(payload)
        keep = {"prompt", "text"}
        ref_field = (info.get("references") or {}).get("field") if isinstance(info.get("references"), dict) else None
        if ref_field:
            keep.add(str(ref_field))
        out: dict = {}
        for k, v in payload.items():
            if k in keep:
                out[k] = v
                continue
            if k not in opts:
                logger.debug("{}: dropping unsupported field {}", model, k)
                continue
            allowed = _enum_values(opts.get(k))
            if allowed and v is not None and str(v) not in {str(a) for a in allowed}:
                logger.warning("{}: value {!r} for {} not in {}; dropping (server default)", model, v, k, allowed[:12])
                continue
            out[k] = v
        return out

    # ---- chat -----------------------------------------------------------
    def chat(
        self,
        model: str,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = True,
    ) -> ChatResult:
        msgs = [dict(m) for m in messages]
        if json_mode:
            rule = "Respond with a single valid JSON object only. No markdown, no code fences, no commentary."
            if msgs and msgs[0].get("role") == "system":
                msgs[0]["content"] = f"{msgs[0]['content']}\n\n{rule}"
            else:
                msgs.insert(0, {"role": "system", "content": rule})
        body = {"model": model, "messages": msgs, "temperature": temperature, "max_tokens": max_tokens, "stream": False}
        for _ in range(RATE_LIMIT_WAITS):
            try:
                d = self._request("POST", "/v1/chat/completions", json=body, timeout=max(self.timeout, 180))
                break
            except RateLimited as e:
                logger.info("chat rate-limited, sleeping {:.0f}s", e.retry_after)
                time.sleep(e.retry_after)
        else:
            raise GenosaiError("chat: rate limit never released")
        try:
            content = d["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise GenosaiError(f"chat: unexpected response {json.dumps(d)[:300]}", payload=d)
        usage = d.get("usage") or {}
        cost = float(usage.get("cost_credits") or 0)
        logger.debug("chat {}: {} chars, cost {}", model, len(content), cost)
        return ChatResult(content=content, cost=cost, usage=usage, model=str(d.get("model") or model))

    # ---- tasks ----------------------------------------------------------
    def create_task(self, model: str, input: dict) -> tuple[str, float]:
        """POST /v1/createTask under the global 1 s dispatch lock. 429 is waited out here
        (as many times as the server asks, within reason)."""
        for _ in range(RATE_LIMIT_WAITS):
            with _dispatch_lock:
                wait = DISPATCH_GAP - (time.time() - _last_dispatch[0])
                if wait > 0:
                    time.sleep(wait)
                _last_dispatch[0] = time.time()
                try:
                    d = self._request("POST", "/v1/createTask", json={"model": model, "input": input})
                except RateLimited as e:
                    logger.info("createTask {} rate-limited, sleeping {:.0f}s", model, e.retry_after)
                    time.sleep(e.retry_after)
                    _last_dispatch[0] = time.time()
                    continue
            d = d.get("data", d) if isinstance(d, dict) else {}
            tid = d.get("taskId") or d.get("task_id") or d.get("id")
            if not tid:
                raise GenosaiError(f"createTask: no taskId in {json.dumps(d)[:300]}", payload=d)
            cost = float(d.get("cost") or 0)
            logger.debug("createTask {} -> {} (est. cost {})", model, tid, cost)
            return str(tid), cost
        raise GenosaiError("createTask: rate limit never released")

    def task_info(self, task_id: str) -> dict:
        d = self._request("GET", "/v1/taskInfo", params={"taskId": task_id})
        return d.get("data", d) if isinstance(d, dict) else {}

    @staticmethod
    def _result_urls(info: dict) -> list[str]:
        res = info.get("result") or {}
        if isinstance(res, dict) and res.get("media_urls"):
            return [str(u) for u in res["media_urls"]]
        for k in ("resultUrls", "result_urls", "urls", "media_urls"):
            if info.get(k):
                return [str(u) for u in info[k]]
        for k in ("mediaUrl", "media_url", "url"):
            if info.get(k):
                return [str(info[k])]
        return []

    def run_task(
        self,
        model: str,
        input: dict,
        watchdog: int = WATCHDOG,
        attempts: int = MAX_ATTEMPTS,
        poll: int = POLL_EVERY,
    ) -> TaskResult:
        """create -> poll -> urls. Watchdog restarts the task (new createTask), failures are
        retried unless they look like validation/moderation (then they are raised at once)."""
        last_err: Exception | None = None
        for attempt in range(1, attempts + 1):
            task_id, est_cost = self.create_task(model, input)
            started = time.time()
            try:
                while True:
                    time.sleep(poll)
                    try:
                        info = self.task_info(task_id)
                    except RateLimited as e:
                        time.sleep(e.retry_after)
                        continue
                    st = str(info.get("status") or "").lower()
                    if st in ("succeeded", "success", "completed"):
                        urls = self._result_urls(info)
                        if not urls:
                            raise GenosaiError(f"task {task_id} succeeded without media urls", payload=info)
                        cost = float(info.get("cost") if info.get("cost") is not None else est_cost)
                        logger.info("task {} {} done in {:.0f}s, cost {}", model, task_id, time.time() - started, cost)
                        return TaskResult(urls=urls, cost=cost, task_id=task_id, info=info)
                    if st in ("failed", "error", "canceled", "cancelled"):
                        msg = str(info.get("message") or info.get("error_message") or info.get("error") or st)
                        if _looks_like_validation(msg):
                            raise GenosaiValidationError(msg, status=400, code="VALIDATION_ERROR", payload=info)
                        raise GenosaiError(f"task {task_id} failed: {msg}", payload=info)
                    if time.time() - started > watchdog:
                        logger.warning("task {} {}: watchdog {}s, restarting ({}/{})", model, task_id, watchdog, attempt, attempts)
                        last_err = TaskTimeout(f"task {task_id} ({model}) not finished in {watchdog}s")
                        break
            except (GenosaiValidationError, InsufficientCredits):
                raise
            except GenosaiError as e:
                last_err = e
                logger.warning("task {} attempt {}/{} failed: {}", model, attempt, attempts, e)
                if attempt < attempts:
                    time.sleep(2 * attempt)
        if isinstance(last_err, TaskTimeout):
            raise TaskTimeout(f"{model}: gave up after {attempts} attempts × {watchdog}s")
        raise last_err or GenosaiError(f"{model}: task failed")

    def run_many(
        self,
        jobs: list[tuple[str, dict]],
        workers: Optional[int] = None,
        raise_on_error: bool = True,
        **kw: Any,
    ) -> list[TaskResult | Exception]:
        """Run tasks in parallel: pool size = len(jobs) (z-image: max 3). Dispatch order is
        preserved by the 1 s lock. With raise_on_error=False failed slots hold the exception."""
        if not jobs:
            return []
        n = len(jobs)
        if workers is None:
            workers = n
            if any(m.startswith("z-image") for m, _ in jobs):
                workers = min(3, n)
        results: list[TaskResult | Exception] = [GenosaiError("not run")] * n

        def _one(i: int) -> None:
            model, inp = jobs[i]
            try:
                results[i] = self.run_task(model, inp, **kw)
            except Exception as e:  # noqa: BLE001 — collected per slot
                results[i] = e

        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            list(ex.map(_one, range(n)))
        if raise_on_error:
            for r in results:
                if isinstance(r, Exception):
                    raise r
        return results

    # ---- files ----------------------------------------------------------
    def upload(self, path: str | Path) -> str:
        p = Path(path)
        mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        for i in range(1, 4):
            try:
                with self._session() as s:
                    r = s.post(f"{self.host}/v1/uploads", files={"file": (p.name, p.read_bytes(), mime)}, timeout=180)
                d = self._handle(r, "POST /v1/uploads")
                d = d.get("data", d) if isinstance(d, dict) else {}
                url = d.get("url") or d.get("fileUrl") or d.get("file_url")
                if not url:
                    raise GenosaiError(f"uploads: no url in {json.dumps(d)[:300]}", payload=d)
                return str(url)
            except (requests.ConnectionError, requests.Timeout) as e:
                if i == 3:
                    raise GenosaiError(f"upload failed: {e}")
                time.sleep(2 * i)
        raise GenosaiError("upload failed")

    @staticmethod
    def download(url: str, dest: str | Path, min_bytes: int = 1024) -> Path:
        """Fetch a result WITHOUT Authorization (presigned S3 rejects it with an XML body)."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        last: Exception | None = None
        for i in range(1, 4):
            try:
                r = requests.get(url, timeout=300, headers={"Connection": "close"})
                r.raise_for_status()
                data = r.content
                if data[:5] == b"<?xml" or len(data) < min_bytes:
                    raise GenosaiError(f"download {url[:80]}: bad body ({len(data)} bytes)")
                dest.write_bytes(data)
                return dest
            except (requests.RequestException, GenosaiError) as e:
                last = e
                time.sleep(2 * i)
        raise GenosaiError(f"download failed: {last}")


def reference_spec(info: Optional[dict]) -> tuple[str, bool, int]:
    """(field name, required, max_files) of a model's reference-image input, ('' , False, 0) if none."""
    refs = (info or {}).get("references")
    if not isinstance(refs, dict) or not refs.get("field"):
        return "", False, 0
    return str(refs["field"]), bool(refs.get("required")), int(refs.get("max_files") or 0)


def _enum_values(opt: Any) -> list:
    """Extract enum choices from an input_options entry (several shapes seen in the wild)."""
    if isinstance(opt, list):
        return opt
    if isinstance(opt, dict):
        for k in ("options", "values", "enum", "choices"):
            v = opt.get(k)
            if isinstance(v, list) and v and not isinstance(v[0], dict):
                return v
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return [x.get("value", x.get("id")) for x in v]
    return []


def _looks_like_validation(msg: str) -> bool:
    m = msg.lower()
    return any(k in m for k in ("validation", "generation failed", "moderation", "policy", "flagged", "not allowed", "invalid"))


def duration_choices(info: Optional[dict]) -> tuple[list[int], int, int]:
    """(discrete options, min, max) for a video model's `duration` input option."""
    if not info:
        return [], 0, 0
    opt = (info.get("input_options") or {}).get("duration")
    vals = [int(float(v)) for v in _enum_values(opt) if str(v).replace(".", "", 1).isdigit()]
    lo = hi = 0
    if isinstance(opt, dict):
        try:
            lo = int(float(opt.get("min"))) if opt.get("min") is not None else 0
            hi = int(float(opt.get("max"))) if opt.get("max") is not None else 0
        except (TypeError, ValueError):
            lo = hi = 0
    if vals:
        lo, hi = min(vals), max(vals)
    return sorted(set(vals)), lo, hi


if __name__ == "__main__":  # smoke: balance + model count (1 call each)
    c = GenosaiClient()
    print("balance:", c.balance())
    print({k: len(v) for k, v in c.models().items()})
