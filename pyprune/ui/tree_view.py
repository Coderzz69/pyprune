"""Package tree view widget for the PyPrune GUI."""

from __future__ import annotations

import tkinter as tk
from tkinter import font, ttk
from typing import Callable

from ..models import PackageGraph

TRASH_PREFIX = "\U0001f5d1\ufe0f "
CHECKED = "\u2611"
UNCHECKED = "\u2610"


class PackageTreeView(ttk.Frame):
    """Frame containing the package Treeview with scrollbars."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        on_toggle: Callable[[str], None],
        on_double_click: Callable[[str], None],
        on_selection_changed: Callable[[], None],
    ) -> None:
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self._on_toggle = on_toggle
        self._on_double_click = on_double_click
        self._on_selection_changed = on_selection_changed

        # Mappings between tree items and package keys.
        self.item_to_key: dict[str, str] = {}
        self.key_to_items: dict[str, list[str]] = {}
        self.scan_items: dict[str, dict[str, str]] = {}

        # Fonts.
        default_font = font.nametofont("TkDefaultFont")
        self._deleted_font = default_font.copy()
        self._deleted_font.configure(overstrike=True)

        # Treeview.
        columns = ("version", "role", "location", "required_by")
        self.tree = ttk.Treeview(self, columns=columns, show="tree headings", selectmode="browse")
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

        yscroll = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.tree.yview)
        xscroll = ttk.Scrollbar(self, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")

        # Tags.
        self.tree.tag_configure("top", foreground="#1a7f37")
        self.tree.tag_configure("dep", foreground="#0969da")
        self.tree.tag_configure("orphan", foreground="#bc4c00")
        self.tree.tag_configure("delete", foreground="#8c1d18", font=self._deleted_font)
        self.tree.tag_configure("venv", foreground="#6f42c1")

        # Bindings.
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_selection_changed())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Remove all items from the tree."""
        self.tree.delete(*self.tree.get_children())
        self.item_to_key.clear()
        self.key_to_items.clear()
        self.scan_items.clear()

    def get_selected_key(self) -> str | None:
        """Return the package key of the currently selected item, or None."""
        selection = self.tree.selection()
        if not selection:
            return None
        return self.item_to_key.get(selection[0])

    def get_selected_scan_item(self) -> dict[str, str] | None:
        """Return the scan item dict of the currently selected item, or None."""
        selection = self.tree.selection()
        if not selection:
            return None
        return self.scan_items.get(selection[0])

    def focus_package(self, key: str) -> None:
        """Select and scroll to the first tree item for *key*."""
        items = self.key_to_items.get(key, [])
        if items:
            self.tree.selection_set(items[0])
            self.tree.see(items[0])

    def populate_packages(
        self,
        graph: PackageGraph,
        keep_state: dict[str, bool],
        query: str = "",
    ) -> int:
        """Populate the tree from the dependency graph.  Returns the number of displayed roots."""
        self.clear()
        root_keys = graph.root_keys
        displayed = 0

        for key in root_keys:
            if query and not self._subtree_matches(graph, key, query, set()):
                continue
            self._insert_package(graph, keep_state, "", key, ancestors=set(), force_expand=bool(query))
            displayed += 1

        return displayed

    def populate_scan(
        self,
        venv_data: list[tuple[str, str, str, list[tuple[str, str]]]],
    ) -> tuple[int, int]:
        """Populate the tree with venv scan results.

        Returns ``(total_venvs, total_packages)``.
        """
        self.clear()

        for project_name, project_path, python_path, packages in venv_data:
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

        total_venvs = len(venv_data)
        total_pkgs = sum(len(pkgs) for _, _, _, pkgs in venv_data)
        return total_venvs, total_pkgs

    def refresh_item(self, key: str, graph: PackageGraph, keep_state: dict[str, bool]) -> None:
        """Update the display text and tags for all items matching *key*."""
        for item in self.key_to_items.get(key, []):
            self.tree.item(
                item,
                text=self._display_text(graph, keep_state, key),
                tags=self._tags_for(graph, keep_state, key),
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _insert_package(
        self,
        graph: PackageGraph,
        keep_state: dict[str, bool],
        parent_item: str,
        key: str,
        ancestors: set[str],
        force_expand: bool = False,
    ) -> str:
        pkg = graph.packages[key]
        role = graph.role_for(key)
        item = self.tree.insert(
            parent_item,
            tk.END,
            text=self._display_text(graph, keep_state, key),
            values=(pkg.version, role, pkg.location, _required_by_text(graph, key)),
            tags=self._tags_for(graph, keep_state, key),
            open=force_expand or not parent_item,
        )
        self.item_to_key[item] = key
        self.key_to_items.setdefault(key, []).append(item)

        if key in ancestors:
            self.tree.insert(item, tk.END, text="cycle detected", values=("", "", "", ""), tags=("dep",))
            return item

        next_ancestors = {*ancestors, key}
        for child_key in sorted(pkg.dependencies, key=lambda dep: graph.packages[dep].name.lower()):
            self._insert_package(graph, keep_state, item, child_key, next_ancestors, force_expand=force_expand)
        return item

    def _subtree_matches(self, graph: PackageGraph, key: str, query: str, seen: set[str]) -> bool:
        if key in seen:
            return False
        seen.add(key)
        pkg = graph.packages[key]
        haystack = " ".join(
            [
                pkg.name,
                pkg.key,
                pkg.version,
                pkg.location,
                graph.role_for(key),
                " ".join(graph.packages[parent].name for parent in pkg.required_by),
            ]
        ).lower()
        return query in haystack or any(
            self._subtree_matches(graph, child, query, seen.copy()) for child in sorted(pkg.dependencies)
        )

    @staticmethod
    def _display_text(graph: PackageGraph, keep_state: dict[str, bool], key: str) -> str:
        pkg = graph.packages[key]
        checkbox = CHECKED if keep_state.get(key, True) else UNCHECKED
        prefix = "" if keep_state.get(key, True) else TRASH_PREFIX
        return f"{checkbox} {prefix}{pkg.name}"

    @staticmethod
    def _tags_for(graph: PackageGraph, keep_state: dict[str, bool], key: str) -> tuple[str, ...]:
        if not keep_state.get(key, True):
            return ("delete",)
        role = graph.role_for(key)
        if role == "Top-level":
            return ("top",)
        if role == "Orphan":
            return ("orphan",)
        return ("dep",)

    def _on_tree_click(self, event: tk.Event[tk.Misc]) -> str | None:
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
        self._on_toggle(self.item_to_key[item])
        return "break"

    def _on_tree_double_click(self, event: tk.Event[tk.Misc]) -> str | None:
        item = self.tree.identify_row(event.y)
        if not item or item not in self.scan_items:
            return None
        self._on_double_click(item)
        return "break"


def _required_by_text(graph: PackageGraph, key: str) -> str:
    """Format the 'required by' column text for a package."""
    parents = sorted(
        (graph.packages[parent].name for parent in graph.packages[key].required_by),
        key=str.lower,
    )
    if not parents:
        return "-"
    joined = ", ".join(parents)
    return joined if len(joined) <= 120 else joined[:117] + "..."
