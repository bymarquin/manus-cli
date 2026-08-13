from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
import time
from pathlib import Path

from . import config
from .api import ManusAPIError, ManusClient, last_assistant_entry, last_assistant_message
from .render import (
    PROMPT,
    console,
    err_console,
    print_assistant,
    print_error,
    print_fail,
    print_header,
    print_history,
    print_status,
    print_success,
    print_warning,
    progress_label,
)

IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
MAX_PROJECT_FILE_BYTES = 10 * 1024 * 1024

SECRET_DIR_NAMES = {".ssh", ".aws", ".gnupg"}
SECRET_NAME_PATTERNS = [
    ".env", ".env.*", "*.pem", "*.key", "*_rsa", "id_rsa*",
    "credentials.json", "secrets*.json", "*.p12", "*.pfx", ".npmrc", ".netrc", "*.pgpass",
]


def _looks_like_secret(path: Path) -> bool:
    if any(part in SECRET_DIR_NAMES for part in path.parts):
        return True
    return any(fnmatch.fnmatch(path.name, pattern) for pattern in SECRET_NAME_PATTERNS)


def _client(timeout: float = 30.0) -> ManusClient:
    api_key = config.load_api_key()
    if not api_key:
        print_fail("Nenhuma API key configurada. Rode: manus login")
        sys.exit(1)
    return ManusClient(api_key, timeout=timeout)


def cmd_login() -> int:
    import getpass

    api_key = getpass.getpass("Manus API key: ").strip()
    if not api_key:
        print_fail("API key vazia.")
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
    print_success(f"key salva ({config.mask(api_key)})")
    return 0


def cmd_use(args: list[str]) -> int:
    if not args:
        print_fail("Uso: manus use <task_id> [--as <apelido>]")
        return 1
    task_id = args[0]
    alias = None
    if "--as" in args:
        idx = args.index("--as")
        if idx + 1 >= len(args):
            print_fail("Uso: manus use <task_id> --as <apelido>")
            return 1
        alias = args[idx + 1]
    client = _client()
    try:
        detail = client.task_detail(task_id)
    except ManusAPIError as e:
        print_error("Erro", e.message)
        return 1
    config.save_last_task(task_id)
    if alias:
        config.save_alias(alias, task_id)
    suffix = f" (apelido: {alias})" if alias else ""
    print_success(f"usando tarefa \"{detail['task']['title']}\" ({task_id}){suffix}")
    return 0


def cmd_alias(args: list[str]) -> int:
    if not args or args[0] != "list":
        print_fail("Uso: manus alias list")
        return 1
    aliases = config.load_aliases()
    if not aliases:
        console.print("[muted](nenhum apelido salvo — use: manus use <task_id> --as <apelido>)[/muted]")
        return 0
    for name, task_id in aliases.items():
        console.print(f"[accent]{name}[/accent] [muted]→[/muted] {task_id}")
    return 0


def cmd_history(args: list[str]) -> int:
    limit = int(args[0]) if args else 20
    client = _client()
    try:
        data = client.list_tasks(limit=limit)
    except ManusAPIError as e:
        print_error("Erro", e.message)
        return 1
    print_history(data.get("data", []))
    return 0


def cmd_open(args: list[str]) -> int:
    task_id = args[0] if args else config.load_last_task()
    if not task_id:
        print_fail("Nenhum task_id informado e nenhuma tarefa recente salva.")
        return 1
    import webbrowser

    url = f"https://manus.im/app/{task_id}"
    webbrowser.open(url)
    console.print(f"[muted]abrindo {url}[/muted]")
    return 0


def cmd_doctor(args: list[str]) -> int:
    import importlib.metadata

    ok = True

    try:
        version = importlib.metadata.version("manus-cli")
    except importlib.metadata.PackageNotFoundError:
        version = "dev (não instalado como pacote)"
    console.print(f"[muted]versão[/muted]  {version}")

    api_key = config.load_api_key()
    key_source = "MANUS_API_KEY" if os.environ.get("MANUS_API_KEY") else "credentials.json"
    if not api_key:
        print_fail("API key: nenhuma configurada (rode: manus login)")
        ok = False
    else:
        print_success(f"API key: presente ({key_source}, {config.mask(api_key)})")
        try:
            ManusClient(api_key).validate_key()
            print_success("Conectividade com api.manus.ai: ok")
        except ManusAPIError as e:
            print_fail(f"Conectividade: key rejeitada ({e.message})")
            ok = False
        except Exception as e:
            print_fail(f"Conectividade: falha de rede ({e})")
            ok = False

    console.print(f"[muted]config[/muted]   {config.CONFIG_DIR}")
    last_task = config.load_last_task()
    aliases = config.load_aliases()
    console.print(f"[muted]última tarefa: {last_task or '(nenhuma)'}, apelidos: {len(aliases)}[/muted]")

    project_rc = config.load_project_rc()
    if project_rc:
        console.print(f"[muted].manusrc neste diretório: {project_rc}[/muted]")

    return 0 if ok else 1


def cmd_status(args: list[str]) -> int:
    task_id = args[0] if args else config.load_last_task()
    if not task_id:
        print_fail("Nenhum task_id informado e nenhuma tarefa recente salva.")
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
        print_fail("Nenhum task_id informado e nenhuma tarefa recente salva.")
        return 1
    client = _client()
    try:
        data = client.list_messages(task_id, limit=5, order="desc")
    except ManusAPIError as e:
        print_error("Erro", e.message)
        return 1
    print_assistant(last_assistant_message(data["messages"]))
    return 0


def _collect_project_files(root: Path, allow_secret: bool = False) -> list[Path]:
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        if _looks_like_secret(path) and not allow_secret:
            print_warning(f"pulando {path} (parece segredo — use --allow-secret se for engano)")
            continue
        if path.stat().st_size > MAX_PROJECT_FILE_BYTES:
            err_console.print(f"[muted]pulando {path} (>10MB)[/muted]")
            continue
        files.append(path)
    return files


def _upload_files(client: ManusClient, paths: list[Path]) -> list[dict]:
    content = []
    for path in paths:
        file_id = client.upload_file(path)
        content.append({"type": "file", "file_id": file_id, "filename": path.name})
    return content


OUTPUT_DIR = Path("manus-output")


def _download_attachments(client: ManusClient, task_id: str, attachments: list[dict]) -> list[str]:
    base = (OUTPUT_DIR / task_id).resolve()
    saved = []
    for att in attachments:
        url = att.get("url")
        if not url:
            continue
        safe_name = os.path.basename(att.get("filename") or "arquivo") or "arquivo"
        dest = (base / safe_name).resolve()
        if dest != base and base not in dest.parents:
            err_console.print(f"[muted]anexo com nome suspeito ignorado: {att.get('filename')!r}[/muted]")
            continue
        client.download_file(url, dest)
        err_console.print(f"[muted]↓ salvo em {dest}[/muted]")
        saved.append(str(dest))
    return saved


def _run_turn(
    client: ManusClient,
    task_id: str | None,
    content,
    timeout: float,
    connectors: list[str] | None = None,
    json_output: bool = False,
) -> str:
    since_ms = int(time.time() * 1000)
    if task_id is None:
        resp = client.create_task(content, connectors=connectors)
        task_id = resp["task_id"]
    else:
        client.send_message(task_id, content, connectors=connectors)

    status = None
    if json_output:
        for msg in client.poll_new_events(task_id, since_ms, timeout=timeout):
            if msg.get("type") == "status_update":
                status = msg["status_update"]["agent_status"]
    else:
        with console.status("[accent]Manus trabalhando...[/accent]", spinner="dots") as live:
            for msg in client.poll_new_events(task_id, since_ms, timeout=timeout):
                if msg.get("type") == "status_update":
                    status = msg["status_update"]["agent_status"]
                label = progress_label(msg)
                if label:
                    live.update(f"[accent]{label}[/accent]")
    # Only persist task_id once we know it's real — task.create can return a
    # task_id that 404s on every read (Manus backend bug), and saving it here
    # unconditionally used to clobber a previously-working last_task_id with
    # a dead one, breaking --continue on the *next* invocation too.
    config.save_last_task(task_id)
    if not json_output:
        if status == "stopped":
            print_success(f"Tarefa {status}")
        else:
            print_warning(f"Tarefa {status}")

    data = client.list_messages(task_id, limit=5, order="desc")
    entry = last_assistant_entry(data["messages"])
    content_text = entry.get("content") if entry else None
    attachments = _download_attachments(client, task_id, entry["attachments"]) if entry and entry.get("attachments") else []
    if json_output:
        print(json.dumps({
            "task_id": task_id,
            "status": status,
            "content": content_text,
            "attachments": attachments,
        }, ensure_ascii=False))
    else:
        print_assistant(content_text)
        console.print()
    return task_id


_MENTION_RE = re.compile(r"@(\S+)")
_MENTION_TRAILING_PUNCT = ".,;:!?)\"'"


def _extract_mentions(text: str) -> list[Path]:
    paths = []
    for match in _MENTION_RE.finditer(text):
        candidate = Path(match.group(1).rstrip(_MENTION_TRAILING_PUNCT))
        if candidate.is_file():
            paths.append(candidate)
    return paths


_SLASH_HELP = "/status  /use <id>  /history  /open [id]  /help  /exit"


def _run_slash_command(client: ManusClient, task_id: str | None, line: str) -> tuple[str | None, bool]:
    """Handle a '/comando' typed in the REPL. Returns (task_id, should_exit)."""
    parts = line[1:].strip().split(maxsplit=1)
    cmd = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("exit", "quit"):
        return task_id, True

    if cmd == "help":
        console.print(f"[muted]{_SLASH_HELP}[/muted]")
        return task_id, False

    if cmd == "status":
        if not task_id:
            print_fail("Nenhuma tarefa ativa ainda.")
        else:
            try:
                print_status(client.task_detail(task_id)["task"])
            except ManusAPIError as e:
                print_error("Erro", e.message)
        return task_id, False

    if cmd == "use":
        if not arg:
            print_fail("Uso: /use <task_id>")
            return task_id, False
        try:
            detail = client.task_detail(arg)
        except ManusAPIError as e:
            print_error("Erro", e.message)
            return task_id, False
        config.save_last_task(arg)
        print_success(f"usando tarefa \"{detail['task']['title']}\" ({arg})")
        return arg, False

    if cmd == "history":
        try:
            data = client.list_tasks(limit=10)
        except ManusAPIError as e:
            print_error("Erro", e.message)
            return task_id, False
        print_history(data.get("data", []))
        return task_id, False

    if cmd == "open":
        target = arg or task_id
        if not target:
            print_fail("Nenhuma tarefa pra abrir.")
        else:
            import webbrowser

            url = f"https://manus.im/app/{target}"
            webbrowser.open(url)
            console.print(f"[muted]abrindo {url}[/muted]")
        return task_id, False

    print_fail(f"Comando desconhecido: /{cmd} (tente /help)")
    return task_id, False


def cmd_chat(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="manus")
    parser.add_argument("prompt", nargs="*")
    parser.add_argument("--continue", dest="continue_", action="store_true")
    parser.add_argument("--file", dest="file", default=None)
    parser.add_argument("--project", dest="project", default=None)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--connector", dest="connectors", action="append", default=None)
    parser.add_argument("--json", dest="json_output", action="store_true")
    parser.add_argument("--task", dest="task_alias", default=None)
    parser.add_argument("--allow-secret", dest="allow_secret", action="store_true")
    args = parser.parse_args(argv)

    prompt_text = " ".join(args.prompt) if args.prompt else None

    stdin_data = None
    if not sys.stdin.isatty():
        stdin_data = sys.stdin.read().strip() or None
    if stdin_data:
        prompt_text = f"{stdin_data}\n\n{prompt_text}" if prompt_text else stdin_data

    client = _client(timeout=args.timeout + 10)

    project_rc = config.load_project_rc()
    connectors = args.connectors or project_rc.get("connectors")

    task_id = config.load_last_task() if args.continue_ else None
    if args.continue_ and not task_id:
        print_fail("Nenhuma tarefa anterior para continuar.")
        return 1
    if args.task_alias:
        task_id = config.resolve_alias(args.task_alias)
        if not task_id:
            print_fail(f"Apelido desconhecido: {args.task_alias!r} (veja: manus alias list)")
            return 1
    if task_id is None and project_rc.get("task_id"):
        task_id = project_rc["task_id"]
        err_console.print(f"[muted]usando tarefa de .manusrc ({task_id})[/muted]")

    try:
        if args.file:
            file_path = Path(args.file)
            if not file_path.is_file():
                print_fail(f"Arquivo não encontrado: {file_path}")
                return 1
            content = _upload_files(client, [file_path])
            if prompt_text:
                content.append({"type": "text", "text": prompt_text})
            task_id = _run_turn(client, task_id, content, args.timeout, connectors, args.json_output)
            return 0

        if args.project:
            root = Path(args.project)
            if not root.is_dir():
                print_fail(f"Diretório não encontrado: {root}")
                return 1
            files = _collect_project_files(root, args.allow_secret)
            console.print(f"[muted]subindo {len(files)} arquivo(s) de {root}...[/muted]")
            content = _upload_files(client, files)
            if prompt_text:
                content.append({"type": "text", "text": prompt_text})
            task_id = _run_turn(client, task_id, content, args.timeout, connectors, args.json_output)
            return 0

        if prompt_text:
            task_id = _run_turn(client, task_id, prompt_text, args.timeout, connectors, args.json_output)
            return 0

        # REPL
        print_header(os.getcwd())
        while True:
            try:
                line = console.input(f"[accent]{PROMPT}[/accent] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print()
                return 0
            if not line:
                return 0
            if line.startswith("/"):
                task_id, should_exit = _run_slash_command(client, task_id, line)
                if should_exit:
                    return 0
                continue
            mentioned = _extract_mentions(line)
            if mentioned:
                turn_content = _upload_files(client, mentioned)
                turn_content.append({"type": "text", "text": line})
            else:
                turn_content = line
            task_id = _run_turn(client, task_id, turn_content, args.timeout, connectors, args.json_output)
    except ManusAPIError as e:
        print_error("Erro", e.message)
        return 1
    except TimeoutError as e:
        print_fail(str(e))
        return 1


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "login":
        sys.exit(cmd_login())
    if argv and argv[0] == "use":
        sys.exit(cmd_use(argv[1:]))
    if argv and argv[0] == "history":
        sys.exit(cmd_history(argv[1:]))
    if argv and argv[0] == "open":
        sys.exit(cmd_open(argv[1:]))
    if argv and argv[0] == "alias":
        sys.exit(cmd_alias(argv[1:]))
    if argv and argv[0] == "doctor":
        sys.exit(cmd_doctor(argv[1:]))
    if argv and argv[0] == "status":
        sys.exit(cmd_status(argv[1:]))
    if argv and argv[0] == "result":
        sys.exit(cmd_result(argv[1:]))
    sys.exit(cmd_chat(argv))


if __name__ == "__main__":
    main()
