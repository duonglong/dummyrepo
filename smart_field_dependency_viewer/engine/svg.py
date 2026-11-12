"""Draw a dependency map payload as one self-contained SVG string.

Every text is XML-escaped; colours and sizes are fixed here, so nothing from
a field label can reach an attribute.
"""
from collections import defaultdict
from collections.abc import Callable
from xml.sax.saxutils import escape

BOX_WIDTH = 230
BOX_HEIGHT = 62
MARK_LINE_HEIGHT = 16
COLUMN_GAP = 90
ROW_GAP = 24
MARGIN = 20

MARK_COLOURS = {
    "circular": "#c0392b",
    "recursive": "#2e6fbd",
    "too_deep": "#e67e22",
    "too_large": "#7f8c8d",
}
# keep the legend colours of sfd_dependency_map.scss equal to these
MARK_PRIORITY = ("circular", "too_large", "too_deep", "recursive")
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


def _positions(nodes: list[dict], box_height: int) -> dict[int, tuple[int, int]]:
    """Place each node in the column of its level, one row after the other."""
    rows_used: dict[int, int] = {}
    positions = {}
    for node in nodes:
        row = rows_used.get(node["level"], 0)
        rows_used[node["level"]] = row + 1
        positions[node["id"]] = (
            MARGIN + node["level"] * (BOX_WIDTH + COLUMN_GAP),
            MARGIN + row * (box_height + ROW_GAP),
        )
    return positions


def _box(node: dict, position: tuple[int, int], marks: list[dict], box_height: int,
         _: Callable[..., str]) -> str:
    """Draw one box; the border takes the most important mark, every mark has its own text line."""
    x, y = position
    main = min(marks, key=lambda mark: MARK_PRIORITY.index(mark["type"]), default=None)
    colour = MARK_COLOURS[main["type"]] if main else DEFAULT_COLOUR
    width = 3 if node["level"] == 0 else 2
    dash = ' stroke-dasharray="6 3"' if node.get("kind") == "related" else ""
    lines = [
        f'<text x="{x + 8}" y="{y + 18}" font-size="12" font-weight="bold" fill="#222">'
        f'{escape(node.get("label") or node["field"])}</text>',
        f'<text x="{x + 8}" y="{y + 34}" font-size="11" fill="#555">'
        f'{escape(node["model"])}.{escape(node["field"])}</text>',
    ]
    for index, label in enumerate(dict.fromkeys((mark["type"], _mark_label(mark, _)) for mark in marks)):
        lines.append(
            f'<text x="{x + 8}" y="{y + 52 + index * MARK_LINE_HEIGHT}" font-size="11" '
            f'font-weight="bold" fill="{MARK_COLOURS[label[0]]}">{escape(label[1])}</text>')
    return (
        f'<g><rect x="{x}" y="{y}" width="{BOX_WIDTH}" height="{box_height}" rx="4" fill="#fff" '
        f'stroke="{colour}" stroke-width="{width}"{dash}/>{"".join(lines)}</g>')


def _arrow(source: tuple[int, int], target: tuple[int, int], colour: str, box_height: int) -> str:
    """Draw a link from the right side of a box to the left side of the next one."""
    x1, y1 = source[0] + BOX_WIDTH, source[1] + box_height // 2
    x2, y2 = target[0], target[1] + box_height // 2
    if x2 <= x1:
        # a link back to an earlier box: leave from the top of the source, enter the top of the target
        x1, y1 = source[0] + BOX_WIDTH // 2, source[1]
        x2, y2 = target[0] + BOX_WIDTH // 2, target[1]
        if source == target:
            x1, x2 = x1 - BOX_WIDTH // 4, x2 + BOX_WIDTH // 4
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
    marks_of_node: dict[int, list[dict]] = defaultdict(list)
    mark_of_link = {}
    for mark in payload["marks"]:
        marks_of_node[mark["node"]].append(mark)
        if "to" in mark:
            mark_of_link.setdefault((mark["node"], mark["to"]), mark)
    box_height = BOX_HEIGHT + MARK_LINE_HEIGHT * max(
        (len({_mark_label(mark, _) for mark in marks}) - 1 for marks in marks_of_node.values()),
        default=0)
    positions = _positions(nodes, box_height)
    arrows = []
    for link in payload["links"]:
        mark = mark_of_link.get((link["from"], link["to"]))
        arrows.append(_arrow(positions[link["from"]], positions[link["to"]],
                             MARK_COLOURS[mark["type"]] if mark else DEFAULT_COLOUR, box_height))
    boxes = [_box(node, positions[node["id"]], marks_of_node[node["id"]], box_height, _)
             for node in nodes]
    width = max(x for x, _row in positions.values()) + BOX_WIDTH + MARGIN
    height = max(y for _col, y in positions.values()) + box_height + MARGIN
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="sans-serif">'
        '<defs><marker id="sfd-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#444"/>'
        '</marker></defs>'
        f'{"".join(arrows)}{"".join(boxes)}</svg>')
