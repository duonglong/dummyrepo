"""Link integrity, condition, cross-field and custom validator checks."""
import logging
import operator
from ast import literal_eval
from datetime import date, datetime

from .basic import FAIL, NOT_APPLICABLE, PASS, Outcome, field_label, is_empty, value_params

_logger = logging.getLogger(__name__)

COMPARE_OPERATORS = {
    '=': operator.eq,
    '!=': operator.ne,
    '>': operator.gt,
    '>=': operator.ge,
    '<': operator.lt,
    '<=': operator.le,
}


def _rule_fields(rule, records):
    return [records._fields[name] for name in rule.field_ids.mapped('name')]


#: models whose company is only the default of a record that may serve several companies
COMPANY_FREE_MODELS = ('res.users',)


def _link_failure(record, target):
    """The explanation code of a link that does not hold for ``record``, or None.

    ``target`` is the linked record read in sudo with archived records visible.
    """
    if not target.exists():
        return 'link_missing'
    if 'active' in target._fields and not target.active:
        return 'link_archived'
    company_field = target._fields.get('company_id')
    if (company_field is not None and company_field.comodel_name == 'res.company' and target.company_id
            and target._name not in COMPANY_FREE_MODELS):
        record_company = record.company_id if 'company_id' in record._fields else record.env['res.company']
        if record_company and target.company_id != record_company:
            return 'link_other_company'
    return None


def check_link(rule, records, batch):
    """Fail a record whose link points to a missing or archived record, or to one of another company.

    A link to a record shared by all companies is fine; an empty link is not applicable (the
    required field rules own it).

    Requirements: FR-DGQ-0006
    """
    rule_fields = _rule_fields(rule, records)
    outcomes = {}
    for record in records:
        outcome = Outcome(NOT_APPLICABLE)
        for field in rule_fields:
            targets = record[field.name]
            if not targets:
                continue
            outcome = Outcome(PASS)
            # sudo: a link to an archived or hidden record must still be judged, whatever the user may read
            for target in targets.sudo().with_context(active_test=False):
                code = _link_failure(record, target)
                if code:
                    params = value_params(batch, records, field, target.display_name if target.exists() else '')
                    outcome = Outcome(FAIL, code, params)
                    break
            if outcome.status == FAIL:
                break
        outcomes[record.id] = outcome
    return outcomes


def check_condition(rule, records, batch):
    """Fail a record of the 'applies to' filter that is outside the 'must satisfy' filter.

    A record outside the 'applies to' filter is not applicable. The filters are domain text read
    with ``literal_eval``, never run as code.

    Requirements: FR-DGQ-0007, BR-DGQ-0009
    """
    # sudo: the filters judge every record of the scan, as the scan itself reads them
    scope = records.sudo().with_context(active_test=False)
    applicable = scope.filtered_domain(literal_eval(rule.applies_domain)) if rule.applies_domain else scope
    satisfied = applicable.filtered_domain(literal_eval(rule.condition_domain))
    outcomes = {}
    for record in records:
        if record not in applicable:
            outcomes[record.id] = Outcome(NOT_APPLICABLE)
        elif record in satisfied:
            outcomes[record.id] = Outcome(PASS)
        else:
            outcomes[record.id] = Outcome(FAIL, 'condition_failed', {'model': records._name})
    return outcomes


def _comparable(value):
    """A value ready to compare: a link by its id, a date and time by its day when set against a date."""
    return value.id if hasattr(value, 'ids') else value


def _same_kind(first, second):
    """Bring a date and a date and time to the same kind so that they compare."""
    if isinstance(first, datetime) and type(second) is date:
        return first.date(), second
    if type(first) is date and isinstance(second, datetime):
        return first, second.date()
    return first, second


def check_cross_field(rule, records, batch):
    """Fail a record for which the comparison of its two fields is false; an empty field is not applicable.

    Requirements: FR-DGQ-0008
    """
    other = records._fields[rule.compare_field_id.name]
    compare = COMPARE_OPERATORS[rule.compare_operator]
    outcomes = {}
    for record in records:
        outcome = Outcome(NOT_APPLICABLE)
        other_value = record[other.name]
        for field in _rule_fields(rule, records):
            value = record[field.name]
            if is_empty(field, value) or is_empty(other, other_value):
                continue
            left, right = _same_kind(_comparable(value), _comparable(other_value))
            if compare(left, right):
                outcome = Outcome(PASS)
                continue
            params = {
                'model': records._name, 'field': field.name, 'other': other.name,
                'operator': rule.compare_operator,
            }
            if field.name in batch.shown_fields and other.name in batch.shown_fields:
                params.update(value=_display(value), other_value=_display(other_value))
            else:
                params['value_hidden'] = True
            outcome = Outcome(FAIL, 'cross_field_failed', params)
            break
        outcomes[record.id] = outcome
    return outcomes


def _display(value):
    """A value as text for an explanation: a link by its name, a day in ISO form."""
    if hasattr(value, 'ids'):
        return value.display_name
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _run_validator(handler, rule, records):
    """The validator's answer ``{record id: reason}`` for ``records``, in a savepoint."""
    with records.env.cr.savepoint():
        answer = handler(records.env, rule, records) or {}
    return {record_id: str(reason) for record_id, reason in answer.items() if record_id in records.ids}


def check_custom(rule, records, batch):
    """Run the validator an installed module registered; its error fails only the record it met.

    A key no installed module registers (or registers for another model) is never run: every
    record is not applicable. The validator is run on the whole batch; when it raises, it is run
    again record by record so that one broken record does not hide the others.

    Requirements: FR-DGQ-0009, BR-DGQ-0009
    """
    descriptor = records.env['data.quality.registry']._dgq_find('validators', rule.validator_key)
    models = (descriptor or {}).get('models')
    if descriptor is None or (models and records._name not in models):
        return dict.fromkeys(records.ids, Outcome(NOT_APPLICABLE))
    handler = descriptor['handler']
    reasons, errors = {}, {}
    try:
        reasons = _run_validator(handler, rule, records)
    except Exception:  # noqa: BLE001 - a validator is foreign code; its error is reported on the record
        for record in records:
            try:
                reasons.update(_run_validator(handler, rule, record))
            except Exception as error:  # noqa: BLE001
                _logger.warning("Validator %s failed on %s,%s", rule.validator_key, records._name, record.id,
                                exc_info=True)
                errors[record.id] = str(error)
    outcomes = {}
    for record in records:
        if record.id in errors:
            outcomes[record.id] = Outcome(FAIL, 'custom_error', {'message': errors[record.id]})
        elif record.id in reasons:
            outcomes[record.id] = Outcome(FAIL, 'custom_failed', {'message': reasons[record.id]})
        else:
            outcomes[record.id] = Outcome(PASS)
    return outcomes


CHECKERS = {
    'link': check_link,
    'condition': check_condition,
    'cross_field': check_cross_field,
    'custom': check_custom,
}


def _operator_words(env, symbol):
    return {
        '=': env._("equal to"),
        '!=': env._("different from"),
        '>': env._("greater than"),
        '>=': env._("greater than or equal to"),
        '<': env._("lower than"),
        '<=': env._("lower than or equal to"),
    }[symbol]


def explain(env, code, params):
    """The sentence of a code of the advanced checks in the language of ``env``, or None for another code.

    Requirements: FR-DGQ-0006, FR-DGQ-0007, FR-DGQ-0008, FR-DGQ-0009
    """
    if code in ('custom_failed', 'custom_error'):
        message = params['message']
        if code == 'custom_error':
            return env._("The validator failed: %(error)s", error=message)
        return message
    if code == 'condition_failed':
        return env._("The record is in the scope of the rule but does not meet its condition.")
    if code == 'cross_field_failed':
        first, second = field_label(env, params), field_label(env, params, params['other'])
        words = _operator_words(env, params['operator'])
        if params.get('value_hidden'):
            return env._("%(field)s must be %(operator)s %(other)s.", field=first, operator=words, other=second)
        return env._(
            "%(field)s is %(value)s and %(other)s is %(other_value)s, but %(field)s must be %(operator)s %(other)s.",
            field=first, value=params['value'], other=second, other_value=params['other_value'], operator=words)
    label = field_label(env, params) if params else ''
    shown = params and not params.get('value_hidden')
    if code == 'link_missing':
        return env._("%(field)s points to a record that no longer exists.", field=label)
    if code == 'link_archived':
        if shown:
            return env._("%(field)s points to %(value)s, which is archived.", field=label, value=params['value'])
        return env._("%(field)s points to an archived record.", field=label)
    if code == 'link_other_company':
        if shown:
            return env._(
                "%(field)s points to %(value)s, which belongs to another company.",
                field=label, value=params['value'])
        return env._("%(field)s points to a record that belongs to another company.", field=label)
    return None
