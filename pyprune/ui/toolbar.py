"""Toolbar and legend bar widgets for the PyPrune GUI."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable


class ToolbarFrame(ttk.Frame):
    """Top toolbar with action buttons and search bar."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_refresh: Callable[[], None],
        on_keep_all: Callable[[], None],
        on_delete_selected: Callable[[], None],
        on_search: Callable[[], None],
        on_clear_search: Callable[[], None],
        search_var: tk.StringVar,
    ) -> None:
        super().__init__(parent, padding=(12, 10, 12, 6))
        self.columnconfigure(6, weight=1)

        self.btn_back = ttk.Button(self, text="◀ Back")
        self.btn_back.grid(row=0, column=0, padx=(0, 8))
        self.btn_back.grid_remove()

        ttk.Button(self, text="Refresh", command=on_refresh).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(self, text="Keep All", command=on_keep_all).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(self, text="Delete Selected", command=on_delete_selected).grid(row=0, column=3, padx=(0, 16))

        ttk.Label(self, text="Search").grid(row=0, column=4, padx=(0, 6))
        search = ttk.Entry(self, textvariable=search_var, width=34)
        search.grid(row=0, column=5, sticky="ew")
        search.bind("<Return>", lambda _event: on_search())
        ttk.Button(self, text="Clear", command=on_clear_search).grid(row=0, column=6, sticky="w", padx=(8, 0))

    def show_back(self, command: Callable[[], None]) -> None:
        """Show the back button with the given command."""
        self.btn_back.configure(command=command)
        self.btn_back.grid()

    def hide_back(self) -> None:
        """Hide the back button."""
        self.btn_back.grid_remove()


class LegendFrame(ttk.Frame):
    """Legend bar showing role colour codes and a summary label."""

    def __init__(self, parent: tk.Misc, summary_var: tk.StringVar) -> None:
        super().__init__(parent, padding=(12, 0, 12, 6))

        _legend_label(self, "Top-level", "#1a7f37").grid(row=0, column=0, padx=(0, 16))
        _legend_label(self, "Dependency", "#0969da").grid(row=0, column=1, padx=(0, 16))
        _legend_label(self, "Orphan", "#bc4c00").grid(row=0, column=2, padx=(0, 16))
        ttk.Label(self, textvariable=summary_var).grid(row=0, column=3, sticky="w")


def _legend_label(parent: ttk.Frame, text: str, color: str) -> tk.Label:
    return tk.Label(parent, text=text, fg=color)
