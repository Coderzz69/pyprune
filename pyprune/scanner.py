"""Filesystem scanner for PyPrune — venv discovery and directory scanning."""

from __future__ import annotations

import sys
from pathlib import Path


# Directories to skip during venv scanning.
_SKIP_DIRS = frozenset({
    "node_modules", "__pycache__", ".git", ".tox", ".nox",
    ".cache", ".local", ".config", ".mozilla", ".thunderbird",
    ".cargo", ".rustup", ".npm", ".nvm", ".pyenv", ".conda",
    ".steam", ".var", ".flatpak", "snap", ".gemini",
    "Downloads", "Music", "Pictures", "Videos", "Templates",
    "Public", ".gnupg", ".ssh",
})

# Recognised virtual environment directory names.
_VENV_NAMES = frozenset({".venv", "venv"})


def resolve_search_path(query: str) -> Path | None:
    """Try to resolve a search query string as a directory path.

    Returns the resolved ``Path`` if valid, otherwise ``None``.
    """
    if not query:
        return None
    candidates = [
        Path(query),
        Path.home() / query,
        Path(query).expanduser(),
    ]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if resolved.is_dir():
                return resolved
        except (OSError, ValueError):
            continue
    return None


def find_venvs(
    directory: Path,
    max_depth: int = 3,
) -> list[tuple[str, str, str]]:
    """Find virtual environment directories under *directory*.

    Returns a list of ``(project_name, project_path, python_path)`` tuples.
    Symlinks are resolved to prevent path-traversal outside *directory*.
    """
    venvs: list[tuple[str, str, str]] = []
    python_name = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    root = directory.resolve()

    def _walk(current: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = sorted(current.iterdir())
        except (PermissionError, OSError):
            return
        for entry in entries:
            if not entry.is_dir() or entry.is_symlink():
                continue
            name = entry.name
            if name in _SKIP_DIRS:
                continue
            if name.startswith(".") and name not in _VENV_NAMES:
                continue

            # Path-traversal guard: ensure resolved path stays within root.
            try:
                resolved = entry.resolve()
                resolved.relative_to(root)
            except (ValueError, OSError):
                continue

            if name in _VENV_NAMES:
                python = entry / python_name
                if python.exists():
                    try:
                        rel_path = str(entry.parent.relative_to(directory))
                        name_to_use = entry.parent.name if rel_path == "." else rel_path
                    except ValueError:
                        name_to_use = entry.parent.name
                    venvs.append((name_to_use, str(entry.parent), str(python)))
            else:
                _walk(entry, depth + 1)

    _walk(directory, 0)
    return venvs
