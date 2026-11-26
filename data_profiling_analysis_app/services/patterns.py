"""Format pattern of a text value."""
from .const import WHITESPACE


def pattern_of(text):
    """Format pattern of a text value: trimmed, each letter as A, each digit as 9, other characters kept.

    Reference implementation of the SQL expression the engine runs.

    Requirements: BR-DPA-0003
    """
    trimmed = text.strip(WHITESPACE)
    return ''.join('A' if char.isalpha() else '9' if char.isdecimal() else char for char in trimmed)
