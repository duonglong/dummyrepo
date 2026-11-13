"""The validation service: one definition of a failed check for scans, tests and imports.

``check`` returns, for each (record, rule), an ``Outcome``: passed, failed with an explanation
code and its parameters, or not applicable. ``explain`` renders a code in the reader's language.
"""
from . import basic
from .basic import FAIL, NOT_APPLICABLE, PASS, Outcome

# The checks by rule type; the advanced checks add their own entries.
CHECKERS = dict(basic.CHECKERS)
EXPLAINERS = [basic.explain]

__all__ = ['CHECKERS', 'FAIL', 'NOT_APPLICABLE', 'PASS', 'Outcome', 'check', 'explain']


def check(rules, records, today=None):
    """Check ``records`` against ``rules`` of their model; return ``{(record id, rule id): Outcome}``.

    A rule whose type has no check here is left out of the answer. ``today`` is the day of the
    scan; it defaults to the current day in the company's time zone.

    Requirements: FR-DGQ-0002, FR-DGQ-0003, FR-DGQ-0004, FR-DGQ-0005
    """
    outcomes = {}
    for rule in rules:
        checker = CHECKERS.get(rule.rule_type)
        if checker is None:
            continue
        for record, outcome in checker(rule, records, today=today).items():
            outcomes[(record, rule.id)] = outcome
    return outcomes


def explain(env, code, params):
    """The sentence of an explanation code in the language of ``env``, or the code when unknown."""
    for explainer in EXPLAINERS:
        sentence = explainer(env, code, params)
        if sentence:
            return sentence
    return code or ''
