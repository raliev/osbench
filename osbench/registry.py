"""Policy plugin registry: decorator + entry-point loading.

Adding a policy = create a file with the class and register it: either via the
``@register_sourcing("name")`` decorator (for policies inside the package), or via the
``osbench.policies.sourcing`` entry point in pyproject.toml (for external plugins).

Entry points are loaded lazily by name: ``list_*`` shows available names without
importing their modules, ``get_*`` imports the class only on access.
"""

from __future__ import annotations

from importlib.metadata import EntryPoint, entry_points
from typing import TypeVar

_T = TypeVar("_T")

_SOURCING: dict[str, type] = {}
_REPLENISHMENT: dict[str, type] = {}
_EP_SOURCING: dict[str, EntryPoint] = {}
_EP_REPLENISHMENT: dict[str, EntryPoint] = {}
_EP_DISCOVERED = False


def register_sourcing(name: str):
    def deco(cls: type[_T]) -> type[_T]:
        if name in _SOURCING and _SOURCING[name] is not cls:
            raise ValueError(f"duplicate sourcing policy registration: {name!r}")
        cls.name = name  # type: ignore[attr-defined]
        _SOURCING[name] = cls
        return cls

    return deco


def register_replenishment(name: str):
    def deco(cls: type[_T]) -> type[_T]:
        if name in _REPLENISHMENT and _REPLENISHMENT[name] is not cls:
            raise ValueError(f"duplicate replenishment policy registration: {name!r}")
        cls.name = name  # type: ignore[attr-defined]
        _REPLENISHMENT[name] = cls
        return cls

    return deco


def _discover_entry_points() -> None:
    """Collect EntryPoint objects (without importing modules), idempotently."""
    global _EP_DISCOVERED
    if _EP_DISCOVERED:
        return
    for group, table in (
        ("osbench.policies.sourcing", _EP_SOURCING),
        ("osbench.policies.replenishment", _EP_REPLENISHMENT),
    ):
        for ep in entry_points(group=group):
            table.setdefault(ep.name, ep)
    _EP_DISCOVERED = True


def get_sourcing(name: str) -> type:
    if name in _SOURCING:
        return _SOURCING[name]
    _discover_entry_points()
    if name in _EP_SOURCING:
        cls = _EP_SOURCING[name].load()
        _SOURCING[name] = cls
        return cls
    raise KeyError(f"unknown sourcing policy {name!r}; available: {list_sourcing()}")


def get_replenishment(name: str) -> type:
    if name in _REPLENISHMENT:
        return _REPLENISHMENT[name]
    _discover_entry_points()
    if name in _EP_REPLENISHMENT:
        cls = _EP_REPLENISHMENT[name].load()
        _REPLENISHMENT[name] = cls
        return cls
    raise KeyError(f"unknown replenishment policy {name!r}; available: {list_replenishment()}")


def list_sourcing() -> list[str]:
    _discover_entry_points()
    return sorted(set(_SOURCING) | set(_EP_SOURCING))


def list_replenishment() -> list[str]:
    _discover_entry_points()
    return sorted(set(_REPLENISHMENT) | set(_EP_REPLENISHMENT))
