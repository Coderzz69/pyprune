"""Subprocess runner for PyPrune — all external process calls with security hardening."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .models import PackageGraph

# Default timeout for all subprocess calls (seconds).
DEFAULT_TIMEOUT = 60

# Regex for validating package names (PEP 508 compatible).
_VALID_PKG_NAME = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")


class SubprocessError(RuntimeError):
    """Raised when a subprocess call fails."""

    def __init__(self, message: str, command: list[str] | None = None, returncode: int | None = None, stderr: str = "") -> None:
        super().__init__(message)
        self.command = command
        self.returncode = returncode
        self.stderr = stderr


def _validate_executable(path: str) -> None:
    """Ensure the given executable path actually exists."""
    resolved = Path(path).resolve()
    if not resolved.exists():
        raise SubprocessError(f"Executable not found: {path}")
    if not resolved.is_file():
        raise SubprocessError(f"Executable path is not a file: {path}")


def _validate_package_names(names: list[str]) -> None:
    """Validate that package names contain only safe characters."""
    for name in names:
        if not _VALID_PKG_NAME.match(name):
            raise SubprocessError(f"Invalid package name: {name!r}")


def _run(command: list[str], *, timeout: int = DEFAULT_TIMEOUT) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with timeout and no shell."""
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            shell=False,  # noqa: S603 — explicit for security
        )
    except subprocess.TimeoutExpired as exc:
        raise SubprocessError(
            f"Command timed out after {timeout}s: {' '.join(command)}",
            command=command,
        ) from exc


def pipdeptree_command(python_executable: str) -> list[str] | None:
    """Probe whether pipdeptree is available and return the best command to use.

    Strategy:
    1. Try running pipdeptree from the *target* Python (it may have it installed).
    2. Fall back to running pipdeptree from *pyprune's own* Python (sys.executable)
       with ``--python <target>`` so we can inspect any venv without installing
       pipdeptree into it.
    """
    _validate_executable(python_executable)

    # 1. Target Python has pipdeptree?
    probe = _run([python_executable, "-m", "pipdeptree", "--version"])
    if probe.returncode == 0:
        return [python_executable, "-m", "pipdeptree"]

    # 2. Fall back to pyprune's own Python + --python <target>.
    if python_executable != sys.executable:
        probe = _run([sys.executable, "-m", "pipdeptree", "--version"])
        if probe.returncode == 0:
            return [sys.executable, "-m", "pipdeptree", "--python", python_executable]

    return None


def install_pipdeptree(python_executable: str) -> None:
    """Install pipdeptree into the target Python environment."""
    _validate_executable(python_executable)
    result = _run([python_executable, "-m", "pip", "install", "pipdeptree"])
    if result.returncode != 0:
        raise SubprocessError(
            result.stderr.strip() or result.stdout.strip() or "Failed to install pipdeptree.",
            command=[python_executable, "-m", "pip", "install", "pipdeptree"],
            returncode=result.returncode,
            stderr=result.stderr,
        )


def run_pipdeptree(python_executable: str, *, ask_install: Any = None) -> list[dict[str, Any]]:
    """Run pipdeptree --json and return the parsed payload.

    Parameters
    ----------
    python_executable:
        Path to the Python interpreter to inspect.
    ask_install:
        Optional callable that returns True if the user agrees to install pipdeptree.
        If None and pipdeptree is missing, a RuntimeError is raised.
    """
    command = pipdeptree_command(python_executable)
    if command is None:
        if ask_install and ask_install():
            install_pipdeptree(python_executable)
            command = pipdeptree_command(python_executable)
        if command is None:
            raise SubprocessError("pipdeptree is required to inspect dependencies.")

    result = _run(command + ["--json"])
    if result.returncode != 0:
        if "No module named pipdeptree" in result.stderr or "not found" in result.stderr.lower():
            if ask_install and ask_install():
                install_pipdeptree(python_executable)
                result = _run(command + ["--json"])
        if result.returncode != 0:
            raise SubprocessError(
                result.stderr.strip() or result.stdout.strip() or "pipdeptree failed.",
                command=command + ["--json"],
                returncode=result.returncode,
                stderr=result.stderr,
            )

    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SubprocessError(f"pipdeptree returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise SubprocessError("pipdeptree returned an unexpected JSON structure.")
    return parsed


def fetch_locations(python_executable: str, graph: PackageGraph) -> None:
    """Query the target Python for installed package locations and update the graph in-place."""
    _validate_executable(python_executable)
    script = (
        "import json, importlib.metadata as md\n"
        "locs = {}\n"
        "for d in md.distributions():\n"
        "    name = d.metadata.get('Name', '')\n"
        "    if name and hasattr(d, '_path'):\n"
        "        locs[name.lower()] = str(d._path.parent)\n"
        "print(json.dumps(locs))\n"
    )
    result = _run([python_executable, "-c", script])
    if result.returncode != 0:
        return
    try:
        locations: dict[str, str] = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return
    for key, pkg in graph.packages.items():
        pkg.location = locations.get(key, locations.get(pkg.name.lower(), ""))


def pip_list_packages(python_executable: str) -> list[tuple[str, str]]:
    """List installed packages via pip list --format=json."""
    _validate_executable(python_executable)
    result = _run([python_executable, "-m", "pip", "list", "--format=json"])
    if result.returncode != 0:
        return []
    try:
        pkg_list = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return []
    packages = [(p["name"], p.get("version", "?")) for p in pkg_list]
    packages.sort(key=lambda p: p[0].lower())
    return packages


def pip_uninstall(python_executable: str, package_names: list[str]) -> None:
    """Uninstall packages via pip uninstall -y."""
    _validate_executable(python_executable)
    _validate_package_names(package_names)
    result = _run([python_executable, "-m", "pip", "uninstall", "-y", *package_names])
    if result.returncode != 0:
        output = result.stderr.strip() or result.stdout.strip() or "pip uninstall failed."
        raise SubprocessError(
            output,
            command=[python_executable, "-m", "pip", "uninstall", "-y", *package_names],
            returncode=result.returncode,
            stderr=result.stderr,
        )
