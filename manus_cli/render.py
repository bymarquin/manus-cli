from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

console = Console()
err_console = Console(stderr=True, style="bold red")


def print_assistant(content: str | None) -> None:
    if content is None:
        console.print("[dim](sem resposta do assistente ainda)[/dim]")
        return
    console.print(Markdown(content))


_PROGRESS_ICONS = {
    "tool_used": "🔧",
    "status_update": "•",
    "plan_update": "📋",
    "new_plan_step": "▸",
    "explanation": "💭",
}


def print_progress_event(msg: dict) -> None:
    event_type = msg.get("type")
    payload = msg.get(event_type, {}) if event_type else {}
    label = payload.get("brief") or payload.get("description")
    if not label:
        return
    icon = _PROGRESS_ICONS.get(event_type, "•")
    console.print(f"[dim]{icon} {label}[/dim]")


def print_error(prefix: str, message: str) -> None:
    err_console.print(f"{prefix}: {message}")


def print_header(cwd: str) -> None:
    console.print()
    console.print("  [bold]Manus CLI[/bold]")
    console.print(f"  [dim]Projeto:[/dim] {cwd}")
    console.print()
    console.print("[dim]Ctrl+C ou linha vazia para sair.[/dim]")
    console.print()


def print_history(tasks: list[dict]) -> None:
    if not tasks:
        console.print("[dim](nenhuma tarefa encontrada)[/dim]")
        return
    table = Table()
    table.add_column("id")
    table.add_column("título")
    table.add_column("status")
    for task in tasks:
        table.add_row(task["id"], task.get("title", ""), task.get("status", ""))
    console.print(table)


def print_status(task: dict) -> None:
    console.print(f"[bold]status[/bold]: {task['status']}")
    console.print(f"[bold]credit_usage[/bold]: {task.get('credit_usage', '?')}")
    console.print(f"[bold]url[/bold]: {task.get('task_url', '?')}")
