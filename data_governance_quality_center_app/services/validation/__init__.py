"""The validation service: one definition of a failed check for scans, tests and imports.

``check`` returns, for each (record, rule), an ``Outcome``: passed, failed with an explanation
code and its parameters, or not applicable. ``explain`` renders a code in the reader's language.
"""
from odoo import fields

from . import advanced, basic
from .basic import FAIL, NOT_APPLICABLE, PASS, Batch, Outcome

# The checks by rule type; the advanced checks add their own entries.
CHECKERS = {**basic.CHECKERS, **advanced.CHECKERS}
EXPLAINERS = [basic.explain, advanced.explain]

__all__ = ['CHECKERS', 'FAIL', 'NOT_APPLICABLE', 'PASS', 'Batch', 'Outcome', 'check', 'explain']


def check(rules, records, now=None):
    """Check ``records`` against ``rules`` of their model; return ``{(record id, rule id): Outcome}``.

    A rule whose type has no check here is left out of the answer. ``now`` is the moment of the
    scan in UTC (default: now); each record's day is that moment in its company's time zone.
    Values of fields that are excluded or sensitive are never put in the parameters.

    Requirements: FR-DGQ-0002, FR-DGQ-0003, FR-DGQ-0004, FR-DGQ-0005, FR-DGQ-0006, FR-DGQ-0007,
    FR-DGQ-0008, FR-DGQ-0009
    """
    shown_fields = records.env['data.quality.field.config']._get_usable_field_names(records._name)
    batch = Batch(now or fields.Datetime.now(), shown_fields)
    outcomes = {}
    for rule in rules:
        checker = CHECKERS.get(rule.rule_type)
        if checker is None:
            continue
        for record, outcome in checker(rule, records, batch).items():
            outcomes[(record, rule.id)] = outcome
    return outcomes


def explain(env, code, params):
    """The sentence of an explanation code in the language of ``env``, or the code when unknown."""
    for explainer in EXPLAINERS:
        sentence = explainer(env, code, params)
        if sentence:
            return sentence
    return code or ''
