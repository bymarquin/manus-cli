from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import tempfile
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

from .files import IGNORED_DIR_NAMES, GitignoreMatcher, looks_like_secret

MAX_READ_BYTES = 256 * 1024
MAX_WRITE_BYTES = 1024 * 1024
MAX_SEARCH_FILE_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
MAX_LIST_RESULTS = 500
MAX_SEARCH_RESULTS = 100

_ALLOWED_ARGUMENTS = {
    "list_files": {"path", "max_results"},
    "read_file": {"path", "start_line", "max_lines"},
    "search": {"path", "query", "regex", "max_results"},
    "write_file": {"path", "content"},
    "replace_text": {"path", "old", "new", "expected_occurrences"},
    "git_diff": {"staged"},
    "run_command": {"argv", "cwd", "timeout"},
}


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    content: str
    metadata: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)


class CommandRunner(Protocol):
    def run(self, argv: list[str], cwd: Path, timeout: float) -> ToolResult: ...


class SubprocessCommandRunner:
    def __init__(self, max_output_bytes: int = MAX_OUTPUT_BYTES):
        self.max_output_bytes = max_output_bytes

    def run(self, argv: list[str], cwd: Path, timeout: float) -> ToolResult:
        env = _sanitized_environment(cwd)
        started = time.monotonic()
        kwargs: dict = {
            "cwd": str(cwd),
            "env": env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": False,
            "shell": False,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True
        try:
            process = subprocess.Popen(argv, **kwargs)
        except (OSError, ValueError) as exc:
            return ToolResult(False, f"falha ao iniciar comando: {exc}")

        try:
            stdout_raw, stderr_raw = process.communicate(timeout=timeout)
        except KeyboardInterrupt:
            _terminate_process(process)
            process.communicate()
            raise
        except subprocess.TimeoutExpired:
            _terminate_process(process)
            stdout_raw, stderr_raw = process.communicate()
            duration = round(time.monotonic() - started, 3)
            stdout, stdout_truncated = _decode_limited(stdout_raw, self.max_output_bytes)
            stderr, stderr_truncated = _decode_limited(stderr_raw, self.max_output_bytes)
            return ToolResult(
                False,
                "comando excedeu timeout",
                {
                    "argv": argv, "exit_code": None, "stdout": stdout, "stderr": stderr,
                    "duration_seconds": duration,
                    "truncated": stdout_truncated or stderr_truncated,
                    "timed_out": True,
                },
            )

        duration = round(time.monotonic() - started, 3)
        stdout, stdout_truncated = _decode_limited(stdout_raw, self.max_output_bytes)
        stderr, stderr_truncated = _decode_limited(stderr_raw, self.max_output_bytes)
        return ToolResult(
            process.returncode == 0,
            "comando concluído" if process.returncode == 0 else f"comando saiu com código {process.returncode}",
            {
                "argv": argv, "exit_code": process.returncode, "stdout": stdout, "stderr": stderr,
                "duration_seconds": duration,
                "truncated": stdout_truncated or stderr_truncated,
                "timed_out": False,
            },
        )


def _sanitized_environment(cwd: Path) -> dict[str, str]:
    sensitive_fragments = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "AUTH")
    env = {
        key: value for key, value in os.environ.items()
        if not any(fragment in key.upper() for fragment in sensitive_fragments)
    }
    path_entries = []
    for entry in env.get("PATH", "").split(os.pathsep):
        candidate = Path(entry)
        if not entry or not candidate.is_absolute():
            continue
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved == cwd or cwd in resolved.parents:
            continue
        path_entries.append(str(resolved))
    env["PATH"] = os.pathsep.join(path_entries)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _terminate_process(process: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        with suppress(OSError):
            process.kill()


def _decode_limited(raw: bytes, limit: int) -> tuple[str, bool]:
    truncated = len(raw) > limit
    selected = raw[:limit]
    return selected.decode("utf-8", errors="replace"), truncated


class WorkspaceTools:
    def __init__(self, root: Path, command_runner: CommandRunner | None = None, command_timeout: float = 120):
        resolved = root.expanduser().resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError(f"workspace não é diretório: {resolved}")
        if resolved.parent == resolved:
            raise ValueError("raiz do sistema de arquivos não pode ser usada como workspace")
        self.root = resolved
        self.command_runner = command_runner or SubprocessCommandRunner()
        self.command_timeout = command_timeout
        self.gitignore = GitignoreMatcher.load(resolved)

    def execute(self, tool: str, arguments: dict) -> ToolResult:
        if not isinstance(arguments, dict):
            return ToolResult(False, "argumentos precisam ser um objeto JSON")
        handlers = {
            "list_files": self._list_files,
            "read_file": self._read_file,
            "search": self._search,
            "write_file": self._write_file,
            "replace_text": self._replace_text,
            "git_diff": self._git_diff,
            "run_command": self._run_command,
        }
        handler = handlers.get(tool)
        if handler is None:
            return ToolResult(False, f"ferramenta desconhecida: {tool}")
        unexpected = set(arguments) - _ALLOWED_ARGUMENTS[tool]
        if unexpected:
            return ToolResult(False, f"argumentos inesperados: {', '.join(sorted(unexpected))}")
        try:
            return handler(arguments)
        except (OSError, UnicodeError, ValueError, re.error) as exc:
            return ToolResult(False, f"{tool} falhou: {exc}")

    def _resolve(self, raw_path: object, *, allow_missing: bool = False) -> tuple[Path, Path]:
        if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
            raise ValueError("caminho inválido")
        rel = Path(raw_path)
        if rel.is_absolute() or any(part == ".." for part in rel.parts):
            raise ValueError("caminho precisa ser relativo ao workspace")
        if ".git" in {part.lower() for part in rel.parts}:
            raise ValueError("acesso direto a .git é bloqueado")
        if looks_like_secret(rel):
            raise ValueError("arquivo parece conter credencial ou segredo")
        resolved = (self.root / rel).resolve(strict=not allow_missing)
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError("symlink ou caminho escapou do workspace")
        return resolved, rel

    def _iter_files(self, base: Path):
        for path in sorted(base.rglob("*"), key=lambda item: item.as_posix()):
            try:
                rel = path.relative_to(self.root)
            except ValueError:
                continue
            if any(part in IGNORED_DIR_NAMES for part in rel.parts):
                continue
            if path.is_symlink():
                continue
            if not path.is_file() or looks_like_secret(rel) or self.gitignore.matches(rel):
                continue
            yield path, rel

    def _list_files(self, arguments: dict) -> ToolResult:
        base, _ = self._resolve(arguments.get("path", "."))
        if not base.is_dir():
            return ToolResult(False, "path não é diretório")
        requested = arguments.get("max_results", MAX_LIST_RESULTS)
        limit = _bounded_int(requested, 1, MAX_LIST_RESULTS, "max_results")
        paths: list[str] = []
        truncated = False
        for _, rel in self._iter_files(base):
            if len(paths) >= limit:
                truncated = True
                break
            paths.append(rel.as_posix())
        return ToolResult(True, "\n".join(paths), {"count": len(paths), "truncated": truncated})

    def _read_file(self, arguments: dict) -> ToolResult:
        path, rel = self._resolve(arguments.get("path"))
        if not path.is_file():
            return ToolResult(False, "path não é arquivo")
        size = path.stat().st_size
        if size > MAX_READ_BYTES:
            return ToolResult(False, f"arquivo excede limite de leitura ({MAX_READ_BYTES} bytes)")
        raw = path.read_bytes()
        if b"\x00" in raw:
            return ToolResult(False, "arquivo binário não pode ser lido")
        text = raw.decode("utf-8")
        lines = text.splitlines()
        start = _bounded_int(arguments.get("start_line", 1), 1, max(1, len(lines) + 1), "start_line")
        max_lines = _bounded_int(arguments.get("max_lines", 200), 1, 500, "max_lines")
        selected = lines[start - 1:start - 1 + max_lines]
        numbered = "\n".join(f"{number}: {line}" for number, line in enumerate(selected, start=start))
        return ToolResult(
            True, numbered,
            {"path": rel.as_posix(), "start_line": start, "returned_lines": len(selected),
             "total_lines": len(lines), "truncated": start - 1 + len(selected) < len(lines)},
        )

    def _search(self, arguments: dict) -> ToolResult:
        query = arguments.get("query")
        if not isinstance(query, str) or not query:
            return ToolResult(False, "query precisa ser string não vazia")
        base, _ = self._resolve(arguments.get("path", "."))
        if not base.is_dir():
            return ToolResult(False, "path não é diretório")
        use_regex = arguments.get("regex", False)
        if not isinstance(use_regex, bool):
            return ToolResult(False, "regex precisa ser boolean")
        matcher = re.compile(query) if use_regex else None
        requested = arguments.get("max_results", MAX_SEARCH_RESULTS)
        limit = _bounded_int(requested, 1, MAX_SEARCH_RESULTS, "max_results")
        matches: list[str] = []
        truncated = False
        for path, rel in self._iter_files(base):
            if path.stat().st_size > MAX_SEARCH_FILE_BYTES:
                continue
            raw = path.read_bytes()
            if b"\x00" in raw:
                continue
            for line_number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), start=1):
                found = bool(matcher.search(line)) if matcher else query in line
                if not found:
                    continue
                if len(matches) >= limit:
                    truncated = True
                    break
                preview = line if len(line) <= 300 else line[:297] + "..."
                matches.append(f"{rel.as_posix()}:{line_number}:{preview}")
            if truncated:
                break
        return ToolResult(True, "\n".join(matches), {"count": len(matches), "truncated": truncated})

    def _write_file(self, arguments: dict) -> ToolResult:
        path, rel = self._resolve(arguments.get("path"), allow_missing=True)
        content = arguments.get("content")
        if not isinstance(content, str):
            return ToolResult(False, "content precisa ser string")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_WRITE_BYTES:
            return ToolResult(False, f"conteúdo excede limite ({MAX_WRITE_BYTES} bytes)")
        if path.exists() and not path.is_file():
            return ToolResult(False, "destino existe e não é arquivo")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._assert_inside(path.parent.resolve(strict=True))
        previous_mode = path.stat().st_mode if path.exists() else None
        _atomic_write_bytes(path, encoded, previous_mode)
        return ToolResult(True, "arquivo gravado", {"path": rel.as_posix(), "bytes": len(encoded)})

    def _replace_text(self, arguments: dict) -> ToolResult:
        path, rel = self._resolve(arguments.get("path"))
        old = arguments.get("old")
        new = arguments.get("new")
        expected = arguments.get("expected_occurrences", 1)
        if not isinstance(old, str) or not old or not isinstance(new, str):
            return ToolResult(False, "old precisa ser string não vazia e new precisa ser string")
        expected_count = _bounded_int(expected, 1, 1000, "expected_occurrences")
        if path.stat().st_size > MAX_WRITE_BYTES:
            return ToolResult(False, f"arquivo excede limite de edição ({MAX_WRITE_BYTES} bytes)")
        text = path.read_text(encoding="utf-8")
        actual = text.count(old)
        if actual != expected_count:
            return ToolResult(
                False, f"trecho aparece {actual} vez(es), esperado {expected_count}",
                {"path": rel.as_posix(), "actual_occurrences": actual},
            )
        updated = text.replace(old, new, expected_count)
        encoded = updated.encode("utf-8")
        if len(encoded) > MAX_WRITE_BYTES:
            return ToolResult(False, f"resultado excede limite ({MAX_WRITE_BYTES} bytes)")
        _atomic_write_bytes(path, encoded, path.stat().st_mode)
        return ToolResult(True, "trecho substituído", {"path": rel.as_posix(), "occurrences": actual})

    def _git_diff(self, arguments: dict) -> ToolResult:
        staged = arguments.get("staged", False)
        if not isinstance(staged, bool):
            return ToolResult(False, "staged precisa ser boolean")
        status = self.command_runner.run(["git", "status", "--short"], self.root, self.command_timeout)
        if not status.ok:
            return status
        argv = ["git", "diff"] + (["--cached"] if staged else [])
        diff = self.command_runner.run(argv, self.root, self.command_timeout)
        if not diff.ok:
            return diff
        status_text = status.metadata.get("stdout", "")
        diff_text = diff.metadata.get("stdout", "")
        return ToolResult(
            True, f"STATUS\n{status_text}\nDIFF\n{diff_text}",
            {"staged": staged, "truncated": status.metadata.get("truncated") or diff.metadata.get("truncated")},
        )

    def _run_command(self, arguments: dict) -> ToolResult:
        argv = arguments.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
            return ToolResult(False, "argv precisa ser lista não vazia de strings")
        cwd, rel = self._resolve(arguments.get("cwd", "."))
        if not cwd.is_dir():
            return ToolResult(False, "cwd não é diretório")
        requested_timeout = arguments.get("timeout", self.command_timeout)
        if not isinstance(requested_timeout, (int, float)) or isinstance(requested_timeout, bool):
            return ToolResult(False, "timeout precisa ser número")
        timeout = min(float(requested_timeout), self.command_timeout)
        if timeout <= 0:
            return ToolResult(False, "timeout precisa ser positivo")
        executable_path = Path(argv[0])
        if executable_path.is_absolute() or any(part == ".." for part in executable_path.parts):
            return ToolResult(False, "caminho do executável precisa permanecer no workspace")
        if executable_path != Path(executable_path.name):
            resolved_executable = (cwd / executable_path).resolve(strict=True)
            self._assert_inside(resolved_executable)
            if not resolved_executable.is_file():
                return ToolResult(False, "executável local não é arquivo")
        result = self.command_runner.run(argv, cwd, timeout)
        metadata = dict(result.metadata)
        metadata["cwd"] = rel.as_posix()
        return ToolResult(result.ok, result.content, metadata)

    def _assert_inside(self, path: Path) -> None:
        if path != self.root and self.root not in path.parents:
            raise ValueError("caminho escapou do workspace")


def _bounded_int(value: object, minimum: int, maximum: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"{name} precisa ser inteiro entre {minimum} e {maximum}")
    return value


def _atomic_write_bytes(path: Path, content: bytes, previous_mode: int | None) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if previous_mode is not None:
            os.chmod(temp_name, previous_mode)
        os.replace(temp_name, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temp_name)
        raise
