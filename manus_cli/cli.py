from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.styles import Style
from rich.markup import escape

from . import config, files, task_runner
from .agent_policy import ApprovalMode, PolicyEngine
from .api import AGENT_PROFILES, SHARE_VISIBILITIES, ManusAPIError, ManusClient
from .coding_agent import AgentStep, CodingAgent, ManusCodingAdapter
from .config import ConfigError
from .render import (
    PROMPT,
    console,
    err_console,
    print_assistant,
    print_connectors,
    print_dry_run,
    print_error,
    print_fail,
    print_header,
    print_history,
    print_projects,
    print_status,
    print_success,
    print_task_error,
    print_waiting,
    print_warning,
    progress_label,
)
from .workspace_tools import WorkspaceTools

OUTPUT_DIR = Path("manus-output")

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _client(timeout: float = 30.0) -> ManusClient:
    try:
        api_key = config.load_api_key()
    except ConfigError as e:
        print_fail(str(e))
        sys.exit(1)
    if not api_key:
        print_fail("Nenhuma API key configurada. Rode: manus login")
        sys.exit(1)
    return ManusClient(api_key, timeout=timeout)


def _resolve_connectors(client: ManusClient, raw_values: list[str] | None) -> list[str] | None:
    """UUIDs pass through; anything else is resolved by name via connector.list."""
    if not raw_values:
        return None
    resolved = []
    connector_cache: list[dict] | None = None
    for value in raw_values:
        if _UUID_RE.match(value):
            resolved.append(value)
            continue
        if connector_cache is None:
            connector_cache = client.list_connectors().get("data", [])
        matches = [c for c in connector_cache if c.get("name", "").lower() == value.lower()]
        if not matches:
            matches = [c for c in connector_cache if value.lower() in c.get("name", "").lower()]
        if not matches:
            names = ", ".join(c.get("name", "?") for c in connector_cache) or "(nenhum configurado)"
            raise ManusAPIError("connector_not_found", f"connector {value!r} não encontrado. Disponíveis: {names}")
        if len(matches) > 1:
            names = ", ".join(c.get("name", "?") for c in matches)
            raise ManusAPIError(
                "connector_ambiguous", f"{value!r} corresponde a vários connectors ({names}); use o UUID direto"
            )
        resolved.append(matches[0]["id"])
    return resolved


def _resolve_project(client: ManusClient, raw_value: str | None) -> str | None:
    """UUID passes through; anything else is resolved by name via project.list."""
    if not raw_value:
        return None
    if _UUID_RE.match(raw_value):
        return raw_value
    projects = client.list_projects().get("data", [])
    matches = [p for p in projects if p.get("name", "").lower() == raw_value.lower()]
    if not matches:
        matches = [p for p in projects if raw_value.lower() in p.get("name", "").lower()]
    if not matches:
        names = ", ".join(p.get("name", "?") for p in projects) or "(nenhum criado)"
        raise ManusAPIError("project_not_found", f"projeto {raw_value!r} não encontrado. Disponíveis: {names}")
    if len(matches) > 1:
        names = ", ".join(p.get("name", "?") for p in matches)
        raise ManusAPIError(
            "project_ambiguous", f"{raw_value!r} corresponde a vários projetos ({names}); use o id direto"
        )
    return matches[0]["id"]


# ---------------------------------------------------------------------------
# Simple subcommands
# ---------------------------------------------------------------------------


def cmd_login(argv: list[str]) -> int:
    import getpass

    api_key = getpass.getpass("Manus API key: ").strip()
    if not api_key:
        print_fail("API key vazia.")
        return 1
    with ManusClient(api_key) as client:
        try:
            client.validate_key()
        except ManusAPIError as e:
            print_error("Falha ao validar a key", e.message)
            return 1
        except Exception as e:  # noqa: BLE001 — top-level CLI boundary: any network failure here should
            print_error("Falha de rede ao validar a key", str(e))  # become a friendly message, not a traceback.
            return 1
    config.save_api_key(api_key)
    print_success(f"key salva ({config.mask(api_key)})")
    return 0


def cmd_use(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="manus use", description="Fixa uma tarefa existente como a atual")
    parser.add_argument("task_id")
    parser.add_argument("--as", dest="alias", metavar="APELIDO", default=None)
    args = parser.parse_args(argv)

    with _client() as client:
        try:
            detail = client.task_detail(args.task_id)
        except ManusAPIError as e:
            print_error("Erro", e.message)
            return 1

    config.save_last_task(args.task_id)
    if args.alias:
        config.save_alias(args.alias, args.task_id)
    suffix = f" (apelido: {args.alias})" if args.alias else ""
    print_success(f"usando tarefa \"{detail['task']['title']}\" ({args.task_id}){suffix}")
    return 0


def cmd_alias(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="manus alias")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list")
    args = parser.parse_args(argv)

    if args.action == "list":
        aliases = config.load_aliases()
        if not aliases:
            console.print("[muted]— nenhum apelido salvo — use: manus use <task_id> --as <apelido> —[/muted]")
            return 0
        for name, task_id in aliases.items():
            console.print(f"[accent]{name}[/accent] [muted]→[/muted] {task_id}")
    return 0


def cmd_history(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="manus history", description="Lista tarefas recentes")
    parser.add_argument("limit", nargs="?", type=int, default=20, help="quantidade a listar (padrão: 20)")
    args = parser.parse_args(argv)
    if args.limit <= 0:
        print_fail("limite deve ser um número positivo")
        return 1

    with _client() as client:
        try:
            data = client.list_tasks(limit=args.limit)
        except ManusAPIError as e:
            print_error("Erro", e.message)
            return 1
    print_history(data.get("data", []))
    return 0


def cmd_connector(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="manus connector")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list")
    args = parser.parse_args(argv)

    if args.action == "list":
        with _client() as client:
            try:
                data = client.list_connectors()
            except ManusAPIError as e:
                print_error("Erro", e.message)
                return 1
        print_connectors(data.get("data", []))
    return 0


def cmd_open(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="manus open")
    parser.add_argument("task_id", nargs="?", default=None)
    args = parser.parse_args(argv)

    task_id = args.task_id or config.load_last_task()
    if not task_id:
        print_fail("Nenhum task_id informado e nenhuma tarefa recente salva.")
        return 1
    import webbrowser

    url = f"https://manus.im/app/{task_id}"
    webbrowser.open(url)
    console.print(f"[muted]abrindo {url}[/muted]")
    return 0


def cmd_confirm(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="manus confirm", description="Confirma uma ação pendente (task.confirmAction)"
    )
    parser.add_argument("event_id")
    parser.add_argument("--input", dest="input_json", default=None, help="JSON com os dados do confirm_input_schema")
    parser.add_argument("--task", dest="task_id", default=None)
    args = parser.parse_args(argv)

    task_id = args.task_id or config.load_last_task()
    if not task_id:
        print_fail("Nenhuma tarefa ativa. Use --task <id> ou 'manus use <id>' primeiro.")
        return 1

    input_data = None
    if args.input_json:
        try:
            input_data = json.loads(args.input_json)
        except json.JSONDecodeError as e:
            print_fail(f"--input não é JSON válido: {e}")
            return 1

    with _client() as client:
        try:
            task_runner.confirm_action(client, task_id, args.event_id, input_data)
        except ManusAPIError as e:
            print_error("Erro", e.message)
            return 1
    print_success("Ação confirmada.")
    return 0


def _check_provisioning(client: ManusClient) -> bool:
    """Detects the ok:true-but-task-doesn't-exist provisioning bug: task.create
    succeeds with a task_id, but task.detail can't find it right after."""
    try:
        created = client.create_task("manus doctor: verificação de provisionamento (pode ignorar/apagar)")
    except ManusAPIError as e:
        print_fail(f"Provisionamento: task.create falhou ({e.message})")
        return False
    task_id = created["task_id"]
    request_id = created.get("request_id", "?")
    try:
        client.task_detail(task_id)
    except ManusAPIError as e:
        print_fail(
            f"Provisionamento: task.create respondeu ok:true (task_id={task_id}, "
            f"request_id={request_id}) mas task.detail não encontra a task logo em seguida "
            f"({e.message}). Isso é um bug do lado do servidor Manus, não do CLI — reporte ao "
            f"suporte com o request_id acima."
        )
        return False
    print_success(f"Provisionamento: task criada e confirmada (task_id={task_id})")
    try:
        client.delete_task(task_id)
    except ManusAPIError:
        pass
    return True


def cmd_doctor(argv: list[str]) -> int:
    import importlib.metadata

    parser = argparse.ArgumentParser(prog="manus doctor")
    parser.add_argument(
        "--check-provisioning",
        action="store_true",
        help="cria uma task de teste e confere se ela existe de verdade logo em seguida "
        "(gasta cota de task.create; detecta o bug de task.create ok:true sem provisionar a task)",
    )
    args = parser.parse_args(argv)

    ok = True

    try:
        version = importlib.metadata.version("manus-cli")
    except importlib.metadata.PackageNotFoundError:
        version = "dev (não instalado como pacote)"
    console.print(f"[muted]versão[/muted]  {version}")

    try:
        api_key = config.load_api_key()
    except ConfigError as e:
        print_fail(str(e))
        return 1

    key_source = "MANUS_API_KEY" if os.environ.get("MANUS_API_KEY") else "credentials.json"
    if not api_key:
        print_fail("API key: nenhuma configurada (rode: manus login)")
        ok = False
    else:
        print_success(f"API key: presente ({key_source}, {config.mask(api_key)})")
        with ManusClient(api_key) as client:
            try:
                client.validate_key()
                print_success("Conectividade com api.manus.ai: ok")
            except ManusAPIError as e:
                print_fail(f"Conectividade: key rejeitada ({e.message})")
                ok = False
            except Exception as e:  # noqa: BLE001 — diagnostic command: report any failure, don't crash.
                print_fail(f"Conectividade: falha de rede ({e})")
                ok = False
            else:
                if args.check_provisioning:
                    ok = _check_provisioning(client) and ok

    console.print(f"[muted]config[/muted]   {config.CONFIG_DIR}")
    try:
        last_task = config.load_last_task()
        aliases = config.load_aliases()
        console.print(f"[muted]última tarefa: {last_task or '(nenhuma)'}, apelidos: {len(aliases)}[/muted]")
    except ConfigError as e:
        print_fail(str(e))
        ok = False

    try:
        project_rc = config.load_project_rc()
        if project_rc:
            console.print(f"[muted].manusrc neste diretório: {project_rc}[/muted]")
    except ConfigError as e:
        print_fail(str(e))
        ok = False

    return 0 if ok else 1


def cmd_status(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="manus status")
    parser.add_argument("task_id", nargs="?", default=None)
    args = parser.parse_args(argv)

    task_id = args.task_id or config.load_last_task()
    if not task_id:
        print_fail("Nenhum task_id informado e nenhuma tarefa recente salva.")
        return 1
    with _client() as client:
        try:
            detail = client.task_detail(task_id)
        except ManusAPIError as e:
            print_error("Erro", e.message)
            return 1
    print_status(detail["task"])
    return 0


def cmd_result(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="manus result")
    parser.add_argument("task_id", nargs="?", default=None)
    args = parser.parse_args(argv)

    task_id = args.task_id or config.load_last_task()
    if not task_id:
        print_fail("Nenhum task_id informado e nenhuma tarefa recente salva.")
        return 1
    with _client() as client:
        try:
            data = client.list_messages(task_id, limit=5, order="desc")
        except ManusAPIError as e:
            print_error("Erro", e.message)
            return 1
    print_assistant(task_runner.last_assistant_message(data["messages"]))
    return 0


def cmd_stop(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="manus stop", description="Para uma tarefa em execução (task.stop)")
    parser.add_argument("task_id", nargs="?", default=None)
    args = parser.parse_args(argv)

    task_id = args.task_id or config.load_last_task()
    if not task_id:
        print_fail("Nenhum task_id informado e nenhuma tarefa recente salva.")
        return 1
    with _client() as client:
        try:
            client.stop_task(task_id)
        except ManusAPIError as e:
            print_error("Erro", e.message)
            return 1
    print_success(f"tarefa {task_id} parada (retomável com --continue/manus use)")
    return 0


def cmd_delete(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="manus delete", description="Apaga uma tarefa permanentemente (task.delete)"
    )
    parser.add_argument("task_id")
    parser.add_argument("--yes", action="store_true", help="não pede confirmação")
    args = parser.parse_args(argv)

    if not args.yes:
        answer = console.input(f"Apagar {args.task_id} permanentemente? Essa ação não pode ser desfeita. [y/N] ")
        if answer.strip().lower() not in ("y", "yes", "s", "sim"):
            print_fail("Cancelado.")
            return 1

    with _client() as client:
        try:
            client.delete_task(args.task_id)
        except ManusAPIError as e:
            print_error("Erro", e.message)
            return 1
    print_success(f"tarefa {args.task_id} apagada")
    return 0


def cmd_update(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="manus update", description="Atualiza título/visibilidade de uma tarefa")
    parser.add_argument("task_id")
    parser.add_argument("--title", default=None)
    parser.add_argument("--share", choices=SHARE_VISIBILITIES, default=None, dest="share_visibility")
    visibility = parser.add_mutually_exclusive_group()
    visibility.add_argument("--hide", action="store_true", help="esconde da lista de tarefas na webapp")
    visibility.add_argument("--show", action="store_true", help="mostra na lista de tarefas na webapp")
    args = parser.parse_args(argv)

    if args.title is None and args.share_visibility is None and not args.hide and not args.show:
        print_fail("Nada pra atualizar — use --title, --share, --hide ou --show.")
        return 1

    visible_in_task_list = True if args.show else (False if args.hide else None)
    with _client() as client:
        try:
            client.update_task(
                args.task_id,
                title=args.title,
                share_visibility=args.share_visibility,
                visible_in_task_list=visible_in_task_list,
            )
        except ManusAPIError as e:
            print_error("Erro", e.message)
            return 1
    print_success(f"tarefa {args.task_id} atualizada")
    return 0


def cmd_project(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="manus project")
    sub = parser.add_subparsers(dest="action", required=True)
    create_parser = sub.add_parser("create")
    create_parser.add_argument("name")
    create_parser.add_argument("--instruction", default=None, help="instrução aplicada a toda tarefa do projeto")
    sub.add_parser("list")
    args = parser.parse_args(argv)

    with _client() as client:
        if args.action == "create":
            try:
                resp = client.create_project(args.name, instruction=args.instruction)
            except ManusAPIError as e:
                print_error("Erro", e.message)
                return 1
            project = resp["project"]
            print_success(f"projeto \"{project['name']}\" criado ({project['id']})")
            return 0
        # action == "list"
        try:
            data = client.list_projects()
        except ManusAPIError as e:
            print_error("Erro", e.message)
            return 1
        print_projects(data.get("data", []))
    return 0


class _ConsoleApproval:
    def __init__(self, *, yes: bool, interactive: bool | None = None):
        self.yes = yes
        self.interactive = sys.stdin.isatty() if interactive is None else interactive

    def approve(self, tool: str, arguments: dict, reason: str) -> bool:
        if self.yes:
            return True
        preview = _approval_preview(tool, arguments)
        if not self.interactive:
            print_warning(f"ação exige confirmação e stdin não é interativo: {preview} ({reason})")
            return False
        err_console.print(f"[warning]Aprovação necessária:[/warning] {preview}")
        err_console.print(f"[muted]{reason}[/muted]")
        answer = err_console.input("Permitir? [y/N] ")
        return answer.strip().lower() in {"y", "yes", "s", "sim"}


def _approval_preview(tool: str, arguments: dict) -> str:
    if tool == "run_command":
        argv = arguments.get("argv")
        if isinstance(argv, list):
            return f"run_command {json.dumps(argv, ensure_ascii=False)}"
    path = arguments.get("path")
    return f"{tool} {path}" if isinstance(path, str) else tool


def _print_agent_step(step: AgentStep) -> None:
    marker = "success" if step.ok else "warning"
    status = "ok" if step.ok else "falhou"
    console.print(
        f"[{marker}]{step.number}. {escape(step.tool)}[/{marker}] {status} "
        f"[muted]— {escape(step.summary)}[/muted]"
    )


def cmd_code(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="manus code",
        description="Agente local de programação: lê, edita e valida código dentro do workspace",
    )
    parser.add_argument("prompt", nargs="*")
    parser.add_argument("--root", default=".", help="raiz confinada do workspace (padrão: diretório atual)")
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--command-timeout", type=float, default=120)
    parser.add_argument("--timeout", type=float, default=300, help="timeout de cada turno Manus")
    parser.add_argument(
        "--approval",
        choices=[mode.value for mode in ApprovalMode],
        default=ApprovalMode.BALANCED.value,
    )
    parser.add_argument("--yes", action="store_true", help="aprova ações confirmáveis; bloqueios duros continuam ativos")
    parser.add_argument("--json", dest="json_output", action="store_true")
    parser.add_argument("--agent-profile", choices=AGENT_PROFILES, default=None)
    args = parser.parse_args(argv)

    if not 1 <= args.max_steps <= 100:
        print_fail("--max-steps precisa estar entre 1 e 100")
        return 1
    if args.command_timeout <= 0 or args.timeout <= 0:
        print_fail("timeouts precisam ser positivos")
        return 1

    objective = " ".join(args.prompt).strip()
    if not objective and not sys.stdin.isatty():
        objective = sys.stdin.read().strip()
    if not objective and sys.stdin.isatty():
        # err_console, not console: --json promises stdout is exclusively JSON, and
        # this prompt must not leak onto it if someone runs `manus code --json`
        # interactively without a prompt argument.
        objective = err_console.input("Objetivo de programação: ").strip()
    if not objective:
        print_fail("Informe o objetivo: manus code \"corrija os testes\"")
        return 1

    try:
        tools = WorkspaceTools(
            Path(args.root),
            command_timeout=args.command_timeout,
        )
    except (OSError, ValueError) as exc:
        print_fail(str(exc))
        return 1

    client = _client()
    try:
        agent = CodingAgent(
            ManusCodingAdapter(client),
            tools,
            PolicyEngine(ApprovalMode(args.approval)),
            _ConsoleApproval(yes=args.yes),
            max_steps=args.max_steps,
            turn_timeout=args.timeout,
            agent_profile=args.agent_profile,
            on_step=None if args.json_output else _print_agent_step,
        )
        try:
            result = agent.run(objective)
        except KeyboardInterrupt:
            if args.json_output:
                print(json.dumps({"success": False, "error": "cancelado pelo usuário"}, ensure_ascii=False))
            else:
                print_warning("cancelado pelo usuário")
            return 130
    finally:
        client.close()

    if result.task_id:
        config.save_last_task(result.task_id)
    if args.json_output:
        print(json.dumps(result.to_dict(), ensure_ascii=False))
        return 0 if result.success else 1

    if result.success:
        print_success("Agente concluiu")
        print_assistant(result.final_message)
    else:
        print_fail(result.error or result.final_message)
    if result.changed_files:
        console.print(f"[muted]arquivos alterados: {', '.join(result.changed_files)}[/muted]")
    if not result.validated:
        print_warning("resultado não validado por comando de teste/build bem-sucedido")
    return 0 if result.success else 1


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


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
            print_warning(f"anexo com nome suspeito ignorado: {att.get('filename')!r}")
            continue
        try:
            actual_path = client.download_file(url, dest)
        except ManusAPIError as e:
            print_warning(f"falha ao baixar anexo {safe_name!r}: {e.message}")
            continue
        err_console.print(f"[muted]↓ salvo em {actual_path}[/muted]")
        saved.append(str(actual_path))
    return saved


# ---------------------------------------------------------------------------
# Turn execution (shared by one-shot prompts, --file/--project, and the REPL)
# ---------------------------------------------------------------------------


def _run_turn(
    client: ManusClient,
    task_id: str | None,
    content,
    timeout: float,
    connectors: list[str] | None = None,
    json_output: bool = False,
    project_id: str | None = None,
    agent_profile: str | None = None,
) -> tuple[str | None, int]:
    """Runs one create/send + poll cycle. Returns (task_id, exit_code).

    Exit codes: 0 = stopped (success), 1 = error or network failure,
    2 = waiting (needs a reply or manus confirm) — never silently 0.

    project_id/agent_profile only take effect when a task is being *created*
    (task_id is None going in) — the API ignores them on task.sendMessage.
    """

    def _make_on_event(live):
        def _cb(msg):
            label = progress_label(msg)
            if label:
                live.update(f"[accent]{label}[/accent]")

        return _cb

    create_kwargs = {"project_id": project_id, "agent_profile": agent_profile}
    try:
        if json_output:
            outcome = task_runner.run_turn(client, task_id, content, timeout, connectors=connectors, **create_kwargs)
        else:
            with console.status("[accent]Manus trabalhando...[/accent]", spinner="dots") as live:
                outcome = task_runner.run_turn(
                    client,
                    task_id,
                    content,
                    timeout,
                    connectors=connectors,
                    on_event=_make_on_event(live),
                    **create_kwargs,
                )
    except task_runner.TaskTimeoutError as e:
        print_fail(str(e))
        return task_id, 1

    # The task genuinely exists once we have any outcome (stopped/waiting/error all
    # imply the server responded with a real status_update) — safe to persist.
    config.save_last_task(outcome.task_id)

    attachments_saved = (
        _download_attachments(client, outcome.task_id, outcome.attachments) if outcome.attachments else []
    )

    if json_output:
        print(
            json.dumps(
                {
                    "task_id": outcome.task_id,
                    "status": outcome.status,
                    "content": outcome.content,
                    "attachments": attachments_saved,
                    "status_detail": outcome.status_detail if outcome.status == "waiting" else None,
                    "error_detail": outcome.error_detail if outcome.status == "error" else None,
                },
                ensure_ascii=False,
            )
        )
        exit_code = {"stopped": 0, "waiting": 2, "error": 1}[outcome.status]
        return outcome.task_id, exit_code

    if outcome.status == "stopped":
        print_success("Tarefa stopped")
        print_assistant(outcome.content)
        console.print()
        return outcome.task_id, 0
    if outcome.status == "waiting":
        print_waiting(outcome.status_detail)
        if outcome.content:
            print_assistant(outcome.content)
        console.print()
        return outcome.task_id, 2
    print_task_error(outcome.error_detail)
    console.print()
    return outcome.task_id, 1


# ---------------------------------------------------------------------------
# @-mentions and /slash commands (REPL only)
# ---------------------------------------------------------------------------

_MENTION_RE = re.compile(r"@(\S+)")
_MENTION_TRAILING_PUNCT = ".,;:!?)\"'"


def _extract_mentions(text: str, allow_secret: bool = False) -> list[Path]:
    paths = []
    for match in _MENTION_RE.finditer(text):
        candidate = Path(match.group(1).rstrip(_MENTION_TRAILING_PUNCT))
        if not candidate.is_file():
            continue
        reason = files.check_single_file(candidate, allow_secret=allow_secret)
        if reason:
            print_warning(f"@{candidate} ignorado: {reason}")
            continue
        paths.append(candidate)
    return paths


def _upload_paths(client: ManusClient, paths: list[Path]) -> list[dict]:
    content = []
    for path in paths:
        file_id = client.upload_file(path)
        content.append({"type": "file", "file_id": file_id, "filename": path.name})
    return content


_SLASH_COMMANDS: list[tuple[str, str]] = [
    ("status", ""),
    ("use", "<id>"),
    ("history", ""),
    ("open", "[id]"),
    ("confirm", "<event_id> [json]"),
    ("stop", ""),
    ("help", ""),
    ("exit", ""),
]
_SLASH_HELP = "  ".join(f"/{name} {args}".rstrip() for name, args in _SLASH_COMMANDS)

_REPL_MAX_MENTION_FILES = 2000  # ponytail: teto simples pra árvore de arquivos gigante, sem cache incremental


class _ReplCompleter(Completer):
    """Dropdown de sugestões: /comandos ao digitar '/', arquivos do projeto ao digitar '@'."""

    def __init__(self, root: Path):
        self._root = root
        self._gitignore: files.GitignoreMatcher | None = None
        self._file_cache: list[str] | None = None

    def _project_files(self) -> list[str]:
        if self._file_cache is not None:
            return self._file_cache
        if self._gitignore is None:
            self._gitignore = files.GitignoreMatcher.load(self._root)
        found = []
        for path in sorted(self._root.rglob("*"), key=lambda item: item.as_posix()):
            # A completion is only a convenience, but it must not advertise
            # paths the upload policy would reject (especially secrets/symlinks).
            if path.is_symlink() or not path.is_file():
                continue
            rel = path.relative_to(self._root)
            if any(part in files.IGNORED_DIR_NAMES for part in rel.parts):
                continue
            if self._gitignore.matches(rel):
                continue
            if files.looks_like_secret(rel) or files.is_rejected_type(path):
                continue
            found.append(rel.as_posix())
            if len(found) >= _REPL_MAX_MENTION_FILES:
                break
        self._file_cache = found
        return self._file_cache

    def get_completions(self, document, complete_event):
        before = document.text_before_cursor
        word = document.get_word_before_cursor(WORD=True)

        if word.startswith("/") and not before[: -len(word)].strip():
            prefix = word[1:]
            for name, args in _SLASH_COMMANDS:
                if name.startswith(prefix):
                    display = f"/{name}" + (f" {args}" if args else "")
                    yield Completion(f"/{name}", start_position=-len(word), display=display)
            return

        if word.startswith("@"):
            prefix = word[1:]
            matches = [p for p in self._project_files() if p.startswith(prefix)]
            for rel in sorted(matches)[:50]:
                yield Completion(f"@{rel}", start_position=-len(word))


def _build_repl_session(root: Path) -> PromptSession | None:
    if not sys.stdin.isatty():
        return None
    style = Style.from_dict(
        {
            "prompt": "bold fg:cyan",
            "completion-menu.completion": "bg:default fg:cyan",
            "completion-menu.completion.current": "bg:cyan fg:black bold",
        }
    )
    return PromptSession(
        [("class:prompt", f"{PROMPT} ")],
        completer=_ReplCompleter(root),
        complete_while_typing=True,
        style=style,
    )


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

    if cmd == "stop":
        if not task_id:
            print_fail("Nenhuma tarefa ativa ainda.")
            return task_id, False
        try:
            client.stop_task(task_id)
        except ManusAPIError as e:
            print_error("Erro", e.message)
            return task_id, False
        print_success(f"tarefa {task_id} parada (retomável normalmente)")
        return task_id, False

    if cmd == "confirm":
        if not task_id:
            print_fail("Nenhuma tarefa ativa ainda.")
            return task_id, False
        sub_parts = arg.split(maxsplit=1)
        if not sub_parts:
            print_fail("Uso: /confirm <event_id> [json]")
            return task_id, False
        event_id = sub_parts[0]
        input_data = None
        if len(sub_parts) > 1:
            try:
                input_data = json.loads(sub_parts[1])
            except json.JSONDecodeError as e:
                print_fail(f"JSON inválido: {e}")
                return task_id, False
        try:
            task_runner.confirm_action(client, task_id, event_id, input_data)
        except ManusAPIError as e:
            print_error("Erro", e.message)
            return task_id, False
        print_success("Ação confirmada.")
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


# ---------------------------------------------------------------------------
# Default command: one-shot prompt / --file / --project / bare REPL
# ---------------------------------------------------------------------------


def _build_chat_parser() -> argparse.ArgumentParser:
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
    parser.add_argument("--dry-run", dest="dry_run", action="store_true", help="só mostra o que seria enviado")
    parser.add_argument("--no-gitignore", dest="no_gitignore", action="store_true")
    parser.add_argument(
        "--in-project", dest="in_project", default=None, metavar="NOME-OU-ID",
        help="associa a tarefa (se criada agora) a um Manus Project — aplica a instrução dele automaticamente",
    )
    parser.add_argument(
        "--agent-profile", dest="agent_profile", choices=AGENT_PROFILES, default=None,
        help="tier de capacidade do agente pra tarefa criada agora (ignorado em --continue/--task)",
    )
    return parser


def cmd_chat(argv: list[str]) -> int:
    parser = _build_chat_parser()
    args = parser.parse_args(argv)

    prompt_text = " ".join(args.prompt) if args.prompt else None

    stdin_data = None
    if not sys.stdin.isatty():
        stdin_data = sys.stdin.read().strip() or None
    if stdin_data:
        prompt_text = f"{stdin_data}\n\n{prompt_text}" if prompt_text else stdin_data

    # --project --dry-run needs no API key/client at all — it's a pure local preview.
    if args.dry_run and args.project:
        root = Path(args.project)
        if not root.is_dir():
            print_fail(f"Diretório não encontrado: {root}")
            return 1
        result = files.select_project_files(root, allow_secret=args.allow_secret, respect_gitignore=not args.no_gitignore)
        print_dry_run(result.files, result.skipped, result.total_bytes)
        return 0

    try:
        project_rc = config.load_project_rc()
    except ConfigError as e:
        print_fail(str(e))
        return 1

    client = _client(timeout=args.timeout + 10)
    try:
        raw_connectors = args.connectors or project_rc.get("connector_names") or project_rc.get("connectors")
        try:
            connectors = _resolve_connectors(client, raw_connectors)
            manus_project_id = _resolve_project(client, args.in_project)
        except ManusAPIError as e:
            print_error("Erro", e.message)
            return 1

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
                reason = files.check_single_file(file_path, allow_secret=args.allow_secret)
                if reason:
                    print_fail(f"Recusado: {file_path} ({reason})")
                    return 1
                content = _upload_paths(client, [file_path])
                if prompt_text:
                    content.append({"type": "text", "text": prompt_text})
                _, exit_code = _run_turn(
                    client, task_id, content, args.timeout, connectors, args.json_output,
                    project_id=manus_project_id, agent_profile=args.agent_profile,
                )
                return exit_code

            if args.project:
                root = Path(args.project)
                if not root.is_dir():
                    print_fail(f"Diretório não encontrado: {root}")
                    return 1
                result = files.select_project_files(
                    root, allow_secret=args.allow_secret, respect_gitignore=not args.no_gitignore
                )
                for s in result.skipped:
                    print_warning(f"pulando {s.relative_path} ({s.reason})")
                if not args.json_output:
                    err_console.print(f"[muted]subindo {len(result.files)} arquivo(s) de {root}...[/muted]")
                upload_result = files.upload_many(client, result.files)
                content = upload_result.content
                for f in upload_result.failed:
                    print_warning(f"{f.relative_path}: {f.reason}")
                if upload_result.uploaded:
                    content.append({
                        "type": "text",
                        "text": files.build_manifest_text(upload_result.uploaded),
                    })
                if prompt_text:
                    content.append({"type": "text", "text": prompt_text})
                if not content:
                    print_fail("Nada para enviar (nenhum arquivo passou nos filtros e nenhum prompt foi dado).")
                    return 1
                _, exit_code = _run_turn(
                    client, task_id, content, args.timeout, connectors, args.json_output,
                    project_id=manus_project_id, agent_profile=args.agent_profile,
                )
                return exit_code

            if prompt_text:
                _, exit_code = _run_turn(
                    client, task_id, prompt_text, args.timeout, connectors, args.json_output,
                    project_id=manus_project_id, agent_profile=args.agent_profile,
                )
                return exit_code

            # REPL
            print_header(os.getcwd())
            repl_session = _build_repl_session(Path.cwd())
            while True:
                try:
                    if repl_session is not None:
                        line = repl_session.prompt().strip()
                    else:
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
                mentioned = _extract_mentions(line, allow_secret=args.allow_secret)
                turn_content: str | list[dict]
                if mentioned:
                    turn_content = _upload_paths(client, mentioned)
                    turn_content.append({"type": "text", "text": line})
                else:
                    turn_content = line
                task_id, _ = _run_turn(
                    client, task_id, turn_content, args.timeout, connectors, args.json_output,
                    project_id=manus_project_id, agent_profile=args.agent_profile,
                )
        except ManusAPIError as e:
            print_error("Erro", e.message)
            return 1
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_SUBCOMMANDS = {
    "login": cmd_login,
    "use": cmd_use,
    "history": cmd_history,
    "open": cmd_open,
    "alias": cmd_alias,
    "connector": cmd_connector,
    "confirm": cmd_confirm,
    "doctor": cmd_doctor,
    "status": cmd_status,
    "result": cmd_result,
    "stop": cmd_stop,
    "delete": cmd_delete,
    "update": cmd_update,
    "project": cmd_project,
    "code": cmd_code,
}


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] in _SUBCOMMANDS:
        sys.exit(_SUBCOMMANDS[argv[0]](argv[1:]))
    sys.exit(cmd_chat(argv))


if __name__ == "__main__":
    main()
