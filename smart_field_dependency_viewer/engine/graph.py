"""Reverse dependency index and chain walk, read from the running registry.

The engine only reads field definitions; it has no write path.
"""
import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

_logger = logging.getLogger(__name__)

FieldKey = tuple[str, str]

MAX_NODES = 1000


def _method_name(field) -> str | None:
    """Return the compute method name of a computed (not related) field."""
    if field.related or not field.compute:
        return None
    return field.compute if isinstance(field.compute, str) else getattr(field.compute, "__name__", None)


def _declared_paths(paths: list[str], names: list[str]) -> list[str]:
    """Return every declared dotted path that the resolved field names start."""
    return [path for path in paths if path.split(".")[: len(names)] == names]


def _resolved_dependencies(field, registry) -> list[tuple]:
    """Return the resolved dependency tuples of a field, as Odoo's own triggers do.

    A custom (manual) field whose dependency no longer exists is skipped and
    logged; any other field re-raises, exactly as the registry does.
    """
    try:
        return list(field.resolve_depends(registry))
    except (ValueError, KeyError) as exc:
        if not field.base_field.manual:
            raise
        _logger.warning("sfd: skipped dependencies of %s: %s", field, exc)
        return []


def field_kind(field) -> str:
    """Return "related", "computed" or "plain" for a field."""
    return "related" if field.related else "computed" if field.compute else "plain"


def field_attributes(field) -> dict[str, Any]:
    """Return what every box of the map says about its field."""
    return {
        "model": field.model_name,
        "field": field.name,
        "label": field.string or field.name,
        "kind": field_kind(field),
        "stored": bool(field.store),
        "method": _method_name(field),
        "related": field.related or None,
    }


def _dependent_row(field, path: str) -> dict[str, Any]:
    return {**field_attributes(field), "path": path}


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
            for resolved in _resolved_dependencies(field, registry):
                names = [step.name for step in resolved]
                declared = _declared_paths(paths, names) or _declared_paths(paths, names[:-1])
                if not declared:
                    continue
                target = resolved[-1]
                row = index[(target.model_name, target.name)].setdefault(
                    (field.model_name, field.name), _dependent_row(field, declared[0]))
                shown = row["path"].split(", ")
                row["path"] = ", ".join(shown + [path for path in declared if path not in shown])
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


def walk(
    index: dict[FieldKey, list[dict[str, Any]]],
    start: FieldKey,
    limit: int,
    is_recursive: Callable[[FieldKey], bool] | None = None,
) -> dict[str, Any]:
    """Walk the dependents of start level by level, down to limit levels.

    The start is level 0 and each dependent one level more than the field it
    depends on. A field met again on the same path gets a "circular" mark (or
    "recursive" when is_recursive says so) on the link back and is not
    expanded. A field at the limit that still has dependents is marked
    "too_deep" with the number of its distinct dependents. After MAX_NODES
    boxes, each branch left unexpanded is marked "too_large".

    Requirements: BR-SFD-0002, BR-SFD-0003, FR-SFD-0006, FR-SFD-0008, FR-SFD-0009
    """
    nodes: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    marks: list[dict[str, Any]] = []

    def add(row: dict[str, Any], level: int) -> int:
        nodes.append({**row, "id": len(nodes), "level": level})
        return len(nodes) - 1

    def expand(key: FieldKey, node_id: int, level: int, path: dict[FieldKey, int]) -> None:
        rows = index.get(key, ())
        if level >= limit:
            if rows:
                marks.append({"node": node_id, "type": "too_deep", "not_shown": len(rows)})
            return
        not_shown = 0
        for row in rows:
            child_key = (row["model"], row["field"])
            if child_key in path:
                links.append({"from": node_id, "to": path[child_key], "path": row["path"]})
                kind = "recursive" if is_recursive and is_recursive(child_key) else "circular"
                marks.append({"node": node_id, "type": kind, "to": path[child_key]})
            elif len(nodes) >= MAX_NODES:
                not_shown += 1
            else:
                child_id = add(row, level + 1)
                links.append({"from": node_id, "to": child_id, "path": row["path"]})
                expand(child_key, child_id, level + 1, {**path, child_key: child_id})
        if not_shown:
            marks.append({"node": node_id, "type": "too_large", "not_shown": not_shown})

    start_id = add({"model": start[0], "field": start[1]}, 0)
    expand(start, start_id, 0, {start: start_id})
    return {
        "nodes": nodes,
        "links": links,
        "marks": marks,
        "truncated": any(mark["type"] == "too_large" for mark in marks),
    }
