from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .files import looks_like_secret


class ApprovalMode(str, Enum):
    SUPERVISED = "supervised"
    BALANCED = "balanced"
    AUTONOMOUS = "autonomous"


class Decision(str, Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


@dataclass(frozen=True)
class PolicyResult:
    decision: Decision
    reason: str
    verification: bool = False


_READ_TOOLS = {"list_files", "read_file", "search", "git_diff"}
_WRITE_TOOLS = {"write_file", "replace_text"}
_ALL_TOOLS = _READ_TOOLS | _WRITE_TOOLS | {"run_command"}
_PATH_TOOLS = {"list_files", "read_file", "search", "write_file", "replace_text"}

_SHELLS = {
    "sh", "bash", "zsh", "fish", "dash", "cmd", "cmd.exe",
    "powershell", "powershell.exe", "pwsh", "pwsh.exe",
}
_HARD_DENY_EXECUTABLES = {
    "sudo", "su", "doas", "rm", "rmdir", "del", "erase", "format",
    "shutdown", "reboot", "halt", "mkfs", "dd",
}
_NETWORK_EXECUTABLES = {"curl", "wget", "ssh", "scp", "sftp", "rsync"}
_DIRECT_SAFE_EXECUTABLES = {
    "pytest", "ruff", "mypy", "pyright", "eslint", "tsc", "vitest", "jest",
    "tox", "nox", "phpunit", "rubocop", "rspec",
}
_PYTHON_SAFE_MODULES = {"unittest", "pytest", "ruff", "mypy", "build", "compileall"}
_PACKAGE_SAFE_SCRIPTS = {"test", "lint", "build", "check", "typecheck", "type-check"}
_GIT_SAFE = {"status", "diff", "log", "show", "ls-files", "rev-parse"}
_GIT_DENY = {"push", "reset", "clean", "checkout", "restore", "switch", "commit", "merge", "rebase"}
_PUBLISH_COMMANDS = {
    ("npm", "publish"), ("npm", "unpublish"), ("pnpm", "publish"),
    ("yarn", "publish"), ("cargo", "publish"), ("python", "-m", "twine"),
    ("python3", "-m", "twine"), ("twine", "upload"),
}


def _safe_script(name: str) -> bool:
    return name in _PACKAGE_SAFE_SCRIPTS or any(name.startswith(prefix + ":") for prefix in _PACKAGE_SAFE_SCRIPTS)


def _executable_name(raw: str) -> str:
    name = Path(raw).name.lower()
    for suffix in (".exe", ".cmd", ".bat"):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name


class PolicyEngine:
    """Pure action classifier. Execution and user interaction live elsewhere."""

    def __init__(self, mode: ApprovalMode = ApprovalMode.BALANCED):
        self.mode = mode

    def evaluate(self, tool: str, arguments: dict) -> PolicyResult:
        if tool not in _ALL_TOOLS:
            return PolicyResult(Decision.DENY, f"ferramenta desconhecida: {tool}")
        if not isinstance(arguments, dict):
            return PolicyResult(Decision.DENY, "argumentos precisam ser um objeto JSON")

        if tool in _PATH_TOOLS:
            path_result = self._check_relative_path(arguments.get("path", "."))
            if path_result:
                return path_result

        if tool in _READ_TOOLS:
            return PolicyResult(Decision.ALLOW, "leitura confinada ao workspace")

        if tool in _WRITE_TOOLS:
            if self.mode == ApprovalMode.SUPERVISED:
                return PolicyResult(Decision.CONFIRM, "modo supervisionado confirma toda escrita")
            return PolicyResult(Decision.ALLOW, "escrita confinada ao workspace")

        command_result = self._classify_command(arguments.get("argv"))
        if command_result.decision == Decision.DENY:
            return command_result
        if self.mode == ApprovalMode.SUPERVISED:
            return PolicyResult(
                Decision.CONFIRM,
                "modo supervisionado confirma todo comando",
                verification=command_result.verification,
            )
        if command_result.decision == Decision.CONFIRM and self.mode == ApprovalMode.AUTONOMOUS:
            return PolicyResult(
                Decision.ALLOW,
                f"modo autônomo: {command_result.reason}",
                verification=command_result.verification,
            )
        return command_result

    @staticmethod
    def _check_relative_path(raw_path: object) -> PolicyResult | None:
        if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
            return PolicyResult(Decision.DENY, "caminho inválido")
        path = Path(raw_path)
        if path.is_absolute() or any(part == ".." for part in path.parts):
            return PolicyResult(Decision.DENY, "caminho precisa permanecer dentro do workspace")
        lowered = {part.lower() for part in path.parts}
        if ".git" in lowered:
            return PolicyResult(Decision.DENY, "acesso direto a .git é bloqueado")
        if looks_like_secret(path):
            return PolicyResult(Decision.DENY, "arquivo parece conter credencial ou segredo")
        return None

    @staticmethod
    def _classify_command(raw_argv: object) -> PolicyResult:
        if not isinstance(raw_argv, list) or not raw_argv or not all(isinstance(arg, str) for arg in raw_argv):
            return PolicyResult(Decision.DENY, "run_command exige argv como lista não vazia de strings")
        if any("\x00" in arg for arg in raw_argv):
            return PolicyResult(Decision.DENY, "argumento contém byte NUL")

        executable_path = Path(raw_argv[0])
        if executable_path.is_absolute() or any(part == ".." for part in executable_path.parts):
            return PolicyResult(Decision.DENY, "caminho do executável precisa permanecer no workspace")
        executable = _executable_name(raw_argv[0])
        if executable in _SHELLS:
            return PolicyResult(Decision.DENY, "shell indireto é bloqueado; use argv direto")
        if executable in _HARD_DENY_EXECUTABLES:
            return PolicyResult(Decision.DENY, f"comando destrutivo bloqueado: {executable}")

        lowered = tuple(arg.lower() for arg in raw_argv)
        if any(lowered[:len(prefix)] == prefix for prefix in _PUBLISH_COMMANDS):
            return PolicyResult(Decision.DENY, "publicação de pacote é bloqueada")

        args = list(lowered[1:])
        if executable in {"npm", "pnpm", "yarn", "cargo"} and args and args[0] in {"publish", "unpublish"}:
            return PolicyResult(Decision.DENY, "publicação de pacote é bloqueada")
        if executable in {"python", "python3", "py", "node", "ruby", "perl", "php"}:
            eval_flags = {"-c", "-e", "--eval"}
            if any(arg in eval_flags for arg in args):
                return PolicyResult(Decision.DENY, "avaliação de código inline é bloqueada")
            if executable in {"python", "python3", "py"} and len(args) >= 2 and args[0] == "-m":
                if args[1] == "twine":
                    return PolicyResult(Decision.DENY, "publicação de pacote é bloqueada")
                if args[1] in _PYTHON_SAFE_MODULES:
                    return PolicyResult(Decision.ALLOW, "verificação Python conhecida", verification=True)
                if args[1] in {"pip", "ensurepip"}:
                    return PolicyResult(Decision.CONFIRM, "instalação de dependências")
            if executable == "node" and args and args[0] == "--test":
                return PolicyResult(Decision.ALLOW, "suite de testes Node conhecida", verification=True)
            if executable == "php" and len(args) >= 2 and args[0] == "artisan" and args[1] == "test":
                return PolicyResult(Decision.ALLOW, "suite de testes PHP conhecida", verification=True)
            return PolicyResult(Decision.CONFIRM, "execução de código do workspace")

        if executable == "git":
            subcommand = args[0] if args else ""
            if subcommand in _GIT_SAFE:
                return PolicyResult(Decision.ALLOW, "inspeção Git somente leitura")
            if subcommand in _GIT_DENY:
                return PolicyResult(Decision.DENY, f"mutação Git bloqueada: git {subcommand}")
            return PolicyResult(Decision.CONFIRM, "comando Git fora da lista somente leitura")

        if executable in _DIRECT_SAFE_EXECUTABLES:
            return PolicyResult(Decision.ALLOW, "verificação conhecida", verification=True)

        if executable in {"npm", "pnpm", "yarn", "bun"}:
            subcommand = args[0] if args else ""
            if subcommand in {"install", "i", "add", "ci", "update", "remove", "uninstall"}:
                return PolicyResult(Decision.CONFIRM, "instalação ou alteração de dependências")
            if subcommand == "test":
                return PolicyResult(Decision.ALLOW, "suite de testes do projeto", verification=True)
            if subcommand in {"run", "run-script"} and len(args) > 1 and _safe_script(args[1]):
                return PolicyResult(Decision.ALLOW, "script de verificação conhecido", verification=True)
            if subcommand in {"exec", "dlx", "x"}:
                return PolicyResult(Decision.CONFIRM, "executor pode baixar ou executar pacote")
            return PolicyResult(Decision.CONFIRM, "comando de gerenciador de pacotes")

        if executable in {"npx", "pip", "pip3", "uv", "poetry", "composer"}:
            return PolicyResult(Decision.CONFIRM, "comando pode instalar ou executar dependências")
        if executable in _NETWORK_EXECUTABLES:
            return PolicyResult(Decision.CONFIRM, "comando usa rede")

        if executable == "go" and args and args[0] in {"test", "vet", "build"}:
            return PolicyResult(Decision.ALLOW, "verificação Go conhecida", verification=True)
        if executable == "cargo" and args and args[0] in {"test", "check", "clippy", "build", "fmt"}:
            return PolicyResult(Decision.ALLOW, "verificação Rust conhecida", verification=True)
        if executable == "dotnet" and args and args[0] in {"test", "build", "format"}:
            return PolicyResult(Decision.ALLOW, "verificação .NET conhecida", verification=True)
        if executable == "make" and args and args[0] in _PACKAGE_SAFE_SCRIPTS:
            return PolicyResult(Decision.ALLOW, "alvo de verificação conhecido", verification=True)
        if executable in {"mvn", "mvnw", "gradle", "gradlew"}:
            goals = {arg for arg in args if not arg.startswith("-")}
            if goals & {"test", "verify", "package", "build", "check"}:
                return PolicyResult(Decision.ALLOW, "verificação JVM conhecida", verification=True)
            return PolicyResult(Decision.CONFIRM, "comando de build JVM")
        if executable == "bundle":
            if len(args) >= 2 and args[0] == "exec" and args[1] in {"rspec", "rubocop"}:
                return PolicyResult(Decision.ALLOW, "verificação Ruby conhecida", verification=True)
            return PolicyResult(Decision.CONFIRM, "comando Bundler")
        if executable == "swift" and args and args[0] in {"test", "build"}:
            return PolicyResult(Decision.ALLOW, "verificação Swift conhecida", verification=True)
        if executable == "mix" and args and args[0] in {"test", "compile", "format"}:
            return PolicyResult(Decision.ALLOW, "verificação Elixir conhecida", verification=True)

        return PolicyResult(Decision.DENY, f"executável fora da política: {executable}")
