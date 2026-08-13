from __future__ import annotations

import time
from pathlib import Path

import httpx

BASE_URL = "https://api.manus.ai/v2"


class ManusAPIError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _require_https(url: str) -> None:
    if not url.lower().startswith("https://"):
        raise ManusAPIError("unsafe_url", f"URL recusada (precisa ser https): {url}")


class ManusClient:
    def __init__(self, api_key: str, timeout: float = 30.0):
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers={"x-manus-api-key": api_key, "Content-Type": "application/json"},
            timeout=timeout,
        )
        # Unauthenticated client for presigned/external URLs (file.upload, attachments):
        # never send our API key to a third-party host.
        self._external_http = httpx.Client(timeout=timeout)

    def _call(self, method: str, path: str, **kwargs) -> dict:
        resp = self._http.request(method, path, **kwargs)
        data = resp.json()
        if not data.get("ok", False):
            err = data.get("error", {})
            raise ManusAPIError(err.get("code", "unknown_error"), err.get("message", resp.text))
        return data

    def validate_key(self) -> None:
        self._call("GET", "/task.list", params={"limit": 1})

    def list_tasks(self, limit: int = 20, order: str = "desc") -> dict:
        return self._call("GET", "/task.list", params={"limit": limit, "order": order})

    def create_task(self, content, project_id: str | None = None, connectors: list[str] | None = None) -> dict:
        message = {"content": content}
        if connectors:
            message["connectors"] = connectors
        body = {"message": message}
        if project_id:
            body["project_id"] = project_id
        return self._call("POST", "/task.create", json=body)

    def send_message(self, task_id: str, content, connectors: list[str] | None = None) -> dict:
        message = {"content": content}
        if connectors:
            message["connectors"] = connectors
        body = {"task_id": task_id, "message": message}
        return self._call("POST", "/task.sendMessage", json=body)

    def task_detail(self, task_id: str) -> dict:
        return self._call("GET", "/task.detail", params={"task_id": task_id})

    def list_messages(self, task_id: str, limit: int = 5, order: str = "desc", verbose: bool = False) -> dict:
        params = {"task_id": task_id, "limit": limit, "order": order}
        if verbose:
            params["verbose"] = "true"
        return self._call("GET", "/task.listMessages", params=params)

    def upload_file(self, path: Path) -> str:
        record = self._call("POST", "/file.upload", json={"filename": path.name})
        upload_url = record["upload_url"]
        _require_https(upload_url)
        with open(path, "rb") as f:
            put_resp = self._external_http.put(upload_url, content=f.read())
        put_resp.raise_for_status()
        return record["file"]["id"]

    def download_file(self, url: str, dest: Path) -> None:
        _require_https(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._external_http.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)

    def poll_new_events(self, task_id: str, since_ms: int, timeout: float, poll_interval: float = 2.0):
        """Yield each event newer than since_ms, in chronological order, as soon as it appears.

        Stops (returns) right after yielding a status_update that reaches a terminal
        state. Anchoring on message timestamps rather than the task's current status
        avoids a race with sendMessage: right after sending, the task can still report
        the *previous* turn's "stopped" status for a moment, which previously made
        callers read back a stale reply.
        """
        seen: set[str] = set()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self.list_messages(task_id, limit=20, order="desc", verbose=True)
            new_events = [
                m for m in reversed(data["messages"]) if int(m["timestamp"]) > since_ms and m["id"] not in seen
            ]
            for msg in new_events:
                seen.add(msg["id"])
                yield msg
                if msg.get("type") == "status_update" and msg["status_update"]["agent_status"] in (
                    "stopped",
                    "waiting",
                    "error",
                ):
                    return
            time.sleep(poll_interval)
        raise TimeoutError(f"Tarefa {task_id} não concluiu em {timeout}s")


def last_assistant_entry(messages: list[dict]) -> dict | None:
    for msg in messages:
        if msg.get("type") == "assistant_message":
            return msg.get("assistant_message", {})
    return None


def last_assistant_message(messages: list[dict]) -> str | None:
    entry = last_assistant_entry(messages)
    return entry.get("content") if entry else None
