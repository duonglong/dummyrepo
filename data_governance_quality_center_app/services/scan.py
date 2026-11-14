"""Scan scope and per-batch checks, as plain functions of an environment.

A scan resolves its scope into (model, rules) pairs, reads the records of each model by
id in batches and checks them rule by rule.
"""
from ast import literal_eval

from odoo.fields import Domain

from .validation import CHECKERS
from .validation.advanced import validator_for

#: rule types the batch can check; other types are reported as not run
CHECKED_RULE_TYPES = tuple(CHECKERS)


def rule_fields(rule):
    """Technical names of the fields a rule reads, the compared field of a cross-field rule included."""
    return set(rule.field_ids.mapped('name')) | set(rule.compare_field_id.mapped('name'))


def filter_blocked_fields(env, rule):
    """Fields the rule's filters and compared field read that scans may not read, as 'model.field'.

    A filter was checked when the rule was saved; a mark set later still has to stop it.

    Requirements: FR-DGQ-0020
    """
    model = env[rule.model_name]
    marked = set(rule._dgq_marked_models().mapped('model'))
    usable_cache = {}
    blocked = set()
    for text in (rule.applies_domain, rule.condition_domain):
        if text:
            domain = Domain(literal_eval(text)).optimize(model)
            blocked |= rule._dgq_blocked_fields(model, domain, usable_cache, marked)
    compared = rule.compare_field_id
    if compared and compared.name not in env['data.quality.field.config']._get_usable_field_names(compared.model):
        blocked.add(f"{compared.model}.{compared.name}")
    return blocked


def not_run_reason(env, rule):
    """Why a rule cannot run, or ``False``: its model, a field or a filter field is left out, or its validator is gone.

    Requirements: FR-DGQ-0020, FR-DGQ-0009
    """
    model_status = env['data.quality.model.config']._get_status(rule.model_name)
    if model_status not in ('supported', 'restricted'):
        return env._("The model %(model)s is excluded from scans.", model=rule.model_id.name)
    if rule.rule_type not in CHECKED_RULE_TYPES:
        return env._("This check type cannot be scanned yet.")
    if rule.rule_type == 'custom' and not validator_for(env, rule.validator_key, rule.model_name):
        return env._("The validator is not available for the model %(model)s.", model=rule.model_id.name)
    usable = env['data.quality.field.config']._get_usable_field_names(rule.model_name)
    left_out = sorted(
        {name for name in rule_fields(rule) if name not in usable}
        | {blocked.removeprefix(rule.model_name + '.') for blocked in filter_blocked_fields(env, rule)})
    if left_out:
        return env._(
            "The field %(fields)s is excluded or sensitive and not allowed.",
            fields=", ".join(left_out))
    return False


def record_domain(model, companies, last_id):
    """Domain of the records one batch reads: after the checkpoint, in the scan's companies."""
    domain = Domain('id', '>', last_id)
    if 'company_id' in model._fields:
        domain &= Domain('company_id', 'in', [False, *companies.ids])
    return domain


def applicable_records(rule, records):
    """The records of a batch the rule is about: in the rule's companies and inside its 'applies to' filter.

    Requirements: FR-DGQ-0015
    """
    applicable = records
    if rule.company_ids:
        applicable = applicable.filtered(
            lambda record: 'company_id' in record._fields and record.company_id in rule.company_ids)
    if rule.applies_domain:
        applicable = applicable.filtered_domain(literal_eval(rule.applies_domain))
    return applicable
