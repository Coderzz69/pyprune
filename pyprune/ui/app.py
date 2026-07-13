"""PackageCleanerApp — main application window orchestrating all UI components."""

from __future__ import annotations

import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from ..models import PackageGraph
from ..scanner import find_venvs, resolve_search_path
from ..subprocess_runner import (
    SubprocessError,
    fetch_locations,
    pip_list_packages,
    pip_uninstall,
    run_pipdeptree,
)
from .detail_panel import DetailPanel
from .toolbar import LegendFrame, ToolbarFrame
from .tree_view import PackageTreeView

APP_TITLE = "PyPrune – Python Package Dependency Cleaner"


class PackageCleanerApp(tk.Tk):
    """Main application window."""

    def __init__(
        self,
        python_executable: str | None = None,
        target_label: str | None = None,
        scan_on_start: Path | None = None,
    ) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1180x760")
        self.minsize(900, 560)

        self.python_executable = python_executable or sys.executable
        self.target_label = target_label or self.python_executable
        self.graph = PackageGraph({})
        self.keep_state: dict[str, bool] = {}
        self.scan_mode = False
        self.current_scan_dir: Path | None = None
        self.previous_scan_dir: Path | None = None
        self.venv_data: list[tuple[str, str, str, list[tuple[str, str]]]] = []

        self.status_var = tk.StringVar(value="Ready")
        self.summary_var = tk.StringVar(value="")
        self.search_var = tk.StringVar(value="")

        self._build_ui()

        if scan_on_start is not None:
            self.after(150, lambda: self.scan_directory(scan_on_start))
        else:
            self.after(150, self.refresh_packages)

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # Toolbar.
        self.toolbar = ToolbarFrame(
            self,
            on_refresh=self.refresh_packages,
            on_keep_all=self.keep_all,
            on_delete_selected=self.delete_selected,
            on_search=self.on_search,
            on_clear_search=self.clear_search,
            search_var=self.search_var,
        )
        self.toolbar.grid(row=0, column=0, sticky="ew")

        # Legend.
        self.legend = LegendFrame(self, self.summary_var)
        self.legend.grid(row=1, column=0, sticky="ew")

        # Content pane.
        content = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        content.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 8))

        self.tree_view = PackageTreeView(
            content,
            on_toggle=self.toggle_package,
            on_double_click=self._on_scan_double_click,
            on_selection_changed=self.update_details,
        )
        self.detail_panel = DetailPanel(content)

        content.add(self.tree_view, weight=4)
        content.add(self.detail_panel, weight=2)

        # Status bar.
        status_bar = ttk.Frame(self, padding=(12, 0, 12, 10))
        status_bar.grid(row=3, column=0, sticky="ew")
        ttk.Label(status_bar, textvariable=self.status_var).grid(row=0, column=0, sticky="w")

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def go_back(self) -> None:
        if self.previous_scan_dir:
            self.scan_directory(self.previous_scan_dir)
            self.toolbar.hide_back()
            self.previous_scan_dir = None

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def on_search(self) -> None:
        """Handle search: filter packages or scan a directory for venvs."""
        query = self.search_var.get().strip()
        path = resolve_search_path(query)
        if path is not None:
            self.scan_directory(path)
        else:
            self.scan_mode = False
            self.populate_tree()

    def clear_search(self) -> None:
        self.search_var.set("")
        self.scan_mode = False
        self.tree_view.scan_items.clear()
        self.populate_tree()

    # ------------------------------------------------------------------
    # Directory Scanning
    # ------------------------------------------------------------------

    def scan_directory(self, directory: Path) -> None:
        """Scan a directory for virtual environments."""
        self.current_scan_dir = directory
        self.set_busy(True, f"Scanning {directory} for virtual environments...")
        thread = threading.Thread(target=self._scan_worker, args=(directory,), daemon=True)
        thread.start()

    def _scan_worker(self, directory: Path) -> None:
        """Background worker to scan directory for venvs and load their packages."""
        self.after(0, lambda: self.status_var.set(f"Searching for virtual environments in {directory}..."))
        venvs = find_venvs(directory)

        venv_results: list[tuple[str, str, str, list[tuple[str, str]]]] = []

        # Load Global/System packages first.
        self.after(0, lambda: self.status_var.set("Loading Global packages..."))
        try:
            packages = pip_list_packages(sys.executable)
            if packages:
                venv_results.append(("🌐 Global (System/User)", "Global Environment", sys.executable, packages))
        except SubprocessError:
            pass

        if not venvs and len(venv_results) == 0:
            self.after(0, lambda: self._handle_no_venvs(directory))
            return

        if not venvs:
            self.after(0, lambda: messagebox.showinfo("No venvs found", f"No .venv or venv directories found in:\n{directory}"))

        if venvs:
            self.after(0, lambda: self.status_var.set(f"Found {len(venvs)} venv(s), loading packages..."))

        for i, (project_name, _project_path, python_path) in enumerate(venvs, 1):
            self.after(0, lambda n=project_name, c=i, t=len(venvs): self.status_var.set(
                f"Loading packages from {n} ({c}/{t})..."
            ))
            try:
                packages = pip_list_packages(python_path)
                if packages:
                    venv_results.append((project_name, _project_path, python_path, packages))
            except SubprocessError:
                continue

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
        total_venvs, total_pkgs = self.tree_view.populate_scan(results)
        self.summary_var.set(f"{total_venvs} venvs found | {total_pkgs} packages total")
        self.update_details()
        self.set_busy(False, f"Found {len(venvs)} virtual environment(s) in {directory} (plus Global).")

    def _on_scan_double_click(self, item: str) -> None:
        """Handle double-click on a scan item to drill into a venv."""
        if not self.scan_mode:
            return
        info = self.tree_view.scan_items.get(item)
        if not info:
            return

        self.target_label = info.get("path", info.get("venv_path", "Global Environment"))
        self.python_executable = info["python"]

        self.previous_scan_dir = self.current_scan_dir
        self.toolbar.show_back(self.go_back)

        self.scan_mode = False
        self.search_var.set("")
        self.tree_view.scan_items.clear()

        focus = info["name"].lower() if info["type"] == "package" else None
        self.refresh_packages(focus)

    # ------------------------------------------------------------------
    # Package Loading
    # ------------------------------------------------------------------

    def refresh_packages(self, focus_package: str | None = None) -> None:
        if self.scan_mode and self.current_scan_dir:
            self.scan_directory(self.current_scan_dir)
            return

        self.set_busy(True, f"Loading package dependency tree for {self.target_label}...")
        thread = threading.Thread(target=self._load_graph_worker, args=(focus_package,), daemon=True)
        thread.start()

    def _load_graph_worker(self, focus_package: str | None = None) -> None:
        try:
            payload = run_pipdeptree(self.python_executable, ask_install=self._ask_install_pipdeptree)
            graph = PackageGraph.from_pipdeptree_json(payload)
            fetch_locations(self.python_executable, graph)
        except (SubprocessError, RuntimeError) as exc:
            self.after(0, lambda exc=exc: self.handle_load_error(exc))
            return
        self.after(0, lambda: self.apply_graph(graph, focus_package))

    def _ask_install_pipdeptree(self) -> bool:
        """Ask the user whether to install pipdeptree (thread-safe)."""
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

    def apply_graph(self, graph: PackageGraph, focus_package: str | None = None) -> None:
        self.graph = graph
        self.keep_state = {k: self.keep_state.get(k, True) for k in self.graph.packages}
        self.populate_tree()
        self.set_busy(
            False,
            f"Loaded {len(self.graph.packages)} installed packages from {self.target_label}.",
        )
        if focus_package and focus_package in self.tree_view.key_to_items:
            self.tree_view.focus_package(focus_package)
            self.update_details()

    def handle_load_error(self, exc: Exception) -> None:
        self.set_busy(False, "Unable to load packages.")
        messagebox.showerror("Package discovery failed", str(exc))

    # ------------------------------------------------------------------
    # Tree population
    # ------------------------------------------------------------------

    def populate_tree(self) -> None:
        query = self.search_var.get().strip().lower()
        displayed = self.tree_view.populate_packages(self.graph, self.keep_state, query)
        total = len(self.graph.packages)
        marked = sum(1 for keep in self.keep_state.values() if not keep)
        self.summary_var.set(
            f"{displayed} roots shown | {total} packages total | {marked} marked for deletion"
        )
        self.update_details()

    # ------------------------------------------------------------------
    # Package actions
    # ------------------------------------------------------------------

    def toggle_package(self, key: str) -> None:
        currently_keep = self.keep_state.get(key, True)
        if currently_keep and self.graph.packages[key].required_by:
            from .tree_view import _required_by_text

            required_by = _required_by_text(self.graph, key)
            proceed = messagebox.askyesno(
                "Dependency warning",
                f"{self.graph.packages[key].name} is required by:\n\n{required_by}\n\n"
                "Deleting it may break packages that are still installed. Mark it for deletion anyway?",
            )
            if not proceed:
                return
        self.keep_state[key] = not currently_keep
        self.tree_view.refresh_item(key, self.graph, self.keep_state)
        self._update_summary()
        self.update_details()

    def keep_all(self) -> None:
        for key in self.keep_state:
            self.keep_state[key] = True
        for key in self.graph.packages:
            self.tree_view.refresh_item(key, self.graph, self.keep_state)
        self._update_summary()
        self.update_details()
        self.status_var.set("All packages reset to keep.")

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
        try:
            pip_uninstall(self.python_executable, names)
        except SubprocessError as exc:
            self.after(0, lambda exc=exc: self._handle_delete_error(str(exc)))
            return
        self.after(0, self.refresh_packages)

    def _handle_delete_error(self, output: str) -> None:
        self.set_busy(False, "Uninstall failed.")
        messagebox.showerror("Uninstall failed", output)

    # ------------------------------------------------------------------
    # Details panel
    # ------------------------------------------------------------------

    def update_details(self) -> None:
        if self.scan_mode:
            info = self.tree_view.get_selected_scan_item()
            if info:
                self.detail_panel.show_scan_item(info)
            else:
                self.detail_panel.show_placeholder()
        else:
            key = self.tree_view.get_selected_key()
            if key:
                self.detail_panel.show_package(self.graph, key, self.keep_state)
            else:
                self.detail_panel.show_placeholder()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _update_summary(self) -> None:
        marked = sum(1 for keep in self.keep_state.values() if not keep)
        self.summary_var.set(
            f"{len(self.graph.root_keys)} roots | {len(self.graph.packages)} packages total | "
            f"{marked} marked for deletion"
        )

    def set_busy(self, busy: bool, message: str) -> None:
        self.status_var.set(message)
        cursor = "watch" if busy else ""
        self.configure(cursor=cursor)
        self._configure_child_cursors(self, cursor)
        self.update_idletasks()

    def _configure_child_cursors(self, widget: tk.Misc, cursor: str) -> None:
        for child in widget.winfo_children():
            try:
                child.configure(cursor=cursor)
            except tk.TclError:
                pass
            self._configure_child_cursors(child, cursor)
