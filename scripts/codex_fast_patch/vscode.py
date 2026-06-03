"""VS Code extension filesystem operations for patch-codex-fast."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path


EXTENSION_RE = re.compile(r"openai\.chatgpt-(?P<version>\d+(?:\.\d+)+)(?:-.+)?$")


@dataclass
class VscodeExtensionPaths:
    """Resolved OpenAI Codex VS Code extension paths."""

    extension_dir: Path
    is_vscode_extension: bool = True

    @property
    def assets_dir(self) -> Path:
        return self.extension_dir / "webview" / "assets"

    @property
    def extracted_app_dir(self) -> Path:
        return self.extension_dir

    @property
    def backup_dir(self) -> Path:
        return self.extension_dir.with_name(f"{self.extension_dir.name}.codex-fast-backup")


def detect_vscode_extension_paths(
    extension_dir: str | None = None,
    *,
    home: Path | None = None,
) -> VscodeExtensionPaths:
    """Resolve the newest installed OpenAI Codex VS Code extension."""

    if extension_dir is not None:
        paths = VscodeExtensionPaths(Path(extension_dir).expanduser())
        ensure_vscode_extension(paths)
        return paths

    root = home or Path.home()
    candidates: list[Path] = []
    for extensions_dir in vscode_extension_roots(root):
        if not extensions_dir.exists():
            continue
        candidates.extend(
            path
            for path in extensions_dir.glob("openai.chatgpt-*")
            if path.is_dir() and extension_version(path) is not None
        )

    if not candidates:
        raise SystemExit(
            "OpenAI Codex VS Code extension not found. Pass --extension-dir explicitly."
        )

    newest = max(candidates, key=lambda path: extension_version(path) or ())
    paths = VscodeExtensionPaths(newest)
    ensure_vscode_extension(paths)
    return paths


def vscode_extension_roots(home: Path) -> tuple[Path, ...]:
    return (
        home / ".vscode-server" / "extensions",
        home / ".vscode" / "extensions",
        home / ".cursor-server" / "extensions",
        home / ".cursor" / "extensions",
        home / ".windsurf-server" / "extensions",
        home / ".windsurf" / "extensions",
    )


def extension_version(path: Path) -> tuple[int, ...] | None:
    match = EXTENSION_RE.match(path.name)
    if match is None:
        return None
    return tuple(int(part) for part in match.group("version").split("."))


def ensure_vscode_extension(paths: VscodeExtensionPaths) -> None:
    if not paths.extension_dir.exists():
        raise SystemExit(f"VS Code extension directory not found: {paths.extension_dir}")
    if not paths.assets_dir.exists():
        raise SystemExit(f"VS Code extension webview assets not found: {paths.assets_dir}")


def backup_vscode_extension(paths: VscodeExtensionPaths) -> None:
    ensure_vscode_extension(paths)
    if paths.backup_dir.exists():
        raise SystemExit(
            f"Backup already exists: {paths.backup_dir}. "
            "Run rollback-vscode first or remove the stale backup after inspecting it."
        )
    shutil.copytree(paths.extension_dir, paths.backup_dir)
    print(f"[OK] Backed up VS Code extension -> {paths.backup_dir}")


def rollback_vscode_extension(paths: VscodeExtensionPaths) -> None:
    if not paths.backup_dir.exists():
        raise SystemExit(f"VS Code extension backup not found: {paths.backup_dir}")
    if paths.extension_dir.exists():
        shutil.rmtree(paths.extension_dir)
    shutil.copytree(paths.backup_dir, paths.extension_dir)
    shutil.rmtree(paths.backup_dir)
    print(f"[OK] Restored VS Code extension from backup: {paths.extension_dir}")


def print_vscode_doctor(paths: VscodeExtensionPaths) -> None:
    print("Platform: VS Code extension")
    print(f"Extension: {paths.extension_dir}")
    print(f"Assets: {paths.assets_dir}")
    print(f"Backup: {'yes' if paths.backup_dir.exists() else 'no'}")
