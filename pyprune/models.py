"""Data models for PyPrune — pure data, no UI or subprocess dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PackageInfo:
    """Metadata for a single installed Python package."""

    key: str
    name: str
    version: str = "unknown"
    location: str = ""
    dependencies: set[str] = field(default_factory=set)
    required_by: set[str] = field(default_factory=set)

    def __repr__(self) -> str:
        return f"PackageInfo(key={self.key!r}, name={self.name!r}, version={self.version!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PackageInfo):
            return NotImplemented
        return self.key == other.key

    def __hash__(self) -> int:
        return hash(self.key)


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
