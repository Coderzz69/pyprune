"""Detail panel widget for the PyPrune GUI."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..models import PackageGraph


class DetailPanel(ttk.Frame):
    """Side panel displaying detailed info for the selected package or scan item."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, padding=(12, 0, 0, 0))
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        ttk.Label(self, text="Package Details", font=("TkDefaultFont", 12, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 6)
        )
        self._text = tk.Text(self, height=12, wrap="word", state="disabled", padx=8, pady=8)
        self._text.grid(row=1, column=0, sticky="nsew")

    def show_text(self, text: str) -> None:
        """Display arbitrary text in the detail panel."""
        self._text.configure(state="normal")
        self._text.delete("1.0", tk.END)
        self._text.insert("1.0", text)
        self._text.configure(state="disabled")

    def show_package(self, graph: PackageGraph, key: str, keep_state: dict[str, bool]) -> None:
        """Display details for a dependency-graph package."""
        pkg = graph.packages[key]
        dependencies = sorted(
            (graph.packages[dep].name for dep in pkg.dependencies),
            key=str.lower,
        )
        required_by = sorted(
            (graph.packages[parent].name for parent in pkg.required_by),
            key=str.lower,
        )
        state = "Keep" if keep_state.get(key, True) else "Marked for deletion"
        text = "\n".join(
            [
                f"Name: {pkg.name}",
                f"Version: {pkg.version}",
                f"Role: {graph.role_for(key)}",
                f"Location: {pkg.location or 'unknown'}",
                f"State: {state}",
                "",
                "Dependencies:",
                _format_name_list(dependencies),
                "",
                "Required by:",
                _format_name_list(required_by),
            ]
        )
        self.show_text(text)

    def show_scan_item(self, info: dict[str, str]) -> None:
        """Display details for a scanned venv or package."""
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
        self.show_text(text)

    def show_placeholder(self) -> None:
        """Show the default placeholder text."""
        self.show_text("Select a package to see dependency details.")


def _format_name_list(names: list[str]) -> str:
    if not names:
        return "  None"
    return "\n".join(f"  {name}" for name in names)
