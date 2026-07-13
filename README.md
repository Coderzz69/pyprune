# PyPrune

A Tkinter GUI for reviewing installed Python packages as a dependency tree and
marking packages to keep or uninstall.

## Install

After publishing to PyPI:

```bash
pip install pyprune
pyprune
```

Launch modes:

```bash
pyprune --root
pyprune .
```

`pyprune .` inspects the current directory. If that directory contains a
`.venv`, the app manages packages from that virtual environment.

`pyprune --root` opens from your home directory context.

For local development from this repository:

```bash
python -m pip install -e .
pyprune
```

The app uses `pipdeptree --json` for discovery. `pipdeptree` is installed as a
package dependency.

On Linux, Tkinter may be packaged separately from Python. If the app fails with
`No module named tkinter`, install your distro's Tkinter package first, for
example:

```bash
sudo dnf install python3-tkinter
sudo apt install python3-tk
```

## Features

- Builds a dependency graph from `pipdeptree --json`.
- Shows top-level packages in green, dependencies in blue, and orphan packages in
  orange.
- Displays package versions and reverse dependencies.
- Uses clickable checkbox markers to keep or mark packages for deletion.
- Warns before marking packages that other packages depend on.
- Uninstalls marked packages with `python -m pip uninstall -y`.
- Scans directories for virtual environments (`pyprune --root`).
- Inspects any venv without requiring pipdeptree to be installed in it.

## Project Structure

```
pyprune/
├── __init__.py            # Public API re-exports
├── __main__.py            # python -m pyprune entrypoint
├── cli.py                 # Argument parsing & main()
├── models.py              # PackageInfo and PackageGraph data models
├── subprocess_runner.py   # Subprocess calls with timeouts & validation
├── scanner.py             # Virtual environment discovery
└── ui/
    ├── __init__.py
    ├── app.py             # Main application window (orchestrator)
    ├── toolbar.py         # Toolbar and legend widgets
    ├── tree_view.py       # Package tree widget
    └── detail_panel.py    # Package details panel
```

| Module | Responsibility |
|--------|---------------|
| `models.py` | Pure data — `PackageInfo` dataclass and `PackageGraph` dependency graph. No UI or subprocess dependencies. |
| `subprocess_runner.py` | All external process calls (`pipdeptree`, `pip list`, `pip uninstall`). Adds timeouts, path validation, and package name sanitization. |
| `scanner.py` | Filesystem scanning to find `.venv`/`venv` directories. Includes path-traversal protection via symlink resolution. |
| `cli.py` | CLI argument parsing (`--root`, path positional) and the `main()` entrypoint. |
| `ui/app.py` | `PackageCleanerApp(tk.Tk)` — composes the UI widgets, manages application state, and coordinates background threads. |
| `ui/toolbar.py` | `ToolbarFrame` (buttons, search) and `LegendFrame` (colour-coded role labels). Callback-driven, no parent coupling. |
| `ui/tree_view.py` | `PackageTreeView` — Treeview with scrollbars, package insertion, filtering, checkbox toggling, and scan display. |
| `ui/detail_panel.py` | `DetailPanel` — side panel showing package metadata, dependencies, and venv info. |

## Security

- **Subprocess timeouts** — all subprocess calls have a 60-second timeout to
  prevent hangs from broken interpreters or network issues.
- **Path validation** — Python executable paths are verified to exist and be
  regular files before being passed to `subprocess.run`.
- **Package name sanitization** — names are validated against
  `^[A-Za-z0-9._-]+$` before being passed to `pip uninstall`.
- **Path-traversal protection** — venv scanning resolves symlinks and ensures
  traversed paths stay within the original scan root.
- **No shell execution** — all subprocess calls use `shell=False`.

## Publish

Install build tools:

```bash
python -m pip install --upgrade build twine
```

Build the distribution:

```bash
python -m build
```

Check the artifacts:

```bash
python -m twine check dist/*
```

Upload to TestPyPI first:

```bash
python -m twine upload --repository testpypi dist/*
```

Then upload to PyPI:

```bash
python -m twine upload dist/*
```

Before uploading, confirm that the package name `pyprune` is available
on PyPI. If it is taken, change `project.name` in `pyproject.toml`.

## Notes

`pipdeptree` does not reliably expose whether a package was explicitly installed
by the user in every environment. This app classifies packages with no reverse
dependencies as top-level roots, and packages with no reverse dependencies and no
dependencies of their own as orphan packages.
