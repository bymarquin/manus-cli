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

PROMPT = "❯"


def print_assistant(content: str | None) -> None:
    if content is None:
        console.print("[muted]— sem resposta do assistente ainda —[/muted]")
        return
    console.print(Markdown(content))


def progress_label(msg: dict) -> str | None:
    event_type = msg.get("type")
    payload = msg.get(event_type, {}) if event_type else {}
    return payload.get("brief") or payload.get("description")


def print_success(message: str) -> None:
    console.print(f"[success]✓[/success] {message}")


def print_error(prefix: str, message: str) -> None:
    err_console.print(f"[error]✗[/error] {prefix}: {message}")


def print_fail(message: str) -> None:
    err_console.print(f"[error]✗[/error] {message}")


def print_warning(message: str) -> None:
    err_console.print(f"[warning]⚠[/warning] {message}")


def print_header(cwd: str) -> None:
    console.print()
    console.print(f"  [accent]{PROMPT}[/accent] [bold]Manus CLI[/bold]")
    console.print(f"    [muted]{cwd}[/muted]")
    console.print()
    console.print("  [muted]/help para comandos · Ctrl+C ou linha vazia para sair[/muted]")
    console.print()


def print_history(tasks: list[dict]) -> None:
    if not tasks:
        console.print("[muted]— nenhuma tarefa encontrada —[/muted]")
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
