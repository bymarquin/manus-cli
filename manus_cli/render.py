from __future__ import annotations

from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.theme import Theme

_THEME = Theme(
    {
        "accent": "bold cyan",
        "muted": "dim",
        "success": "bold green",
        "error": "bold red",
        "warning": "bold yellow",
    }
)

console = Console(theme=_THEME)
err_console = Console(stderr=True, theme=_THEME)

# Alguns consoles Windows (ex: binário PyInstaller com stdout redirecionado em CI,
# codepage cp1252/cp437) não representam ❯✓✗⚠ e explodem com UnicodeEncodeError.
# Detecta a capacidade real do encoding e cai pra ASCII equivalente nesse caso,
# mantendo os glifos Unicode em qualquer console que os suporte (a maioria).
_GLYPH_SAMPLE = "❯✓✗⚠—"


def _supports_unicode(encoding: str | None) -> bool:
    if not encoding:
        return True
    try:
        _GLYPH_SAMPLE.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def _console_supports_unicode(*consoles: Console) -> bool:
    return all(_supports_unicode(getattr(c.file, "encoding", None)) for c in consoles)


_UNICODE_OK = _console_supports_unicode(console, err_console)

PROMPT = "❯" if _UNICODE_OK else ">"
_CHECK = "✓" if _UNICODE_OK else "+"
_CROSS = "✗" if _UNICODE_OK else "x"
_WARN = "⚠" if _UNICODE_OK else "!"
_DASH = "—" if _UNICODE_OK else "-"


def print_assistant(content: str | None) -> None:
    if content is None:
        console.print(f"[muted]{_DASH} sem resposta do assistente ainda {_DASH}[/muted]")
        return
    console.print(Markdown(content))


def progress_label(msg: dict) -> str | None:
    event_type = msg.get("type")
    payload = msg.get(event_type, {}) if event_type else {}
    return payload.get("brief") or payload.get("description")


def print_success(message: str) -> None:
    console.print(f"[success]{_CHECK}[/success] {message}")


def print_error(prefix: str, message: str) -> None:
    err_console.print(f"[error]{_CROSS}[/error] {prefix}: {message}")


def print_fail(message: str) -> None:
    err_console.print(f"[error]{_CROSS}[/error] {message}")


def print_warning(message: str) -> None:
    err_console.print(f"[warning]{_WARN}[/warning] {message}")


def print_header(cwd: str) -> None:
    console.print()
    console.print(f"  [accent]{PROMPT}[/accent] [bold]Manus CLI[/bold]")
    console.print(f"    [muted]{cwd}[/muted]")
    console.print()
    console.print("  [muted]/help para comandos · Ctrl+C ou linha vazia para sair[/muted]")
    console.print()


def print_history(tasks: list[dict]) -> None:
    if not tasks:
        console.print(f"[muted]{_DASH} nenhuma tarefa encontrada {_DASH}[/muted]")
        return
    table = Table(box=box.SIMPLE_HEAD, header_style="accent", border_style="muted")
    table.add_column("id")
    table.add_column("título")
    table.add_column("status")
    for task in tasks:
        table.add_row(task["id"], task.get("title", ""), task.get("status", ""))
    console.print(table)


def print_status(task: dict) -> None:
    console.print(f"[muted]status[/muted]       {task['status']}")
    console.print(f"[muted]credit_usage[/muted] {task.get('credit_usage', '?')}")
    console.print(f"[muted]url[/muted]          {task.get('task_url', '?')}")


def print_waiting(status_detail: dict | None) -> None:
    detail = status_detail or {}
    event_type = detail.get("waiting_for_event_type", "?")
    event_id = detail.get("waiting_for_event_id", "?")
    description = detail.get("waiting_description") or "(sem descrição)"
    console.print(f"[warning]{_WARN}[/warning] Tarefa esperando: {description}")
    console.print(f"  [muted]event_type[/muted] {event_type}")
    console.print(f"  [muted]event_id[/muted]   {event_id}")
    if event_type == "messageAskUser":
        console.print("  [muted]→ responda normalmente (mensagem de texto) para continuar[/muted]")
    else:
        console.print(f"  [muted]→ manus confirm {event_id} [--input '<json>'][/muted]")


def print_task_error(error_detail: dict | None) -> None:
    detail = error_detail or {}
    error_type = detail.get("error_type", "?")
    content = detail.get("content") or "(sem detalhes)"
    console.print(f"[error]{_CROSS}[/error] Tarefa falhou ({error_type}): {content}")


def print_connectors(connectors: list[dict]) -> None:
    if not connectors:
        console.print(f"[muted]{_DASH} nenhum connector configurado nesta conta {_DASH}[/muted]")
        return
    table = Table(box=box.SIMPLE_HEAD, header_style="accent", border_style="muted")
    table.add_column("id")
    table.add_column("nome")
    table.add_column("tipo")
    table.add_column("categoria")
    for c in connectors:
        table.add_row(c.get("id", ""), c.get("name", ""), c.get("type", ""), c.get("category", ""))
    console.print(table)


def print_projects(projects: list[dict]) -> None:
    if not projects:
        console.print(f"[muted]{_DASH} nenhum projeto criado nesta conta {_DASH}[/muted]")
        return
    table = Table(box=box.SIMPLE_HEAD, header_style="accent", border_style="muted")
    table.add_column("id")
    table.add_column("nome")
    table.add_column("instrução")
    for p in projects:
        instruction = p.get("instruction") or ""
        if len(instruction) > 60:
            instruction = instruction[:57] + "..."
        table.add_row(p.get("id", ""), p.get("name", ""), instruction)
    console.print(table)


def print_dry_run(selected: list, skipped: list, total_bytes: int) -> None:
    console.print(
        f"[accent]{_DASH} dry-run: {len(selected)} arquivo(s), {total_bytes} bytes, "
        f"nada foi enviado {_DASH}[/accent]"
    )
    for f in selected:
        console.print(f"  [success]{_CHECK}[/success] {f.relative_path.as_posix()} [muted]({f.size} bytes)[/muted]")
    for s in skipped:
        console.print(f"  [warning]{_DASH}[/warning] {s.relative_path.as_posix()} [muted]{s.reason}[/muted]")
