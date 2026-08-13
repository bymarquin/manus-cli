from __future__ import annotations

from rich.console import Console

console = Console()
err_console = Console(stderr=True, style="bold red")


def print_assistant(content: str | None) -> None:
    if content is None:
        console.print("[dim](sem resposta do assistente ainda)[/dim]")
        return
    console.print(content)


def print_error(prefix: str, message: str) -> None:
    err_console.print(f"{prefix}: {message}")


def print_status(task: dict) -> None:
    console.print(f"[bold]status[/bold]: {task['status']}")
    console.print(f"[bold]credit_usage[/bold]: {task.get('credit_usage', '?')}")
    console.print(f"[bold]url[/bold]: {task.get('task_url', '?')}")
