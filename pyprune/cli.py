"""CLI entrypoint for PyPrune."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .ui.app import PackageCleanerApp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pyprune",
        description="Open the Python Package Cleaner GUI.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="Directory to inspect. Use '.' for the current directory. If it has a .venv, that venv is used.",
    )
    parser.add_argument(
        "--root",
        action="store_true",
        help="Inspect the home directory context instead of the current directory.",
    )
    return parser


def resolve_python_for_path(path: Path) -> tuple[str, str]:
    """Resolve the Python executable and label for a given directory or file path."""
    target = path.expanduser().resolve()
    if target.is_file():
        return str(target), str(target)

    if not target.exists():
        raise FileNotFoundError(f"Target path does not exist: {target}")

    venv_python = target / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if venv_python.exists():
        return str(venv_python), str(target)

    return sys.executable, str(target)


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.root:
        target = Path.home()
        python_executable, target_label = resolve_python_for_path(target)
        app = PackageCleanerApp(
            python_executable=python_executable,
            target_label=target_label,
            scan_on_start=target,
        )
    else:
        target = Path(args.path or ".")
        python_executable, target_label = resolve_python_for_path(target)
        app = PackageCleanerApp(python_executable=python_executable, target_label=target_label)
    app.mainloop()


if __name__ == "__main__":
    main()
