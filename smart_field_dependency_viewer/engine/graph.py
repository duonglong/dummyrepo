"""Reverse dependency index and chain walk, read from the running registry.

The engine only reads field definitions; it has no write path.
"""
from collections import defaultdict
from typing import Any

FieldKey = tuple[str, str]


def _method_name(field) -> str | None:
    """Return the compute method name of a computed (not related) field."""
    if field.related or not field.compute:
        return None
    return field.compute if isinstance(field.compute, str) else getattr(field.compute, "__name__", None)


def _declared_path(paths: list[str], names: list[str]) -> str | None:
    """Return the declared dotted path that the resolved field names start."""
    for path in paths:
        if path.split(".")[: len(names)] == names:
            return path
    return None


def _dependent_row(field, path: str) -> dict[str, Any]:
    return {
        "model": field.model_name,
        "field": field.name,
        "label": field.string or field.name,
        "kind": "related" if field.related else "computed",
        "stored": bool(field.store),
        "path": path,
        "method": _method_name(field),
        "related": field.related or None,
    }


def build_reverse_index(registry) -> dict[FieldKey, list[dict[str, Any]]]:
    """Map each field to the computed and related fields that depend on it.

    Only computed fields that declare dependencies and related fields are
    dependents; Odoo's own resolved dependencies are read, so the index holds
    exactly what Odoo recomputes.

    Requirements: BR-SFD-0001, FR-SFD-0002, FR-SFD-0003
    """
    index: dict[FieldKey, dict[FieldKey, dict[str, Any]]] = defaultdict(dict)
    for model_cls in registry.models.values():
        if model_cls._abstract:
            continue
        for field in model_cls._fields.values():
            paths = list(registry.field_depends.get(field, ()))
            if not (field.compute or field.related) or not paths:
                continue
            for resolved in field.resolve_depends(registry):
                names = [step.name for step in resolved]
                path = _declared_path(paths, names) or _declared_path(paths, names[:-1])
                if path is None:
                    continue
                target = resolved[-1]
                row = index[(target.model_name, target.name)].setdefault(
                    (field.model_name, field.name), _dependent_row(field, path))
                if path not in row["path"].split(", "):
                    row["path"] = f"{row['path']}, {path}"
    return {
        key: sorted(rows.values(), key=lambda row: (row["model"], row["field"]))
        for key, rows in index.items()
    }


def own_dependencies(registry, model_name: str, field_name: str) -> list[str] | None:
    """Return the dependency paths the field declares, or None for a plain field.

    Requirements: FR-SFD-0005
    """
    field = registry[model_name]._fields[field_name]
    if not (field.compute or field.related):
        return None
    return list(registry.field_depends.get(field, ())) or None


def walk(index: dict[FieldKey, list[dict[str, Any]]], start: FieldKey, limit: int) -> dict[str, list]:
    """Walk the dependents of start level by level, down to limit levels.

    A dependent already on the current path is linked but not expanded.

    Requirements: FR-SFD-0002
    """
    nodes: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []

    def add(row: dict[str, Any], level: int) -> int:
        nodes.append({**row, "id": len(nodes), "level": level})
        return len(nodes) - 1

    def expand(key: FieldKey, node_id: int, level: int, on_path: frozenset[FieldKey]) -> None:
        if level >= limit:
            return
        for row in index.get(key, ()):
            child_key = (row["model"], row["field"])
            child_id = add(row, level + 1)
            links.append({"from": node_id, "to": child_id, "path": row["path"]})
            if child_key not in on_path:
                expand(child_key, child_id, level + 1, on_path | {child_key})

    start_id = add({"model": start[0], "field": start[1]}, 0)
    expand(start, start_id, 0, frozenset({start}))
    return {"nodes": nodes, "links": links}
