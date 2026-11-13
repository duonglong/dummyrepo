"""Required field, pattern, number range and date checks."""
import re
from collections import namedtuple
from functools import lru_cache

import pytz

from odoo import fields

PASS, FAIL, NOT_APPLICABLE = 'pass', 'fail', 'not_applicable'
Outcome = namedtuple('Outcome', 'status code params', defaults=(None, None))

# Ready patterns a manager may pick instead of typing one.
PATTERN_PRESETS = {
    'email': r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}",
    'phone': r"\+?[0-9][0-9 ()./-]{5,}[0-9]",
    'url': r"https?://[^\s/$.?#][^\s]*",
}
NUMBER_TYPES = ('integer', 'float', 'monetary')
TEXT_TYPES = ('char', 'text', 'html')
ALWAYS_FILLED_TYPES = ('boolean', *NUMBER_TYPES)


def is_empty(field, value):
    """Tell whether a value of ``field`` counts as no value.

    Text of spaces, an empty list of links and an unset choice are empty; a number 0 and a
    false tick box are real values.

    Requirements: FR-DGQ-0002
    """
    if field.type in ALWAYS_FILLED_TYPES:
        return False
    if field.type in TEXT_TYPES:
        return not (value or '').strip()
    return not value


def today_of(records, today=None):
    """The day of the scan in the company's time zone."""
    if today:
        return today
    env = records.env
    tz = env.company.partner_id.tz or env.user.tz or 'UTC'
    return fields.Datetime.now().replace(tzinfo=pytz.utc).astimezone(pytz.timezone(tz)).date()


def local_date(records, field, value):
    """A date or datetime value as a day in the company's time zone."""
    if field.type != 'datetime':
        return value
    env = records.env
    tz = pytz.timezone(env.company.partner_id.tz or env.user.tz or 'UTC')
    return value.replace(tzinfo=pytz.utc).astimezone(tz).date()


@lru_cache(maxsize=256)
def compiled(pattern):
    """A compiled pattern, kept for the many records of a scan."""
    return re.compile(pattern)


def rule_pattern(rule):
    """The pattern of a rule: the typed one, else the one of its preset."""
    return rule.pattern or PATTERN_PRESETS[rule.pattern_preset]


def _fields_of(rule, records):
    return [records._fields[name] for name in rule.field_ids.mapped('name')]


def check_required(rule, records, today=None):
    """Fail a record for which any field of the rule is empty.

    Requirements: FR-DGQ-0002
    """
    outcomes = {}
    rule_fields = _fields_of(rule, records)
    for record in records:
        empty = [f.name for f in rule_fields if is_empty(f, record[f.name])]
        if empty:
            outcomes[record.id] = Outcome(FAIL, 'required_empty', {'model': records._name, 'fields': empty})
        else:
            outcomes[record.id] = Outcome(PASS)
    return outcomes


def check_pattern(rule, records, today=None):
    """Fail a record whose filled text field does not fully match the pattern; an empty field is not applicable.

    Requirements: FR-DGQ-0003
    """
    pattern = compiled(rule_pattern(rule))
    outcomes = {}
    for record in records:
        outcome = Outcome(NOT_APPLICABLE)
        for field in _fields_of(rule, records):
            value = record[field.name]
            if is_empty(field, value):
                continue
            if pattern.fullmatch(value):
                outcome = Outcome(PASS)
            else:
                outcome = Outcome(FAIL, 'pattern_mismatch', {
                    'model': records._name, 'field': field.name, 'value': value,
                    'pattern': rule_pattern(rule)})
                break
        outcomes[record.id] = outcome
    return outcomes


def check_range(rule, records, today=None):
    """Fail a record whose number is below the minimum or above the maximum (both bounds are inside).

    Requirements: FR-DGQ-0004
    """
    outcomes = {}
    for record in records:
        outcome = Outcome(PASS)
        for field in _fields_of(rule, records):
            value = record[field.name]
            params = {'model': records._name, 'field': field.name, 'value': value}
            if rule.has_min and value < rule.min_value:
                outcome = Outcome(FAIL, 'range_below', dict(params, min=rule.min_value))
            elif rule.has_max and value > rule.max_value:
                outcome = Outcome(FAIL, 'range_above', dict(params, max=rule.max_value))
            if outcome.status == FAIL:
                break
        outcomes[record.id] = outcome
    return outcomes


def date_failure(rule, value, today):
    """The explanation (code, extra parameters) of a day outside the rule's condition, or None."""
    condition = rule.date_condition
    if condition == 'not_future' and value > today:
        return 'date_future', {}
    if condition == 'not_past' and value < today:
        return 'date_past', {}
    if condition == 'not_older' and (today - value).days > rule.date_days:
        return 'date_older', {'days': rule.date_days}
    if condition == 'not_ahead' and (value - today).days > rule.date_days:
        return 'date_ahead', {'days': rule.date_days}
    if condition == 'between' and not rule.date_from <= value <= rule.date_to:
        return 'date_between', {'from': rule.date_from.isoformat(), 'to': rule.date_to.isoformat()}
    return None


def check_date(rule, records, today=None):
    """Fail a record whose filled date falls outside the rule's condition; no date is not applicable.

    Requirements: FR-DGQ-0005
    """
    today = today_of(records, today)
    outcomes = {}
    for record in records:
        outcome = Outcome(NOT_APPLICABLE)
        for field in _fields_of(rule, records):
            if not record[field.name]:
                continue
            value = local_date(records, field, record[field.name])
            failure = date_failure(rule, value, today)
            if failure:
                code, extra = failure
                outcome = Outcome(FAIL, code, dict(
                    extra, model=records._name, field=field.name, value=value.isoformat()))
                break
            outcome = Outcome(PASS)
        outcomes[record.id] = outcome
    return outcomes


CHECKERS = {
    'required': check_required,
    'pattern': check_pattern,
    'range': check_range,
    'date': check_date,
}


def number_text(value):
    """A number without a useless decimal part: 12000.0 reads 12000."""
    return str(int(value)) if value == int(value) else str(value)


def field_label(env, params, name=None):
    """The translated label of a field named in the parameters."""
    field = env[params['model']]._fields[name or params['field']]
    return field._description_string(env)


def explain(env, code, params):
    """The sentence of a code of the basic checks in the language of ``env``, or None for another code.

    Requirements: FR-DGQ-0002, FR-DGQ-0003, FR-DGQ-0004, FR-DGQ-0005
    """
    if code == 'required_empty':
        labels = ', '.join(field_label(env, params, name) for name in params['fields'])
        return env._("%(fields)s is empty.", fields=labels)
    if code not in ('pattern_mismatch', 'range_below', 'range_above') and not (code or '').startswith('date_'):
        return None
    label = field_label(env, params)
    if code == 'pattern_mismatch':
        return env._(
            "%(field)s has the value '%(value)s', which does not match the expected format %(pattern)s.",
            field=label, value=params['value'], pattern=params['pattern'])
    if code == 'range_below':
        return env._(
            "%(field)s is %(value)s, below the minimum of %(limit)s.",
            field=label, value=number_text(params['value']), limit=number_text(params['min']))
    if code == 'range_above':
        return env._(
            "%(field)s is %(value)s, above the maximum of %(limit)s.",
            field=label, value=number_text(params['value']), limit=number_text(params['max']))
    value = params['value']
    sentences = {
        'date_future': env._("%(field)s is %(value)s, which is in the future.", field=label, value=value),
        'date_past': env._("%(field)s is %(value)s, which is in the past.", field=label, value=value),
        'date_older': env._(
            "%(field)s is %(value)s, which is older than the limit of %(days)s days.",
            field=label, value=value, days=params.get('days')),
        'date_ahead': env._(
            "%(field)s is %(value)s, which is more than %(days)s days ahead.",
            field=label, value=value, days=params.get('days')),
        'date_between': env._(
            "%(field)s is %(value)s, outside the dates from %(start)s to %(end)s.",
            field=label, value=value, start=params.get('from'), end=params.get('to')),
    }
    return sentences.get(code)
