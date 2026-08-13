from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config
from .api import ManusAPIError, ManusClient, last_assistant_message
from .render import console, err_console, print_assistant, print_error, print_status

IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
MAX_PROJECT_FILE_BYTES = 10 * 1024 * 1024


def _client(timeout: float = 30.0) -> ManusClient:
    api_key = config.load_api_key()
    if not api_key:
        err_console.print("Nenhuma API key configurada. Rode: manus login")
        sys.exit(1)
    return ManusClient(api_key, timeout=timeout)


def cmd_login() -> int:
    import getpass

    api_key = getpass.getpass("Manus API key: ").strip()
    if not api_key:
        err_console.print("API key vazia.")
        return 1
    client = ManusClient(api_key)
    try:
        client.validate_key()
    except ManusAPIError as e:
        print_error("Falha ao validar a key", e.message)
        return 1
    except Exception as e:
        print_error("Falha de rede ao validar a key", str(e))
        return 1
    config.save_api_key(api_key)
    console.print(f"[green]OK[/green] key salva ({config.mask(api_key)})")
    return 0


def cmd_status(args: list[str]) -> int:
    task_id = args[0] if args else config.load_last_task()
    if not task_id:
        err_console.print("Nenhum task_id informado e nenhuma tarefa recente salva.")
        return 1
    client = _client()
    try:
        detail = client.task_detail(task_id)
    except ManusAPIError as e:
        print_error("Erro", e.message)
        return 1
    print_status(detail["task"])
    return 0


def cmd_result(args: list[str]) -> int:
    task_id = args[0] if args else config.load_last_task()
    if not task_id:
        err_console.print("Nenhum task_id informado e nenhuma tarefa recente salva.")
        return 1
    client = _client()
    try:
        data = client.list_messages(task_id, limit=5, order="desc")
    except ManusAPIError as e:
        print_error("Erro", e.message)
        return 1
    print_assistant(last_assistant_message(data["messages"]))
    return 0


def _collect_project_files(root: Path) -> list[Path]:
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        if path.stat().st_size > MAX_PROJECT_FILE_BYTES:
            err_console.print(f"[dim]pulando {path} (>10MB)[/dim]")
            continue
        files.append(path)
    return files


def _upload_files(client: ManusClient, paths: list[Path]) -> list[dict]:
    content = []
    for path in paths:
        file_id = client.upload_file(path)
        content.append({"type": "file", "file_id": file_id, "filename": path.name})
    return content


def _run_turn(client: ManusClient, task_id: str | None, content, timeout: float) -> str:
    if task_id is None:
        resp = client.create_task(content)
        task_id = resp["task_id"]
    else:
        client.send_message(task_id, content)
    config.save_last_task(task_id)
    client.wait_for_completion(task_id, timeout=timeout)
    data = client.list_messages(task_id, limit=5, order="desc")
    print_assistant(last_assistant_message(data["messages"]))
    return task_id


def cmd_chat(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="manus")
    parser.add_argument("prompt", nargs="*")
    parser.add_argument("--continue", dest="continue_", action="store_true")
    parser.add_argument("--file", dest="file", default=None)
    parser.add_argument("--project", dest="project", default=None)
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args(argv)

    prompt_text = " ".join(args.prompt) if args.prompt else None
    client = _client(timeout=args.timeout + 10)

    task_id = config.load_last_task() if args.continue_ else None
    if args.continue_ and not task_id:
        err_console.print("Nenhuma tarefa anterior para continuar.")
        return 1

    try:
        if args.file:
            file_path = Path(args.file)
            if not file_path.is_file():
                err_console.print(f"Arquivo não encontrado: {file_path}")
                return 1
            content = _upload_files(client, [file_path])
            if prompt_text:
                content.append({"type": "text", "text": prompt_text})
            task_id = _run_turn(client, task_id, content, args.timeout)
            return 0

        if args.project:
            root = Path(args.project)
            if not root.is_dir():
                err_console.print(f"Diretório não encontrado: {root}")
                return 1
            files = _collect_project_files(root)
            console.print(f"[dim]subindo {len(files)} arquivo(s) de {root}...[/dim]")
            content = _upload_files(client, files)
            if prompt_text:
                content.append({"type": "text", "text": prompt_text})
            task_id = _run_turn(client, task_id, content, args.timeout)
            return 0

        if prompt_text:
            task_id = _run_turn(client, task_id, prompt_text, args.timeout)
            return 0

        # REPL
        console.print("[dim]Modo chat. Ctrl+C ou linha vazia para sair.[/dim]")
        while True:
            try:
                line = console.input("[bold cyan]> [/bold cyan]").strip()
            except (EOFError, KeyboardInterrupt):
                console.print()
                return 0
            if not line:
                return 0
            task_id = _run_turn(client, task_id, line, args.timeout)
    except ManusAPIError as e:
        print_error("Erro", e.message)
        return 1
    except TimeoutError as e:
        err_console.print(str(e))
        return 1


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "login":
        sys.exit(cmd_login())
    if argv and argv[0] == "status":
        sys.exit(cmd_status(argv[1:]))
    if argv and argv[0] == "result":
        sys.exit(cmd_result(argv[1:]))
    sys.exit(cmd_chat(argv))


if __name__ == "__main__":
    main()
