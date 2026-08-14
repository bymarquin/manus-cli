from __future__ import annotations

import time
from dataclasses import dataclass, field

from .api import ManusAPIError, ManusClient

# Timer/sleep de SO (sobretudo Windows, com granularidade de ~15ms) pode acordar
# ligeiramente antes do deadline mesmo após consumir o orçamento inteiro de uma
# requisição. Sem essa folga, um erro de rede bem na borda do timeout vazava cru
# pro chamador em vez do TaskTimeoutError previsível.
_DEADLINE_EPSILON_S = 0.05
_STRUCTURED_OUTPUT_GRACE_S = 10.0

TERMINAL_STATUSES = ("stopped", "waiting", "error")

# https://open.manus.ai/docs/v2/task-lifecycle — waiting_for_event_type "messageAskUser"
# is answered via task.sendMessage; every other waiting event type is answered via
# task.confirmAction.
MESSAGE_ASK_USER_EVENT_TYPE = "messageAskUser"


class TaskTimeoutError(TimeoutError):
    pass


def last_assistant_entry(messages: list[dict]) -> dict | None:
    for msg in messages:
        if msg.get("type") == "assistant_message":
            return msg.get("assistant_message") or {}
    return None


def last_assistant_message(messages: list[dict]) -> str | None:
    entry = last_assistant_entry(messages)
    return entry.get("content") if entry else None


@dataclass
class TaskOutcome:
    task_id: str
    status: str  # "stopped" | "waiting" | "error"
    content: str | None
    attachments: list[dict] = field(default_factory=list)
    status_detail: dict | None = None
    error_detail: dict | None = None
    structured_output: dict | None = None

    @property
    def needs_confirm(self) -> bool:
        if self.status != "waiting" or not self.status_detail:
            return False
        return self.status_detail.get("waiting_for_event_type") != MESSAGE_ASK_USER_EVENT_TYPE

    @property
    def needs_reply(self) -> bool:
        if self.status != "waiting" or not self.status_detail:
            return False
        return self.status_detail.get("waiting_for_event_type") == MESSAGE_ASK_USER_EVENT_TYPE


def start_or_continue(
    client: ManusClient,
    task_id: str | None,
    content,
    *,
    request_timeout: float | None = None,
    **create_kwargs,
) -> str:
    if task_id is None:
        resp = client.create_task(content, request_timeout=request_timeout, **create_kwargs)
        return resp["task_id"]
    connectors = create_kwargs.get("connectors")
    structured_output_schema = create_kwargs.get("structured_output_schema")
    client.send_message(
        task_id,
        content,
        connectors=connectors,
        structured_output_schema=structured_output_schema,
        request_timeout=request_timeout,
    )
    return task_id


def _extract_status_update(msg: dict) -> tuple[str | None, dict | None]:
    """Safely pull (agent_status, status_detail) out of a status_update event.

    Malformed/unexpected shapes are tolerated — return (None, None) rather than
    raising a KeyError, since a single odd event should never crash polling.
    """
    su = msg.get("status_update")
    if not isinstance(su, dict):
        return None, None
    status = su.get("agent_status")
    if status not in TERMINAL_STATUSES:
        return None, None
    return status, su


def poll_until_settled(
    client: ManusClient,
    task_id: str,
    since_ms: int,
    timeout: float,
    on_event=None,
    *,
    deadline: float | None = None,
):
    """Poll task.listMessages until a status_update newer than since_ms reaches a
    terminal state. Returns (status, status_detail).

    The timeout is a hard wall-clock budget for the whole loop: each individual
    list_messages call inherits the client's own request timeout (bounded
    separately), and the sleep between polls is clamped so it never overshoots
    the deadline.

    Uses `cursor`-based pagination each poll, walking newest-to-oldest only as far
    as since_ms, so a burst of many events between polls can't push an
    older-but-still-new event off the first page before it's ever seen — without
    re-fetching the entire conversation history on every single poll.
    """
    seen: set[str] = set()
    deadline = deadline if deadline is not None else time.monotonic() + timeout
    status: str | None = None
    status_detail: dict | None = None

    while True:
        new_batch: list[dict] = []  # collected newest-first; reversed before dispatch
        cursor = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TaskTimeoutError(f"Tarefa {task_id} não concluiu em {timeout}s")
            try:
                data = client.list_messages(
                    task_id,
                    limit=20,
                    order="desc",
                    verbose=True,
                    cursor=cursor,
                    request_timeout=remaining,
                )
            except ManusAPIError:
                if time.monotonic() >= deadline - _DEADLINE_EPSILON_S:
                    raise TaskTimeoutError(f"Tarefa {task_id} não concluiu em {timeout}s") from None
                raise
            messages = data.get("messages") or []
            reached_old = False
            for msg in messages:
                mid = msg.get("id")
                ts_raw = msg.get("timestamp")
                if mid is None:
                    continue
                try:
                    ts = int(ts_raw)
                except (TypeError, ValueError):
                    continue
                if ts <= since_ms:
                    reached_old = True
                    break
                if mid not in seen:
                    seen.add(mid)
                    new_batch.append(msg)
            if reached_old or not (data.get("has_more") and data.get("next_cursor")):
                break
            cursor = data.get("next_cursor")

        for msg in reversed(new_batch):  # chronological order for the caller's callback
            if on_event:
                on_event(msg)
            if msg.get("type") == "status_update":
                new_status, new_detail = _extract_status_update(msg)
                if new_status:
                    status, status_detail = new_status, new_detail

        if status:
            return status, status_detail

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TaskTimeoutError(f"Tarefa {task_id} não concluiu em {timeout}s")
        time.sleep(min(2.0, remaining))


def build_outcome(
    client: ManusClient,
    task_id: str,
    status: str,
    status_detail: dict | None,
    *,
    since_ms: int | None = None,
    request_timeout: float | None = None,
) -> TaskOutcome:
    data = client.list_messages(
        task_id, limit=50, order="desc", verbose=True, request_timeout=request_timeout
    )
    messages = data.get("messages") or []
    entry = last_assistant_entry(messages)
    content = entry.get("content") if entry else None
    attachments = entry.get("attachments") if entry else None

    error_detail = None
    structured_output = None
    for msg in messages:
        if msg.get("type") != "structured_output_result":
            continue
        candidate = msg.get("structured_output_result")
        if not isinstance(candidate, dict):
            continue
        # Newest-first: this is the most recent structured_output_result in the
        # window. If it predates this turn (extraction runs *after* the turn
        # ends, so it can lag behind "stopped" by a few seconds), it belongs to
        # an earlier turn on the same task_id — treat as "not landed yet" rather
        # than acting on a stale decision from several turns ago.
        if since_ms is not None:
            try:
                msg_ts = int(msg.get("timestamp"))
            except (TypeError, ValueError):
                msg_ts = None
            if msg_ts is None or msg_ts <= since_ms:
                break
        structured_output = candidate
        break

    if status == "error":
        for msg in messages:
            if msg.get("type") == "error_message":
                error_detail = msg.get("error_message")
                break

    return TaskOutcome(
        task_id=task_id,
        status=status,
        content=content,
        attachments=attachments or [],
        status_detail=status_detail,
        error_detail=error_detail,
        structured_output=structured_output,
    )


def run_turn(
    client: ManusClient,
    task_id: str | None,
    content,
    timeout: float,
    on_event=None,
    **create_kwargs,
) -> TaskOutcome:
    deadline = time.monotonic() + timeout
    since_ms = int(time.time() * 1000)
    task_id = start_or_continue(
        client,
        task_id,
        content,
        request_timeout=max(0.001, deadline - time.monotonic()),
        **create_kwargs,
    )
    status, status_detail = poll_until_settled(
        client, task_id, since_ms, timeout, on_event=on_event, deadline=deadline
    )
    expect_structured = bool(create_kwargs.get("structured_output_schema")) and status == "stopped"
    structured_deadline = min(deadline, time.monotonic() + _STRUCTURED_OUTPUT_GRACE_S)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TaskTimeoutError(f"Tarefa {task_id} não concluiu em {timeout}s")
        request_timeout = remaining
        if expect_structured:
            request_timeout = min(request_timeout, max(0.001, structured_deadline - time.monotonic()))
        outcome = build_outcome(
            client, task_id, status, status_detail, since_ms=since_ms, request_timeout=request_timeout
        )
        if not expect_structured or outcome.structured_output is not None:
            return outcome
        wait = structured_deadline - time.monotonic()
        if wait <= 0:
            return outcome
        time.sleep(min(0.5, wait))


def confirm_action(client: ManusClient, task_id: str, event_id: str, input_data: dict | None = None) -> dict:
    return client.confirm_action(task_id, event_id, input_data)
