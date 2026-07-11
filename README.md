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
