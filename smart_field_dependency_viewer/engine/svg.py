"""Draw a dependency map payload as one self-contained SVG string.

Every text is XML-escaped; colours and sizes are fixed here, so nothing from
a field label can reach an attribute.
"""
from collections.abc import Callable
from xml.sax.saxutils import escape

BOX_WIDTH = 230
BOX_HEIGHT = 62
COLUMN_GAP = 90
ROW_GAP = 24
MARGIN = 20

MARK_COLOURS = {
    "circular": "#c0392b",
    "recursive": "#2e6fbd",
    "too_deep": "#e67e22",
    "too_large": "#7f8c8d",
}
DEFAULT_COLOUR = "#444444"


def _mark_label(mark: dict, _: Callable[..., str]) -> str:
    """Return the text of a mark, shown inside the box; _ translates a source string."""
    count = mark.get("not_shown", 0)
    shown = _("1 dependent not shown") if count == 1 else _("%s dependents not shown", count)
    return {
        "circular": lambda: _("circular"),
        "recursive": lambda: _("recursive by design"),
        "too_deep": lambda: _("too deep: %s", shown),
        "too_large": lambda: _("map too large: %s", shown),
    }[mark["type"]]()


def _positions(nodes: list[dict]) -> dict[int, tuple[int, int]]:
    """Place each node in the column of its level, one row after the other."""
    rows_used: dict[int, int] = {}
    positions = {}
    for node in nodes:
        row = rows_used.get(node["level"], 0)
        rows_used[node["level"]] = row + 1
        positions[node["id"]] = (
            MARGIN + node["level"] * (BOX_WIDTH + COLUMN_GAP),
            MARGIN + row * (BOX_HEIGHT + ROW_GAP),
        )
    return positions


def _box(node: dict, position: tuple[int, int], mark: dict | None, _: Callable[..., str]) -> str:
    x, y = position
    colour = MARK_COLOURS[mark["type"]] if mark else DEFAULT_COLOUR
    width = 3 if node["level"] == 0 else 2
    dash = ' stroke-dasharray="6 3"' if node.get("kind") == "related" else ""
    lines = [
        f'<text x="{x + 8}" y="{y + 18}" font-size="12" font-weight="bold" fill="#222">'
        f'{escape(node.get("label") or node["field"])}</text>',
        f'<text x="{x + 8}" y="{y + 34}" font-size="11" fill="#555">'
        f'{escape(node["model"])}.{escape(node["field"])}</text>',
    ]
    if mark:
        lines.append(
            f'<text x="{x + 8}" y="{y + 52}" font-size="11" font-weight="bold" fill="{colour}">'
            f'{escape(_mark_label(mark, _))}</text>')
    return (
        f'<g><rect x="{x}" y="{y}" width="{BOX_WIDTH}" height="{BOX_HEIGHT}" rx="4" fill="#fff" '
        f'stroke="{colour}" stroke-width="{width}"{dash}/>{"".join(lines)}</g>')


def _arrow(source: tuple[int, int], target: tuple[int, int], colour: str) -> str:
    """Draw a link from the right side of a box to the left side of the next one."""
    x1, y1 = source[0] + BOX_WIDTH, source[1] + BOX_HEIGHT // 2
    x2, y2 = target[0], target[1] + BOX_HEIGHT // 2
    if x2 <= x1:
        # a link back to an earlier box: leave from the top of the source, enter the top of the target
        x1, y1 = source[0] + BOX_WIDTH // 2, source[1]
        x2, y2 = target[0] + BOX_WIDTH // 2, target[1]
        lift = min(y1, y2) - ROW_GAP
        path = f"M{x1},{y1} C{x1},{lift} {x2},{lift} {x2},{y2}"
    else:
        middle = (x1 + x2) // 2
        path = f"M{x1},{y1} C{middle},{y1} {middle},{y2} {x2},{y2}"
    return (f'<path d="{path}" fill="none" stroke="{colour}" stroke-width="1.5" '
            f'marker-end="url(#sfd-arrow)"/>')


def render_svg(payload: dict, _: Callable[..., str]) -> str:
    """Return the SVG of a map payload, or an empty string when it has no boxes.

    _ translates a source string with the caller's language (env._).

    One column per level, one box per node, an arrow from each field to the
    fields that depend on it; every mark is a coloured box border with a text
    label so it reads without colour and in the exported image.

    Requirements: FR-SFD-0006, FR-SFD-0008, FR-SFD-0009
    """
    nodes = payload["nodes"]
    if not nodes:
        return ""
    positions = _positions(nodes)
    mark_of_node = {}
    for mark in payload["marks"]:
        mark_of_node.setdefault(mark["node"], mark)
    arrows = []
    for link in payload["links"]:
        mark = next((m for m in payload["marks"]
                     if m["node"] == link["from"] and m.get("to") == link["to"]), None)
        arrows.append(_arrow(positions[link["from"]], positions[link["to"]],
                             MARK_COLOURS[mark["type"]] if mark else DEFAULT_COLOUR))
    boxes = [_box(node, positions[node["id"]], mark_of_node.get(node["id"]), _) for node in nodes]
    width = max(x for x, _row in positions.values()) + BOX_WIDTH + MARGIN
    height = max(y for _col, y in positions.values()) + BOX_HEIGHT + MARGIN
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="sans-serif">'
        '<defs><marker id="sfd-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#444"/>'
        '</marker></defs>'
        f'{"".join(arrows)}{"".join(boxes)}</svg>')
