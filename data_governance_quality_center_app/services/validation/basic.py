"""Required field, pattern, number range and date checks."""
import re
from collections import namedtuple
from functools import lru_cache

import pytz
from odoo.tools.mail import is_html_empty

PASS, FAIL, NOT_APPLICABLE = 'pass', 'fail', 'not_applicable'
Outcome = namedtuple('Outcome', 'status code params', defaults=(None, None))
# What one call of the service shares across its rules: the moment of the scan (UTC) and the
# names of the fields whose values may be written in an explanation.
Batch = namedtuple('Batch', 'now shown_fields')

# Ready patterns a manager may pick instead of typing one.
PATTERN_PRESETS = {
    'email': r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}",
    'phone': r"\+?[0-9][0-9 ()./-]{5,}[0-9]",
    'url': r"https?://[^\s/$.?#][^\s]*",
}
NUMBER_TYPES = ('integer', 'float', 'monetary')
TEXT_TYPES = ('char', 'text')
ALWAYS_FILLED_TYPES = ('boolean', *NUMBER_TYPES)


def is_empty(field, value):
    """Tell whether a value of ``field`` counts as no value.

    Text of spaces, rich text holding only empty markup, an empty list of links and an unset
    choice are empty; a number 0 and a false tick box are real values.

    Requirements: FR-DGQ-0002
    """
    if field.type in ALWAYS_FILLED_TYPES:
        return False
    if field.type == 'html':
        return is_html_empty(value)
    if field.type in TEXT_TYPES:
        return not (value or '').strip()
    return not value


def zone_names(records):
    """The time zone of each record: its company's, else the current company's, else the user's, else UTC."""
    env = records.env
    default = env.company.partner_id.tz or env.user.tz or 'UTC'
    company_field = records._fields.get('company_id')
    if company_field is None or company_field.comodel_name != 'res.company':
        return dict.fromkeys(records.ids, default)
    return {record.id: record.company_id.partner_id.tz or default for record in records}


def local_day(moment, zone_name):
    """The day of a UTC moment in a time zone."""
    return pytz.utc.localize(moment).astimezone(pytz.timezone(zone_name)).date()


@lru_cache(maxsize=256)
def compiled(pattern):
    """A compiled pattern, kept for the many records of a scan."""
    return re.compile(pattern)


def rule_pattern(rule):
    """The pattern of a rule: the typed one, else the one of its preset."""
    return rule.pattern or PATTERN_PRESETS[rule.pattern_preset]


def _fields_of(rule, records):
    return [records._fields[name] for name in rule.field_ids.mapped('name')]


def value_params(batch, records, field, value):
    """The parameters naming a checked field and its value, the value hidden when the field may not be shown."""
    params = {'model': records._name, 'field': field.name}
    if field.name in batch.shown_fields:
        params['value'] = value
    else:
        params['value_hidden'] = True
    return params


def check_required(rule, records, batch):
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


def check_pattern(rule, records, batch):
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
                outcome = Outcome(FAIL, 'pattern_mismatch', dict(
                    value_params(batch, records, field, value), pattern=rule_pattern(rule)))
                break
        outcomes[record.id] = outcome
    return outcomes


def check_range(rule, records, batch):
    """Fail a record whose number is below the minimum or above the maximum (both bounds are inside).

    Requirements: FR-DGQ-0004
    """
    outcomes = {}
    for record in records:
        outcome = Outcome(PASS)
        for field in _fields_of(rule, records):
            value = record[field.name]
            if rule.has_min and value < rule.min_value:
                outcome = Outcome(FAIL, 'range_below', dict(
                    value_params(batch, records, field, value), min=rule.min_value))
            elif rule.has_max and value > rule.max_value:
                outcome = Outcome(FAIL, 'range_above', dict(
                    value_params(batch, records, field, value), max=rule.max_value))
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


def check_date(rule, records, batch):
    """Fail a record whose filled date falls outside the rule's condition; no date is not applicable.

    The day of the scan and the day of a date and time value are both taken in the time zone of
    the record's company.

    Requirements: FR-DGQ-0005
    """
    zones = zone_names(records)
    today_in = {zone: local_day(batch.now, zone) for zone in set(zones.values())}
    outcomes = {}
    for record in records:
        zone = zones[record.id]
        outcome = Outcome(NOT_APPLICABLE)
        for field in _fields_of(rule, records):
            value = record[field.name]
            if not value:
                continue
            day = local_day(value, zone) if field.type == 'datetime' else value
            failure = date_failure(rule, day, today_in[zone])
            if failure:
                code, extra = failure
                outcome = Outcome(FAIL, code, dict(
                    extra, **value_params(batch, records, field, day.isoformat())))
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
    """The translated label of a field named in the parameters, or its name once the field or model is gone."""
    name = name or params['field']
    model = env.get(params.get('model'))
    field = model._fields.get(name) if model is not None else None
    return field._description_string(env) if field else name


# The sentence of each code that quotes a value: (env, field label, params) -> text.
VALUE_SENTENCES = {
    'pattern_mismatch': lambda env, label, p: env._(
        "%(field)s has the value '%(value)s', which does not match the expected format %(pattern)s.",
        field=label, value=p['value'], pattern=p['pattern']),
    'range_below': lambda env, label, p: env._(
        "%(field)s is %(value)s, below the minimum of %(limit)s.",
        field=label, value=number_text(p['value']), limit=number_text(p['min'])),
    'range_above': lambda env, label, p: env._(
        "%(field)s is %(value)s, above the maximum of %(limit)s.",
        field=label, value=number_text(p['value']), limit=number_text(p['max'])),
    'date_future': lambda env, label, p: env._(
        "%(field)s is %(value)s, which is in the future.", field=label, value=p['value']),
    'date_past': lambda env, label, p: env._(
        "%(field)s is %(value)s, which is in the past.", field=label, value=p['value']),
    'date_older': lambda env, label, p: env._(
        "%(field)s is %(value)s, which is older than the limit of %(days)s days.",
        field=label, value=p['value'], days=p['days']),
    'date_ahead': lambda env, label, p: env._(
        "%(field)s is %(value)s, which is more than %(days)s days ahead.",
        field=label, value=p['value'], days=p['days']),
    'date_between': lambda env, label, p: env._(
        "%(field)s is %(value)s, outside the dates from %(start)s to %(end)s.",
        field=label, value=p['value'], start=p['from'], end=p['to']),
}

# The same codes for a field whose value may not be shown (sensitive or excluded).
HIDDEN_VALUE_SENTENCES = {
    'pattern_mismatch': lambda env, label, p: env._(
        "%(field)s does not match the expected format %(pattern)s.", field=label, pattern=p['pattern']),
    'range_below': lambda env, label, p: env._(
        "%(field)s is below the minimum of %(limit)s.", field=label, limit=number_text(p['min'])),
    'range_above': lambda env, label, p: env._(
        "%(field)s is above the maximum of %(limit)s.", field=label, limit=number_text(p['max'])),
    'date_future': lambda env, label, p: env._("%(field)s is in the future.", field=label),
    'date_past': lambda env, label, p: env._("%(field)s is in the past.", field=label),
    'date_older': lambda env, label, p: env._(
        "%(field)s is older than the limit of %(days)s days.", field=label, days=p['days']),
    'date_ahead': lambda env, label, p: env._(
        "%(field)s is more than %(days)s days ahead.", field=label, days=p['days']),
    'date_between': lambda env, label, p: env._(
        "%(field)s is outside the dates from %(start)s to %(end)s.", field=label, start=p['from'], end=p['to']),
}


def explain(env, code, params):
    """The sentence of a code of the basic checks in the language of ``env``, or None for another code.

    A field whose value may not be shown is named without its value.

    Requirements: FR-DGQ-0002, FR-DGQ-0003, FR-DGQ-0004, FR-DGQ-0005
    """
    if code == 'required_empty':
        labels = ', '.join(field_label(env, params, name) for name in params['fields'])
        return env._("%(fields)s is empty.", fields=labels)
    sentences = HIDDEN_VALUE_SENTENCES if params and params.get('value_hidden') else VALUE_SENTENCES
    sentence = sentences.get(code)
    return sentence(env, field_label(env, params), params) if sentence else None
