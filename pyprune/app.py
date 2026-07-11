#!/usr/bin/env python3
"""PyPrune – Tkinter GUI for inspecting and pruning installed Python packages."""

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
    def __init__(self, python_executable: str | None = None, target_label: str | None = None) -> None:
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
        self.status_var = tk.StringVar(value="Ready")
        self.summary_var = tk.StringVar(value="")
        self.search_var = tk.StringVar(value="")

        default_font = font.nametofont("TkDefaultFont")
        self.deleted_font = default_font.copy()
        self.deleted_font.configure(overstrike=True)

        self._build_ui()
        self.after(150, self.refresh_packages)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        toolbar = ttk.Frame(self, padding=(12, 10, 12, 6))
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(5, weight=1)

        ttk.Button(toolbar, text="Refresh", command=self.refresh_packages).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(toolbar, text="Keep All", command=self.keep_all).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(toolbar, text="Delete Selected", command=self.delete_selected).grid(row=0, column=2, padx=(0, 16))

        ttk.Label(toolbar, text="Search").grid(row=0, column=3, padx=(0, 6))
        search = ttk.Entry(toolbar, textvariable=self.search_var, width=34)
        search.grid(row=0, column=4, sticky="ew")
        search.bind("<Return>", lambda _event: self.populate_tree())
        ttk.Button(toolbar, text="Clear", command=self.clear_search).grid(row=0, column=5, sticky="w", padx=(8, 0))

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

        columns = ("version", "role", "required_by")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Package")
        self.tree.heading("version", text="Version")
        self.tree.heading("role", text="Role")
        self.tree.heading("required_by", text="Required by")
        self.tree.column("#0", width=360, minwidth=240, stretch=True)
        self.tree.column("version", width=140, minwidth=90, stretch=False)
        self.tree.column("role", width=110, minwidth=90, stretch=False)
        self.tree.column("required_by", width=320, minwidth=180, stretch=True)
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
        self.tree.bind("<Button-1>", self.on_tree_click)
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

    def refresh_packages(self) -> None:
        self.set_busy(True, f"Loading package dependency tree for {self.target_label}...")
        thread = threading.Thread(target=self._load_graph_worker, daemon=True)
        thread.start()

    def _load_graph_worker(self) -> None:
        try:
            payload = self.run_pipdeptree()
            graph = PackageGraph.from_pipdeptree_json(payload)
        except Exception as exc:  # noqa: BLE001 - surface any subprocess/JSON issue in the GUI.
            self.after(0, lambda: self.handle_load_error(exc))
            return
        self.after(0, lambda: self.apply_graph(graph))

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

    def apply_graph(self, graph: PackageGraph) -> None:
        self.graph = graph
        self.keep_state = {key: True for key in graph.packages}
        self.populate_tree()
        self.set_busy(False, f"Loaded {len(graph.packages)} installed packages from {self.target_label}.")

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
            values=(pkg.version, role, self.required_by_text(key)),
            tags=self.tags_for(key),
            open=force_expand or not parent_item,
        )
        self.item_to_key[item] = key
        self.key_to_items.setdefault(key, []).append(item)

        if key in ancestors:
            self.tree.insert(item, tk.END, text="cycle detected", values=("", "", ""), tags=("dep",))
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
        item = self.tree.identify_row(event.y)
        if not item or item not in self.item_to_key:
            return None
        bbox = self.tree.bbox(item, "#0")
        if bbox and event.x > bbox[0] + 34:
            return None
        self.toggle_package(self.item_to_key[item])
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
        if not selection or selection[0] not in self.item_to_key:
            text = "Select a package to see dependency details."
        else:
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
                    f"State: {state}",
                    "",
                    "Dependencies:",
                    self.format_name_list(dependencies),
                    "",
                    "Required by:",
                    self.format_name_list(required_by),
                ]
            )

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
    target = Path.home() if args.root else Path(args.path or ".")
    python_executable, target_label = resolve_python_for_path(target)
    app = PackageCleanerApp(python_executable=python_executable, target_label=target_label)
    app.mainloop()


if __name__ == "__main__":
    main()
