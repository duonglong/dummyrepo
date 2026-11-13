"""Comparison of records for the duplicate checks: plain functions, no state.

A matching field is described by a ``(field_name, method, weight)`` triple; the values of a
record are a dict ``{field_name: value}`` read from the database. Everything here is pure, so
the batches of a check can call it on any slice of the records.
"""
import re
import unicodedata
from difflib import SequenceMatcher

METHOD_EXACT = 'exact'
METHOD_NORMALIZED = 'normalized'
METHOD_FUZZY = 'fuzzy'

# Characters of a name that carry no meaning when comparing; everything else is kept.
_NOT_ALNUM = re.compile(r'[\W_]+')
_NOT_DIGIT = re.compile(r'\D+')
# Length of the beginning of a fuzzy value used to group candidates.
BLOCK_PREFIX_LENGTH = 4


def is_empty(value):
    """Tell whether a stored value says nothing: unset or only spaces."""
    if isinstance(value, str):
        return not value.strip()
    return value is None or value is False


def normalize(value, field_name=''):
    """Return the comparison form of a value.

    Text is compared without case, accents, outer and repeated spaces and punctuation;
    e-mail addresses only lose their case and outer spaces; phone numbers keep their digits.

    Requirements: BR-DGQ-0012
    """
    if not isinstance(value, str):
        return value
    name = field_name.lower()
    if 'mail' in name:
        return value.strip().lower()
    if 'phone' in name or 'mobile' in name:
        return _NOT_DIGIT.sub('', value)
    text = unicodedata.normalize('NFKD', value.casefold())
    text = ''.join(char for char in text if not unicodedata.combining(char))
    return _NOT_ALNUM.sub(' ', text).strip()


def field_score(method, value_a, value_b, field_name=''):
    """Score one matching field from 0 to 100, or None when a side is empty.

    Requirements: BR-DGQ-0012
    """
    if is_empty(value_a) or is_empty(value_b):
        return None
    if method == METHOD_EXACT:
        return 100.0 if value_a == value_b else 0.0
    text_a, text_b = normalize(value_a, field_name), normalize(value_b, field_name)
    if method == METHOD_NORMALIZED:
        return 100.0 if text_a == text_b else 0.0
    return 100.0 * SequenceMatcher(None, text_a, text_b).ratio()


def pair_score(lines, values_a, values_b):
    """Score a pair of records: the weighted average of the field scores, or None.

    A field empty on either side is left out; when no field is left the pair has no score.

    Requirements: BR-DGQ-0012
    """
    total = weight_sum = 0.0
    for field_name, method, weight in lines:
        score = field_score(method, values_a.get(field_name), values_b.get(field_name), field_name)
        if score is not None:
            total += score * weight
            weight_sum += weight
    return total / weight_sum if weight_sum else None


def block_keys(lines, values):
    """Return the keys under which a record is a candidate for a duplicate.

    Records are only compared when they share a key, which avoids scoring every pair: the
    whole comparison form of an exact or normalized value, the first letters of a fuzzy one.

    Requirements: FR-DGQ-0039
    """
    keys = set()
    for field_name, method, _weight in lines:
        value = values.get(field_name)
        if is_empty(value):
            continue
        if method == METHOD_EXACT:
            keys.add('%s=%s' % (field_name, value))
        elif method == METHOD_NORMALIZED:
            keys.add('%s=%s' % (field_name, normalize(value, field_name)))
        else:
            prefix = normalize(value, field_name)[:BLOCK_PREFIX_LENGTH]
            if prefix:
                keys.add('%s~%s' % (field_name, prefix))
    return keys


def group_pairs(pairs):
    """Join pairs ``(id_a, id_b, score)`` into groups; return ``[(ids, best_score)]``.

    Every record linked to another by a pair ends in the same group (union-find).

    Requirements: FR-DGQ-0039
    """
    parent = {}

    def find(node):
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for id_a, id_b, _score in pairs:
        parent[find(id_a)] = find(id_b)
    members, best = {}, {}
    for id_a, id_b, score in pairs:
        root = find(id_a)
        members.setdefault(root, set()).update((id_a, id_b))
        best[root] = max(best.get(root, 0.0), score)
    return [(sorted(ids), best[root]) for root, ids in members.items()]
