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


def record_domain(model, companies, last_id, rules):
    """Domain of the records one batch reads: after the checkpoint, in the companies the rules are about.

    A record with no company is read only when a rule is not limited to companies; a rule
    limited to companies never applies to it.

    Requirements: FR-DGQ-0016
    """
    domain = Domain('id', '>', last_id)
    if 'company_id' in model._fields:
        if all(rule.company_ids for rule in rules):
            allowed = companies & rules.company_ids
            domain &= Domain('company_id', 'in', allowed.ids)
        else:
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


def batch_counts(rules, records, outcomes, covered, status):
    """Counts of one batch by (company id, rule id); rule id 0 is the model row.

    ``outcomes`` maps a rule to its (record id, rule id) results, ``covered`` is the ids of the
    records some rule applied to, and ``status`` holds the not-applicable and fail markers.
    A record counts once per model row however many rules it fails.

    Requirements: FR-DGQ-0019
    """
    not_applicable, fail = status
    company_of = {
        record.id: record.company_id.id if 'company_id' in record._fields else False for record in records}
    counts = {}

    def row(record_id, rule_id):
        return counts.setdefault((company_of[record_id], rule_id), dict.fromkeys(
            ('records_scanned', 'records_applicable', 'records_failed', 'records_failing',
             'records_error', 'records_warning'), 0))

    for record_id in covered:
        row(record_id, 0)['records_scanned'] += 1
    severities = {}
    for rule in rules:
        for (record_id, _rule_id), outcome in outcomes[rule].items():
            if outcome.status == not_applicable:
                continue
            tally = row(record_id, rule.id)
            tally.update(rule_version=rule.version, severity=rule.severity)
            tally['records_applicable'] += 1
            if outcome.status == fail:
                tally['records_failed'] += 1
                severities.setdefault(record_id, set()).add(rule.severity)
    for record_id, kinds in severities.items():
        model_row = row(record_id, 0)
        model_row['records_failing'] += 1
        model_row['records_error'] += 'critical' in kinds
        model_row['records_warning'] += 'warning' in kinds
    return counts
