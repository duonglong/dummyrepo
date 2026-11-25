"""The readable name of a record that has no name field of its own.

Requirements: FR-SPM-0083
"""


def join_name(parts, fallback):
    """Return the parts that are set, joined with dashes, or ``fallback`` when none is.

    A record that has no name field shows these in breadcrumbs, pickers and titles.
    """
    return " - ".join(str(part) for part in parts if part) or fallback
