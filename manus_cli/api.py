from __future__ import annotations

import os
import random
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

import httpx

BASE_URL = "https://api.manus.ai/v2"

# Documented at https://open.manus.ai/docs/v2/rate-limits. Upload attempts use a
# conservative process-local limiter below; the remaining values document the
# server budgets used to reason about retry/backoff behavior.
RATE_LIMITS_PER_MIN = {
    "task.create": 10,
    "task.sendMessage": 10,
    "task.detail": 100,
    "task.list": 100,
    "task.listMessages": 100,
    "task.confirmAction": 40,
    "file.upload": 40,
}

MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
FILE_UPLOADS_PER_MINUTE_BUDGET = 35


class ManusAPIError(Exception):
    def __init__(self, code: str, message: str, request_id: str | None = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.request_id = request_id


def _require_https(url: str) -> None:
    if not url.lower().startswith("https://"):
        raise ManusAPIError("unsafe_url", "URL de anexo recusada (precisa ser https)")


def _sleep_with_jitter(base_delay: float, attempt: int, retry_after: str | None) -> None:
    if retry_after:
        try:
            delay = float(retry_after)
        except ValueError:
            delay = base_delay * (2**attempt)
    else:
        delay = base_delay * (2**attempt)
    time.sleep(delay + delay * random.uniform(0, 0.25))


# Idempotent calls (GET, PUT to a presigned URL) are safe to retry on anything
# transient. Non-idempotent calls (task.create, task.sendMessage, file.upload's
# record-creation POST, task.confirmAction) can duplicate a real side effect if
# retried after the request may already have reached the server — so for those we
# only retry cases where we KNOW the server never processed the request: a
# connection that never got established, or an explicit 429 (which by definition
# means the request was rejected before doing any work).
_IDEMPOTENT_RETRY_STATUS = {429, 500, 502, 503, 504}
_NON_IDEMPOTENT_RETRY_STATUS = {429}


def _with_retries(
    request_fn,
    *,
    idempotent: bool,
    attempts: int = 4,
    base_delay: float = 1.0,
    before_attempt=None,
):
    retry_status = _IDEMPOTENT_RETRY_STATUS if idempotent else _NON_IDEMPOTENT_RETRY_STATUS
    for attempt in range(attempts):
        if before_attempt:
            before_attempt()
        try:
            resp = request_fn()
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
            # The request never left the client — always safe to retry.
            if attempt == attempts - 1:
                raise ManusAPIError("network_error", f"falha de conexão após {attempts} tentativas: {e}") from e
            _sleep_with_jitter(base_delay, attempt, None)
            continue
        except httpx.TransportError as e:
            # Timed out / dropped *after* the request may have been sent.
            if not idempotent:
                raise ManusAPIError(
                    "ambiguous_failure",
                    "a conexão caiu depois de enviar a requisição — o servidor pode ou não "
                    f"ter processado. Não repetido automaticamente para evitar duplicar. ({e})",
                ) from e
            if attempt == attempts - 1:
                raise ManusAPIError("network_error", f"falha de rede após {attempts} tentativas: {e}") from e
            _sleep_with_jitter(base_delay, attempt, None)
            continue

        if resp.status_code in retry_status and attempt < attempts - 1:
            _sleep_with_jitter(base_delay, attempt, resp.headers.get("Retry-After"))
            continue
        return resp
    raise ManusAPIError("network_error", "falha de rede: tentativas esgotadas")


class SlidingWindowRateLimiter:
    """Process-local sliding-window limiter used at the actual HTTP-attempt seam."""

    def __init__(self, limit: int, period: float = 60.0, *, clock=time.monotonic, sleep=time.sleep):
        self.limit = limit
        self.period = period
        self._clock = clock
        self._sleep = sleep
        self._attempts: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            while self._attempts and now - self._attempts[0] >= self.period:
                self._attempts.popleft()
            if len(self._attempts) >= self.limit:
                wait = self.period - (now - self._attempts[0])
                if wait > 0:
                    self._sleep(wait)
                now = self._clock()
                while self._attempts and now - self._attempts[0] >= self.period:
                    self._attempts.popleft()
            self._attempts.append(now)


def _unique_destination(dest: Path) -> Path:
    """Never overwrite an existing (or symlinked) path silently."""
    if not dest.exists() and not dest.is_symlink():
        return dest
    stem, suffix = dest.stem, dest.suffix
    n = 2
    while True:
        candidate = dest.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
        n += 1


class ManusClient:
    def __init__(self, api_key: str, timeout: float = 30.0):
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers={"x-manus-api-key": api_key, "Content-Type": "application/json"},
            timeout=timeout,
        )
        # Unauthenticated client for presigned/external URLs (file.upload PUT, attachment
        # downloads): never send our API key to a third-party host.
        self._external_http = httpx.Client(timeout=timeout)
        self._file_upload_limiter = SlidingWindowRateLimiter(FILE_UPLOADS_PER_MINUTE_BUDGET)

    def close(self) -> None:
        self._http.close()
        self._external_http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _call(
        self,
        method: str,
        path: str,
        *,
        idempotent: bool | None = None,
        attempts: int = 4,
        before_attempt=None,
        **kwargs,
    ) -> dict:
        if idempotent is None:
            idempotent = method == "GET"
        resp = _with_retries(
            lambda: self._http.request(method, path, **kwargs),
            idempotent=idempotent,
            attempts=attempts,
            before_attempt=before_attempt,
        )

        try:
            data = resp.json()
        except ValueError as e:
            raise ManusAPIError(
                "invalid_response", f"resposta não-JSON da API (status {resp.status_code}): {e}"
            ) from e
        if not isinstance(data, dict):
            raise ManusAPIError("invalid_response", f"resposta com formato inesperado (status {resp.status_code})")

        request_id = data.get("request_id")
        if not data.get("ok", False):
            err = data.get("error")
            if not isinstance(err, dict):
                err = {}
            code = err.get("code", "unknown_error")
            message = err.get("message") or f"erro HTTP {resp.status_code} sem detalhes"
            raise ManusAPIError(code, message, request_id)
        if not resp.is_success:
            # ok:true but a non-2xx status is a schema surprise, not a normal error path.
            raise ManusAPIError(
                "unexpected_status", f"resposta ok:true com status HTTP {resp.status_code}", request_id
            )
        return data

    def validate_key(self) -> None:
        self._call("GET", "/task.list", params={"limit": 1})

    def list_tasks(self, limit: int = 20, order: str = "desc") -> dict:
        return self._call("GET", "/task.list", params={"limit": limit, "order": order})

    def list_connectors(self) -> dict:
        return self._call("GET", "/connector.list")

    def create_task(
        self,
        content,
        *,
        project_id: str | None = None,
        connectors: list[str] | None = None,
        agent_profile: str | None = None,
        enable_skills: list[str] | None = None,
        force_skills: list[str] | None = None,
        structured_output_schema: dict | None = None,
        request_timeout: float | None = None,
    ) -> dict:
        message: dict = {"content": content}
        if connectors:
            message["connectors"] = connectors
        if enable_skills:
            message["enable_skills"] = enable_skills
        if force_skills:
            message["force_skills"] = force_skills
        body: dict = {"message": message}
        if project_id:
            body["project_id"] = project_id
        if agent_profile:
            body["agent_profile"] = agent_profile
        if structured_output_schema:
            body["structured_output_schema"] = structured_output_schema
        kwargs = {"timeout": request_timeout} if request_timeout is not None else {}
        return self._call(
            "POST", "/task.create", json=body, idempotent=False,
            attempts=1 if request_timeout is not None else 4, **kwargs,
        )

    def send_message(
        self,
        task_id: str,
        content,
        *,
        connectors: list[str] | None = None,
        structured_output_schema: dict | None = None,
        request_timeout: float | None = None,
    ) -> dict:
        message: dict = {"content": content}
        if connectors:
            message["connectors"] = connectors
        body: dict = {"task_id": task_id, "message": message}
        if structured_output_schema:
            body["structured_output_schema"] = structured_output_schema
        kwargs = {"timeout": request_timeout} if request_timeout is not None else {}
        return self._call(
            "POST", "/task.sendMessage", json=body, idempotent=False,
            attempts=1 if request_timeout is not None else 4, **kwargs,
        )

    def confirm_action(self, task_id: str, event_id: str, input_data: dict | None = None) -> dict:
        body: dict = {"task_id": task_id, "event_id": event_id}
        if input_data is not None:
            body["input"] = input_data
        return self._call("POST", "/task.confirmAction", json=body, idempotent=False)

    def task_detail(self, task_id: str) -> dict:
        return self._call("GET", "/task.detail", params={"task_id": task_id})

    def list_messages(
        self,
        task_id: str,
        limit: int = 20,
        order: str = "desc",
        verbose: bool = False,
        cursor: str | None = None,
        request_timeout: float | None = None,
    ) -> dict:
        params: dict = {"task_id": task_id, "limit": limit, "order": order}
        if verbose:
            params["verbose"] = "true"
        if cursor:
            params["cursor"] = cursor
        if request_timeout is None:
            return self._call("GET", "/task.listMessages", params=params)
        return self._call(
            "GET",
            "/task.listMessages",
            params=params,
            attempts=1,
            timeout=request_timeout,
        )

    def upload_file(self, path: Path, filename: str | None = None) -> str:
        record = self._call(
            "POST",
            "/file.upload",
            json={"filename": filename or path.name},
            idempotent=False,
            before_attempt=self._file_upload_limiter.acquire,
        )
        upload_url = record["upload_url"]
        _require_https(upload_url)

        def put_once():
            # A retry must reopen the stream. Reusing the same file object after a
            # partial/failed PUT would resume at its current offset and corrupt it.
            with open(path, "rb") as stream:
                return self._external_http.put(upload_url, content=stream)

        put_resp = _with_retries(put_once, idempotent=True)
        put_resp.raise_for_status()
        return record["file"]["id"]

    def download_file(self, url: str, dest: Path, *, max_bytes: int = MAX_DOWNLOAD_BYTES) -> Path:
        _require_https(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest = _unique_destination(dest)
        fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), prefix=".manus-dl-")
        tmp_path = Path(tmp_name)
        try:
            written = 0
            with os.fdopen(fd, "wb") as out, self._external_http.stream("GET", url) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_bytes():
                    written += len(chunk)
                    if written > max_bytes:
                        raise ManusAPIError(
                            "download_too_large",
                            f"anexo excede o limite de {max_bytes // (1024 * 1024)}MB, download abortado",
                        )
                    out.write(chunk)
            os.replace(tmp_path, dest)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        return dest
