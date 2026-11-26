"""Quartiles and the usual range of a number field."""
from .const import USUAL_RANGE_FACTOR


def quartiles(values):
    """First quartile, median and third quartile of the values, as a spreadsheet's QUARTILE.INC.

    Reference implementation the SQL percentiles are tested against.

    Requirements: BR-DPA-0004
    """
    ordered = sorted(values)
    last = len(ordered) - 1

    def at(fraction):
        position = fraction * last
        below = int(position)
        above = min(below + 1, last)
        return ordered[below] + (ordered[above] - ordered[below]) * (position - below)

    return at(0.25), at(0.5), at(0.75)


def usual_range(q1, q3):
    """Bounds of the usual range: a bound itself is usual.

    Requirements: BR-DPA-0004
    """
    gap = q3 - q1
    return q1 - USUAL_RANGE_FACTOR * gap, q3 + USUAL_RANGE_FACTOR * gap
