#!/usr/bin/env python3
"""PyPrune - Tkinter GUI for inspecting and pruning installed Python packages."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import font, messagebox, ttk
from typing import Any


APP_TITLE = "PyPrune – Python Package Dependency Cleaner"
TRASH_PREFIX = "\U0001f5d1\ufe0f "
CHECKED = "\u2611"
UNCHECKED = "\u2610"


@dataclass
class PackageInfo:
    key: str
    name: str
    version: str = "unknown"
    location: str = ""
    dependencies: set[str] = field(default_factory=set)
    required_by: set[str] = field(default_factory=set)


class PackageGraph:
    """Dependency graph built from pipdeptree --json output."""

    def __init__(self, packages: dict[str, PackageInfo]) -> None:
        self.packages = packages

    @classmethod
    def from_pipdeptree_json(cls, payload: list[dict[str, Any]]) -> "PackageGraph":
        packages: dict[str, PackageInfo] = {}

        def read_pkg(raw: dict[str, Any]) -> PackageInfo:
            key = str(raw.get("key") or raw.get("package_name") or raw.get("name") or "").lower()
            name = str(raw.get("package_name") or raw.get("name") or key)
            version = str(raw.get("installed_version") or raw.get("version") or "unknown")
            if not key:
                key = name.lower()
            info = packages.get(key)
            if info is None:
                info = PackageInfo(key=key, name=name, version=version)
                packages[key] = info
            else:
                info.name = info.name or name
                if info.version == "unknown" and version != "unknown":
                    info.version = version
            return info

        for entry in payload:
            parent = read_pkg(entry.get("package", {}))
            for dep_raw in entry.get("dependencies", []):
                child = read_pkg(dep_raw)
                parent.dependencies.add(child.key)
                child.required_by.add(parent.key)

        return cls(packages)

    @property
    def root_keys(self) -> list[str]:
        return sorted(
            (key for key, pkg in self.packages.items() if not pkg.required_by),
            key=lambda key: self.packages[key].name.lower(),
        )

    @property
    def orphan_keys(self) -> list[str]:
        return sorted(
            (
                key
                for key, pkg in self.packages.items()
                if not pkg.required_by and not pkg.dependencies
            ),
            key=lambda key: self.packages[key].name.lower(),
        )

    def role_for(self, key: str) -> str:
        pkg = self.packages[key]
        if not pkg.required_by and not pkg.dependencies:
            return "Orphan"
        if not pkg.required_by:
            return "Top-level"
        return "Dependency"


class PackageCleanerApp(tk.Tk):
    def __init__(self, python_executable: str | None = None, target_label: str | None = None, scan_on_start: Path | None = None) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1180x760")
        self.minsize(900, 560)

        self.python_executable = python_executable or sys.executable
        self.target_label = target_label or self.python_executable
        self.graph = PackageGraph({})
        self.keep_state: dict[str, bool] = {}
        self.item_to_key: dict[str, str] = {}
        self.key_to_items: dict[str, list[str]] = {}
        self.scan_mode = False
        self.current_scan_dir: Path | None = None
        self.previous_scan_dir: Path | None = None
        self.venv_data: list[tuple[str, str, str, list[tuple[str, str]]]] = []
        self.scan_items: dict[str, dict[str, str]] = {}
        self.status_var = tk.StringVar(value="Ready")
        self.summary_var = tk.StringVar(value="")
        self.search_var = tk.StringVar(value="")

        default_font = font.nametofont("TkDefaultFont")
        self.deleted_font = default_font.copy()
        self.deleted_font.configure(overstrike=True)

        self._build_ui()
        if scan_on_start is not None:
            self.after(150, lambda: self.scan_directory(scan_on_start))
        else:
            self.after(150, self.refresh_packages)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        toolbar = ttk.Frame(self, padding=(12, 10, 12, 6))
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(6, weight=1)

        self.btn_back = ttk.Button(toolbar, text="◀ Back", command=self.go_back)
        self.btn_back.grid(row=0, column=0, padx=(0, 8))
        self.btn_back.grid_remove()

        ttk.Button(toolbar, text="Refresh", command=self.refresh_packages).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(toolbar, text="Keep All", command=self.keep_all).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(toolbar, text="Delete Selected", command=self.delete_selected).grid(row=0, column=3, padx=(0, 16))

        ttk.Label(toolbar, text="Search").grid(row=0, column=4, padx=(0, 6))
        search = ttk.Entry(toolbar, textvariable=self.search_var, width=34)
        search.grid(row=0, column=5, sticky="ew")
        search.bind("<Return>", lambda _event: self.on_search())
        ttk.Button(toolbar, text="Clear", command=self.clear_search).grid(row=0, column=6, sticky="w", padx=(8, 0))

        legend = ttk.Frame(self, padding=(12, 0, 12, 6))
        legend.grid(row=1, column=0, sticky="ew")
        self._legend_label(legend, "Top-level", "#1a7f37").grid(row=0, column=0, padx=(0, 16))
        self._legend_label(legend, "Dependency", "#0969da").grid(row=0, column=1, padx=(0, 16))
        self._legend_label(legend, "Orphan", "#bc4c00").grid(row=0, column=2, padx=(0, 16))
        ttk.Label(legend, textvariable=self.summary_var).grid(row=0, column=3, sticky="w")

        content = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        content.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 8))

        tree_frame = ttk.Frame(content)
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        columns = ("version", "role", "location", "required_by")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Package")
        self.tree.heading("version", text="Version")
        self.tree.heading("role", text="Role")
        self.tree.heading("location", text="Location")
        self.tree.heading("required_by", text="Required by")
        self.tree.column("#0", width=320, minwidth=200, stretch=True)
        self.tree.column("version", width=120, minwidth=80, stretch=False)
        self.tree.column("role", width=100, minwidth=80, stretch=False)
        self.tree.column("location", width=220, minwidth=120, stretch=True)
        self.tree.column("required_by", width=260, minwidth=140, stretch=True)
        self.tree.grid(row=0, column=0, sticky="nsew")

        yscroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        xscroll = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")

        self.tree.tag_configure("top", foreground="#1a7f37")
        self.tree.tag_configure("dep", foreground="#0969da")
        self.tree.tag_configure("orphan", foreground="#bc4c00")
        self.tree.tag_configure("delete", foreground="#8c1d18", font=self.deleted_font)
        self.tree.tag_configure("venv", foreground="#6f42c1")
        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.bind("<Double-1>", self.on_tree_double_click)
        self.tree.bind("<<TreeviewSelect>>", self.update_details)

        detail_frame = ttk.Frame(content, padding=(12, 0, 0, 0))
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(1, weight=1)
        ttk.Label(detail_frame, text="Package Details", font=("TkDefaultFont", 12, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 6)
        )
        self.details = tk.Text(detail_frame, height=12, wrap="word", state="disabled", padx=8, pady=8)
        self.details.grid(row=1, column=0, sticky="nsew")

        content.add(tree_frame, weight=4)
        content.add(detail_frame, weight=2)

        status_bar = ttk.Frame(self, padding=(12, 0, 12, 10))
        status_bar.grid(row=3, column=0, sticky="ew")
        ttk.Label(status_bar, textvariable=self.status_var).grid(row=0, column=0, sticky="w")

    def _legend_label(self, parent: ttk.Frame, text: str, color: str) -> tk.Label:
        return tk.Label(parent, text=text, fg=color)

    def go_back(self) -> None:
        if self.previous_scan_dir:
            self.scan_directory(self.previous_scan_dir)
            self.btn_back.grid_remove()
            self.previous_scan_dir = None

    def on_search(self) -> None:
        """Handle search: filter packages or scan a directory for venvs."""
        query = self.search_var.get().strip()
        path = self._resolve_search_path(query)
        if path is not None:
            self.scan_directory(path)
        else:
            self.scan_mode = False
            self.populate_tree()

    def _resolve_search_path(self, query: str) -> Path | None:
        """Try to resolve the search query as a directory path."""
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

    def scan_directory(self, directory: Path) -> None:
        """Scan a directory for virtual environments."""
        self.current_scan_dir = directory
        self.set_busy(True, f"Scanning {directory} for virtual environments...")
        thread = threading.Thread(
            target=self._scan_worker, args=(directory,), daemon=True
        )
        thread.start()

    def _find_venvs(self, directory: Path, max_depth: int = 3) -> list[tuple[str, str, str]]:
        """Find .venv/venv directories. Returns (project_name, project_path, python_path)."""
        venvs: list[tuple[str, str, str]] = []
        python_name = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        venv_names = {".venv", "venv"}
        skip_dirs = {
            "node_modules", "__pycache__", ".git", ".tox", ".nox",
            ".cache", ".local", ".config", ".mozilla", ".thunderbird",
            ".cargo", ".rustup", ".npm", ".nvm", ".pyenv", ".conda",
            ".steam", ".var", ".flatpak", "snap", ".gemini",
            "Downloads", "Music", "Pictures", "Videos", "Templates",
            "Public", ".gnupg", ".ssh",
        }

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
                if name in skip_dirs:
                    continue
                if name.startswith(".") and name not in venv_names:
                    continue
                if name in venv_names:
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

    def _scan_worker(self, directory: Path) -> None:
        """Background worker to scan directory for venvs and load their packages."""
        self.after(0, lambda: self.status_var.set(f"Searching for virtual environments in {directory}..."))
        venvs = self._find_venvs(directory)
        
        venv_results: list[tuple[str, str, str, list[tuple[str, str]]]] = []

        # Load Global/System packages first
        self.after(0, lambda: self.status_var.set("Loading Global packages..."))
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "list", "--format=json"],
                capture_output=True, text=True, check=False,
            )
            if result.returncode == 0:
                pkg_list = json.loads(result.stdout)
                packages = [(p["name"], p.get("version", "?")) for p in pkg_list]
                packages.sort(key=lambda p: p[0].lower())
                venv_results.append(("🌐 Global (System/User)", "Global Environment", sys.executable, packages))
        except Exception:
            pass

        if not venvs and len(venv_results) == 0:
            self.after(0, lambda: self._handle_no_venvs(directory))
            return
            
        if not venvs:
            # We found global but no venvs, notify the user.
            self.after(0, lambda: messagebox.showinfo("No venvs found", f"No .venv or venv directories found in:\n{directory}"))

        if venvs:
            self.after(0, lambda: self.status_var.set(f"Found {len(venvs)} venv(s), loading packages..."))
            
        for i, (project_name, project_path, python_path) in enumerate(venvs, 1):
            self.after(0, lambda n=project_name, c=i, t=len(venvs): self.status_var.set(
                f"Loading packages from {n} ({c}/{t})..."
            ))
            try:
                result = subprocess.run(
                    [python_path, "-m", "pip", "list", "--format=json"],
                    capture_output=True, text=True, check=False,
                )
                if result.returncode != 0:
                    continue
                pkg_list = json.loads(result.stdout)
                packages = [(p["name"], p.get("version", "?")) for p in pkg_list]
                packages.sort(key=lambda p: p[0].lower())
                venv_results.append((project_name, project_path, python_path, packages))
            except Exception:
                continue

        if not venvs and len(venv_results) <= 1:
            # If only global was found and no venvs, we don't necessarily abort,
            # but we can just show global.
            pass
            
        self.after(0, lambda: self._apply_scan_results(directory, venvs, venv_results))

    def _handle_no_venvs(self, directory: Path) -> None:
        self.set_busy(False, f"No virtual environments found in {directory}.")
        messagebox.showinfo("No venvs found", f"No .venv or venv directories found in:\n{directory}")

    def _apply_scan_results(
        self,
        directory: Path,
        venvs: list[tuple[str, str, str]],
        results: list[tuple[str, str, str, list[tuple[str, str]]]],
    ) -> None:
        self.scan_mode = True
        self.venv_data = results
        self.populate_tree_scan()
        self.set_busy(False, f"Found {len(venvs)} virtual environment(s) in {directory} (plus Global).")

    def populate_tree_scan(self) -> None:
        """Populate tree with venv scan results."""
        self.tree.delete(*self.tree.get_children())
        self.item_to_key.clear()
        self.key_to_items.clear()
        self.scan_items.clear()

        for project_name, project_path, python_path, packages in self.venv_data:
            venv_item = self.tree.insert(
                "", tk.END,
                text=f"\U0001f4c1 {project_name}",
                values=(f"{len(packages)} packages", "Virtual Env", project_path, ""),
                tags=("venv",),
                open=False,
            )
            self.scan_items[venv_item] = {
                "type": "venv",
                "name": project_name,
                "path": project_path,
                "python": python_path,
                "pkg_count": str(len(packages)),
            }
            for pkg_name, pkg_version in packages:
                pkg_item = self.tree.insert(
                    venv_item, tk.END,
                    text=f"  {pkg_name}",
                    values=(pkg_version, "", "", ""),
                    tags=("dep",),
                )
                self.scan_items[pkg_item] = {
                    "type": "package",
                    "name": pkg_name,
                    "version": pkg_version,
                    "venv": project_name,
                    "venv_path": project_path,
                    "python": python_path,
                }

        total_venvs = len(self.venv_data)
        total_pkgs = sum(len(pkgs) for _, _, _, pkgs in self.venv_data)
        self.summary_var.set(f"{total_venvs} venvs found | {total_pkgs} packages total")
        self.update_details()

    def refresh_packages(self, focus_package: str | None = None) -> None:
        if self.scan_mode and self.current_scan_dir:
            self.scan_directory(self.current_scan_dir)
            return
        
        self.set_busy(True, f"Loading package dependency tree for {self.target_label}...")
        thread = threading.Thread(target=self._load_graph_worker, args=(focus_package,), daemon=True)
        thread.start()

    def _load_graph_worker(self, focus_package: str | None = None) -> None:
        try:
            payload = self.run_pipdeptree()
            graph = PackageGraph.from_pipdeptree_json(payload)
            self._fetch_locations(graph)
        except Exception as exc:  # noqa: BLE001 - surface any subprocess/JSON issue in the GUI.
            self.after(0, lambda exc=exc: self.handle_load_error(exc))
            return
        self.after(0, lambda: self.apply_graph(graph, focus_package))

    def _fetch_locations(self, graph: PackageGraph) -> None:
        """Query the target Python for installed package locations."""
        script = (
            "import json, importlib.metadata as md\n"
            "locs = {}\n"
            "for d in md.distributions():\n"
            "    name = d.metadata.get('Name', '')\n"
            "    if name and hasattr(d, '_path'):\n"
            "        locs[name.lower()] = str(d._path.parent)\n"
            "print(json.dumps(locs))\n"
        )
        result = subprocess.run(
            [self.python_executable, "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return
        try:
            locations: dict[str, str] = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            return
        for key, pkg in graph.packages.items():
            pkg.location = locations.get(key, locations.get(pkg.name.lower(), ""))

    def run_pipdeptree(self) -> list[dict[str, Any]]:
        command = self.pipdeptree_command()
        if command is None:
            install = self.ask_install_pipdeptree()
            if not install:
                raise RuntimeError("pipdeptree is required to inspect dependencies.")
            self.install_pipdeptree()
            command = self.pipdeptree_command()
            if command is None:
                raise RuntimeError("pipdeptree was installed, but no executable command was found.")

        result = subprocess.run(command + ["--json"], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            if "No module named pipdeptree" in result.stderr or "not found" in result.stderr.lower():
                install = self.ask_install_pipdeptree()
                if install:
                    self.install_pipdeptree()
                    result = subprocess.run(command + ["--json"], capture_output=True, text=True, check=False)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "pipdeptree failed.")

        try:
            parsed = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"pipdeptree returned invalid JSON: {exc}") from exc
        if not isinstance(parsed, list):
            raise RuntimeError("pipdeptree returned an unexpected JSON structure.")
        return parsed

    def pipdeptree_command(self) -> list[str] | None:
        probe = subprocess.run(
            [self.python_executable, "-m", "pipdeptree", "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0:
            return [self.python_executable, "-m", "pipdeptree"]
        return None

    def ask_install_pipdeptree(self) -> bool:
        result: list[bool] = []
        event = threading.Event()

        def ask() -> None:
            result.append(
                messagebox.askyesno(
                    "Install pipdeptree?",
                    "pipdeptree is required to discover installed packages and dependencies.\n\n"
                    "Install it into this Python environment now?",
                )
            )
            event.set()

        self.after(0, ask)
        event.wait()
        return bool(result and result[0])

    def install_pipdeptree(self) -> None:
        self.after(0, lambda: self.status_var.set("Installing pipdeptree..."))
        result = subprocess.run(
            [self.python_executable, "-m", "pip", "install", "pipdeptree"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "Failed to install pipdeptree.")

    def apply_graph(self, graph: PackageGraph, focus_package: str | None = None) -> None:
        self.graph = graph
        # preserve keep state across refreshes if keys match
        self.keep_state = {k: self.keep_state.get(k, True) for k in self.graph.packages}
        self.populate_tree()
        self.set_busy(
            False,
            f"Loaded {len(self.graph.packages)} installed packages from {self.target_label}.",
        )
        if focus_package and focus_package in self.key_to_items:
            items = self.key_to_items[focus_package]
            if items:
                self.tree.selection_set(items[0])
                self.tree.see(items[0])
                self.update_details()

    def handle_load_error(self, exc: Exception) -> None:
        self.set_busy(False, "Unable to load packages.")
        messagebox.showerror("Package discovery failed", str(exc))

    def populate_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.item_to_key.clear()
        self.key_to_items.clear()

        query = self.search_var.get().strip().lower()
        root_keys = self.graph.root_keys
        displayed = 0

        for key in root_keys:
            if query and not self.subtree_matches(key, query, set()):
                continue
            self.insert_package("", key, ancestors=set(), force_expand=bool(query))
            displayed += 1

        total = len(self.graph.packages)
        marked = sum(1 for keep in self.keep_state.values() if not keep)
        self.summary_var.set(
            f"{displayed} roots shown | {total} packages total | {marked} marked for deletion"
        )
        self.update_details()

    def subtree_matches(self, key: str, query: str, seen: set[str]) -> bool:
        if key in seen:
            return False
        seen.add(key)
        pkg = self.graph.packages[key]
        haystack = " ".join(
            [
                pkg.name,
                pkg.key,
                pkg.version,
                pkg.location,
                self.graph.role_for(key),
                " ".join(self.graph.packages[parent].name for parent in pkg.required_by),
            ]
        ).lower()
        return query in haystack or any(
            self.subtree_matches(child, query, seen.copy()) for child in sorted(pkg.dependencies)
        )

    def insert_package(self, parent_item: str, key: str, ancestors: set[str], force_expand: bool = False) -> str:
        pkg = self.graph.packages[key]
        role = self.graph.role_for(key)
        item = self.tree.insert(
            parent_item,
            tk.END,
            text=self.display_text(key),
            values=(pkg.version, role, pkg.location, self.required_by_text(key)),
            tags=self.tags_for(key),
            open=force_expand or not parent_item,
        )
        self.item_to_key[item] = key
        self.key_to_items.setdefault(key, []).append(item)

        if key in ancestors:
            self.tree.insert(item, tk.END, text="cycle detected", values=("", "", "", ""), tags=("dep",))
            return item

        next_ancestors = {*ancestors, key}
        for child_key in sorted(pkg.dependencies, key=lambda dep: self.graph.packages[dep].name.lower()):
            self.insert_package(item, child_key, next_ancestors, force_expand=force_expand)
        return item

    def display_text(self, key: str) -> str:
        pkg = self.graph.packages[key]
        checkbox = CHECKED if self.keep_state.get(key, True) else UNCHECKED
        prefix = "" if self.keep_state.get(key, True) else TRASH_PREFIX
        return f"{checkbox} {prefix}{pkg.name}"

    def tags_for(self, key: str) -> tuple[str, ...]:
        if not self.keep_state.get(key, True):
            return ("delete",)
        role = self.graph.role_for(key)
        if role == "Top-level":
            return ("top",)
        if role == "Orphan":
            return ("orphan",)
        return ("dep",)

    def required_by_text(self, key: str) -> str:
        parents = sorted(
            (self.graph.packages[parent].name for parent in self.graph.packages[key].required_by),
            key=str.lower,
        )
        if not parents:
            return "-"
        joined = ", ".join(parents)
        return joined if len(joined) <= 120 else joined[:117] + "..."

    def on_tree_click(self, event: tk.Event[tk.Misc]) -> str | None:
        region = self.tree.identify("region", event.x, event.y)
        if region != "tree":
            return None
        element = self.tree.identify_element(event.x, event.y)
        if "indicator" in element.lower():
            return None
        item = self.tree.identify_row(event.y)
        if not item or item not in self.item_to_key:
            return None
        bbox = self.tree.bbox(item, "#0")
        if bbox and event.x > bbox[0] + 34:
            return None
        self.toggle_package(self.item_to_key[item])
        return "break"

    def on_tree_double_click(self, event: tk.Event[tk.Misc]) -> str | None:
        if not self.scan_mode:
            return None
        item = self.tree.identify_row(event.y)
        if not item or item not in self.scan_items:
            return None
        
        info = self.scan_items[item]
        self.target_label = info.get("path", info.get("venv_path", "Global Environment"))
        self.python_executable = info["python"]
        
        self.previous_scan_dir = self.current_scan_dir
        self.btn_back.grid()
        
        self.scan_mode = False
        self.search_var.set("")
        self.scan_items.clear()
        
        focus = info["name"].lower() if info["type"] == "package" else None
        self.refresh_packages(focus)
        return "break"

    def toggle_package(self, key: str) -> None:
        currently_keep = self.keep_state.get(key, True)
        if currently_keep and self.graph.packages[key].required_by:
            required_by = self.required_by_text(key)
            proceed = messagebox.askyesno(
                "Dependency warning",
                f"{self.graph.packages[key].name} is required by:\n\n{required_by}\n\n"
                "Deleting it may break packages that are still installed. Mark it for deletion anyway?",
            )
            if not proceed:
                return
        self.keep_state[key] = not currently_keep
        self.refresh_items_for_key(key)
        self.update_summary()
        self.update_details()

    def refresh_items_for_key(self, key: str) -> None:
        for item in self.key_to_items.get(key, []):
            self.tree.item(item, text=self.display_text(key), tags=self.tags_for(key))

    def update_summary(self) -> None:
        marked = sum(1 for keep in self.keep_state.values() if not keep)
        self.summary_var.set(
            f"{len(self.graph.root_keys)} roots | {len(self.graph.packages)} packages total | "
            f"{marked} marked for deletion"
        )

    def update_details(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        selection = self.tree.selection()
        if not selection:
            text = "Select a package to see dependency details."
        elif self.scan_mode and selection[0] in self.scan_items:
            info = self.scan_items[selection[0]]
            if info["type"] == "venv":
                text = "\n".join([
                    f"Project: {info['name']}",
                    f"Path: {info['path']}",
                    f"Packages: {info['pkg_count']}",
                    "",
                    "Tip: Double-click to instantly manage this venv.",
                ])
            else:
                text = "\n".join([
                    f"Name: {info['name']}",
                    f"Version: {info['version']}",
                    f"Virtual Env: {info['venv']}",
                    f"Venv Path: {info['venv_path']}",
                ])
        elif selection[0] in self.item_to_key:
            key = self.item_to_key[selection[0]]
            pkg = self.graph.packages[key]
            dependencies = sorted(
                (self.graph.packages[dep].name for dep in pkg.dependencies),
                key=str.lower,
            )
            required_by = sorted(
                (self.graph.packages[parent].name for parent in pkg.required_by),
                key=str.lower,
            )
            state = "Keep" if self.keep_state.get(key, True) else "Marked for deletion"
            text = "\n".join(
                [
                    f"Name: {pkg.name}",
                    f"Version: {pkg.version}",
                    f"Role: {self.graph.role_for(key)}",
                    f"Location: {pkg.location or 'unknown'}",
                    f"State: {state}",
                    "",
                    "Dependencies:",
                    self.format_name_list(dependencies),
                    "",
                    "Required by:",
                    self.format_name_list(required_by),
                ]
            )
        else:
            text = "Select a package to see dependency details."

        self.details.configure(state="normal")
        self.details.delete("1.0", tk.END)
        self.details.insert("1.0", text)
        self.details.configure(state="disabled")

    def format_name_list(self, names: list[str]) -> str:
        if not names:
            return "  None"
        return "\n".join(f"  {name}" for name in names)

    def keep_all(self) -> None:
        for key in self.keep_state:
            self.keep_state[key] = True
        for key in self.graph.packages:
            self.refresh_items_for_key(key)
        self.update_summary()
        self.update_details()
        self.status_var.set("All packages reset to keep.")

    def clear_search(self) -> None:
        self.search_var.set("")
        self.scan_mode = False
        self.scan_items.clear()
        self.populate_tree()

    def delete_selected(self) -> None:
        marked = sorted(
            (key for key, keep in self.keep_state.items() if not keep),
            key=lambda key: self.graph.packages[key].name.lower(),
        )
        if not marked:
            messagebox.showinfo("Nothing to delete", "No packages are marked for deletion.")
            return

        risky = [key for key in marked if self.graph.packages[key].required_by]
        lines = [f"{self.graph.packages[key].name}=={self.graph.packages[key].version}" for key in marked]
        warning = ""
        if risky:
            warning = (
                "\n\nWarning: some selected packages are required by other installed packages. "
                "Uninstalling them can break those packages."
            )
        proceed = messagebox.askyesno(
            "Confirm uninstall",
            "The following packages will be uninstalled:\n\n"
            + "\n".join(lines[:30])
            + ("\n..." if len(lines) > 30 else "")
            + warning
            + "\n\nContinue?",
        )
        if not proceed:
            return

        self.set_busy(True, "Uninstalling selected packages...")
        thread = threading.Thread(target=self._delete_worker, args=(marked,), daemon=True)
        thread.start()

    def _delete_worker(self, marked: list[str]) -> None:
        names = [self.graph.packages[key].name for key in marked]
        result = subprocess.run(
            [self.python_executable, "-m", "pip", "uninstall", "-y", *names],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            output = result.stderr.strip() or result.stdout.strip() or "pip uninstall failed."
            self.after(0, lambda: self.handle_delete_error(output))
            return
        self.after(0, self.refresh_packages)

    def handle_delete_error(self, output: str) -> None:
        self.set_busy(False, "Uninstall failed.")
        messagebox.showerror("Uninstall failed", output)

    def set_busy(self, busy: bool, message: str) -> None:
        self.status_var.set(message)
        cursor = "watch" if busy else ""
        self.configure(cursor=cursor)
        self.configure_child_cursors(self, cursor)
        self.update_idletasks()

    def configure_child_cursors(self, widget: tk.Misc, cursor: str) -> None:
        for child in widget.winfo_children():
            try:
                child.configure(cursor=cursor)
            except tk.TclError:
                pass
            self.configure_child_cursors(child, cursor)


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
