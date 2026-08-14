from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

from pathspec.gitignore import GitIgnoreSpec

from .api import ManusAPIError, ManusClient

IGNORED_DIR_NAMES = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}

SECRET_DIR_NAMES = {".ssh", ".aws", ".gnupg"}
SECRET_NAME_PATTERNS = [
    ".env", ".env.*", "*.pem", "*.key", "*_rsa", "id_rsa*",
    "credentials.json", "secrets*.json", "*.p12", "*.pfx", ".npmrc", ".netrc", "*.pgpass",
]

# https://open.manus.ai/docs/v2/file.upload — "All types accepted except executables
# and scripts". We can't enumerate every rejected type the API might apply server-side,
# so this is a conservative client-side pre-filter for the obvious ones, not a promise
# of exhaustive parity with the API's own validation.
REJECTED_EXTENSIONS = {".exe", ".sh", ".bat", ".dmg", ".msi", ".cmd", ".com"}

MAX_FILE_BYTES = 200 * 1024 * 1024  # comfortably under the documented 512MB/file cap
MAX_TOTAL_BYTES = 500 * 1024 * 1024
MAX_FILE_COUNT = 200

def looks_like_secret(rel_path: Path) -> bool:
    lowered_parts = tuple(part.lower() for part in rel_path.parts)
    if any(part in SECRET_DIR_NAMES for part in lowered_parts):
        return True
    lowered_name = rel_path.name.lower()
    return any(fnmatch.fnmatch(lowered_name, pattern.lower()) for pattern in SECRET_NAME_PATTERNS)


def is_rejected_type(path: Path) -> bool:
    return path.suffix.lower() in REJECTED_EXTENSIONS


@dataclass
class GitignoreMatcher:
    """Git wildmatch rules, including nested .gitignore scopes and negation."""

    root: Path
    rules: list[tuple[Path, GitIgnoreSpec]] = field(default_factory=list)

    @classmethod
    def load(cls, root: Path) -> GitignoreMatcher:
        root = root.resolve()
        rules: list[tuple[Path, GitIgnoreSpec]] = []
        ignore_files = []
        for gi in root.rglob(".gitignore"):
            rel = gi.relative_to(root)
            if any(part in IGNORED_DIR_NAMES for part in rel.parts[:-1]):
                continue
            ignore_files.append(gi)
        for gi in sorted(ignore_files, key=lambda p: (len(p.relative_to(root).parts), p.as_posix())):
            try:
                spec = GitIgnoreSpec.from_lines(gi.read_text(errors="ignore").splitlines())
            except (OSError, ValueError):
                continue
            rules.append((gi.parent.relative_to(root), spec))
        return cls(root=root, rules=rules)

    def matches(self, rel_path: Path) -> bool:
        ignored = False
        for base, spec in self.rules:
            try:
                scoped_path = rel_path.relative_to(base) if base.parts else rel_path
            except ValueError:
                continue
            scoped = scoped_path.as_posix()
            for pattern in spec.patterns:
                if pattern.include is not None and pattern.match_file(scoped) is not None:
                    ignored = pattern.include
        return ignored


@dataclass
class SkippedFile:
    relative_path: Path
    reason: str


@dataclass
class SelectedFile:
    absolute_path: Path
    relative_path: Path
    size: int
    display_name: str


@dataclass
class SelectionResult:
    files: list[SelectedFile]
    skipped: list[SkippedFile]
    total_bytes: int


def select_project_files(
    root: Path, *, allow_secret: bool = False, respect_gitignore: bool = True
) -> SelectionResult:
    root = root.resolve()
    gitignore = GitignoreMatcher.load(root) if respect_gitignore else None

    raw_candidates: list[tuple[Path, Path, int]] = []
    skipped: list[SkippedFile] = []

    for path in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
        rel = path.relative_to(root)
        if any(part in IGNORED_DIR_NAMES for part in rel.parts):
            continue

        if path.is_symlink():
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                skipped.append(SkippedFile(rel, "link simbólico quebrado"))
                continue
            if resolved != root and root not in resolved.parents:
                skipped.append(SkippedFile(rel, "link simbólico aponta fora do projeto"))
                continue
            if not resolved.is_file():
                continue
        elif not path.is_file():
            continue

        if gitignore and gitignore.matches(rel):
            continue
        if looks_like_secret(rel) and not allow_secret:
            skipped.append(SkippedFile(rel, "parece segredo (use --allow-secret se for engano)"))
            continue
        if is_rejected_type(path):
            skipped.append(SkippedFile(rel, "tipo de arquivo não aceito pela API (executável/script)"))
            continue
        try:
            size = path.stat().st_size
        except OSError as e:
            skipped.append(SkippedFile(rel, f"erro ao ler metadados: {e}"))
            continue
        if size > MAX_FILE_BYTES:
            skipped.append(SkippedFile(rel, f"maior que {MAX_FILE_BYTES // (1024 * 1024)}MB"))
            continue
        raw_candidates.append((rel, path, size))

    files: list[SelectedFile] = []
    used_names: dict[str, int] = {}
    total = 0
    for rel, path, size in raw_candidates:
        if len(files) >= MAX_FILE_COUNT:
            skipped.append(SkippedFile(rel, f"limite de {MAX_FILE_COUNT} arquivos por envio atingido"))
            continue
        if total + size > MAX_TOTAL_BYTES:
            skipped.append(SkippedFile(rel, f"limite de {MAX_TOTAL_BYTES // (1024 * 1024)}MB no envio total atingido"))
            continue
        base = rel.name
        count = used_names.get(base, 0)
        display = base if count == 0 else f"{Path(base).stem}__{count + 1}{Path(base).suffix}"
        used_names[base] = count + 1
        files.append(SelectedFile(absolute_path=path, relative_path=rel, size=size, display_name=display))
        total += size

    return SelectionResult(files=files, skipped=skipped, total_bytes=total)


def check_single_file(path: Path, *, allow_secret: bool = False) -> str | None:
    """Apply the same secret/type/size policy to a single file (e.g. an @mention).

    Returns a skip reason string, or None if the file is fine to upload.
    """
    if looks_like_secret(path) and not allow_secret:
        return "parece segredo (use --allow-secret se for engano)"
    if is_rejected_type(path):
        return "tipo de arquivo não aceito pela API (executável/script)"
    try:
        size = path.stat().st_size
    except OSError as e:
        return f"erro ao ler metadados: {e}"
    if size > MAX_FILE_BYTES:
        return f"maior que {MAX_FILE_BYTES // (1024 * 1024)}MB"
    return None


def build_manifest_text(files: list[SelectedFile]) -> str:
    """A plain-text manifest mapping each uploaded file back to its real relative
    path, since the API's `filename` is just a display string and collisions get
    renamed — this is the unambiguous source of truth for the agent."""
    lines = ["Arquivos do projeto enviados (caminho relativo -> nome no anexo, tamanho):"]
    for f in files:
        lines.append(f"- {f.relative_path.as_posix()} -> {f.display_name} ({f.size} bytes)")
    return "\n".join(lines)


@dataclass
class UploadBatchResult:
    content: list[dict]
    uploaded: list[SelectedFile]
    failed: list[SkippedFile]


def upload_many(client: ManusClient, files: list[SelectedFile], *, on_progress=None) -> UploadBatchResult:
    content: list[dict] = []
    uploaded: list[SelectedFile] = []
    failed: list[SkippedFile] = []

    for f in files:
        try:
            file_id = client.upload_file(f.absolute_path, filename=f.display_name)
        except ManusAPIError as e:
            failed.append(SkippedFile(f.relative_path, f"upload falhou: {e.message}"))
            if on_progress:
                on_progress(f, False)
            continue
        content.append({"type": "file", "file_id": file_id, "filename": f.display_name})
        uploaded.append(f)
        if on_progress:
            on_progress(f, True)

    return UploadBatchResult(content=content, uploaded=uploaded, failed=failed)
