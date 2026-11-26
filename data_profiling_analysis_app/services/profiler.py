"""The profiling engine: SQL aggregates on the records the runner may read.

The environment passed in is bound to the person running the profile (never
sudo), so record rules, the selected companies and archived records are applied
by the ORM's own search (BR-DPA-0006, NFR-SEC-0002).
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.orm.query import Query
from odoo.tools import SQL

from . import patterns, stats
from .const import (
    COMMON_FORMAT_SHARE, MAX_LISTED_VALUES, PROFILABLE_TYPES, RUN_IDS_TABLE, TECHNICAL_FIELDS, TEXT_TYPES,
    USUAL_RANGE_MIN_VALUES, WHITESPACE,
)

LONG_TEXT_TYPES = ('text', 'html')  # their distinct values are counted, never listed (FR-DPA-0007)
RELATIONAL_TYPES = ('many2one', 'many2many')
NUMBER_TYPES = ('integer', 'float', 'monetary')


def profilable_fields(env, model_name):
    """Names of the stored, readable fields of the model that can be profiled.

    Requirements: BR-DPA-0002
    """
    model = env[model_name]
    return [
        name for name, field in model._fields.items()
        if field.store
        and name not in TECHNICAL_FIELDS
        and field.type in PROFILABLE_TYPES
        and model._has_field_access(field, 'read')
    ]


def profile(env, model_name, field_names, outdated_days=None, run_date=None):
    """Profile the readable active records of a model, field by field.

    Returns ``{'record_count': int, 'fields': [dict, ...]}``, one dict per
    profiled field (the chosen ones still profilable, or every profilable one
    when none is chosen), in the order of the model's fields.

    Each dict also holds the distinct count, the listed values, the number of
    values beyond the listed ones and, for a selection, the unused values.

    A text field also gets its most common format and its values in another
    format, a number field its usual range and its unusual values, each value
    as a finding dict under ``findings``. A number field adds its statistics, a
    date field its earliest and latest dates and span in days.

    With ``outdated_days``, the records not updated for that many days before ``run_date``
    (now when not given) come as finding dicts under ``outdated``, oldest first.

    Requirements: FR-DPA-0002, FR-DPA-0004, FR-DPA-0007, FR-DPA-0008, FR-DPA-0009, FR-DPA-0010,
    FR-DPA-0011, FR-DPA-0013, FR-DPA-0014, FR-DPA-0015, FR-DPA-0016, FR-DPA-0017, FR-DPA-0028,
    BR-DPA-0001, BR-DPA-0005, BR-DPA-0006, NFR-SEC-0002
    """
    model = env[model_name]
    if not model.has_access('read'):
        raise AccessError(env._("You are not allowed to read the records of this model."))
    profilable = profilable_fields(env, model_name)
    chosen = [name for name in profilable if name in field_names] if field_names else profilable
    record_count = _store_run_ids(model)
    fields_result = [_profile_field(model, model._fields[name], record_count) for name in chosen]
    outdated = _outdated_result(model, outdated_days, run_date or fields.Datetime.now()) if outdated_days else []
    env.cr.execute(SQL("DROP TABLE %s", SQL.identifier(RUN_IDS_TABLE)))
    return {'record_count': record_count, 'fields': fields_result, 'outdated': outdated}


def _store_run_ids(model):
    """Keep the ids of the records the runner may read once, so record rules run once. Returns their number."""
    table = SQL.identifier(RUN_IDS_TABLE)
    cr = model.env.cr
    cr.execute(SQL("CREATE TEMPORARY TABLE %s (id integer PRIMARY KEY) ON COMMIT DROP", table))
    query = model._search([])
    cr.execute(SQL(
        "INSERT INTO %s %s", table, query.select(SQL.identifier(model._table, 'id'))))
    return cr.rowcount


def _profile_field(model, field, record_count):
    """Counts, completeness, distinct values and unused selection values of one field."""
    filled_count = _count_filled(model, field)
    empty_count = record_count - filled_count
    completeness = round(filled_count * 100 / record_count, 1) if record_count else 0.0
    distinct_count, groups = _group_values(model, field)
    return {
        'distinct_count': distinct_count,
        'more_count': max(distinct_count - MAX_LISTED_VALUES, 0),
        'values': _value_lines(model.env, field, groups[:MAX_LISTED_VALUES], empty_count, record_count),
        'unused_values': _unused_selection_labels(model.env, field, groups),
        'field': field,
        'record_count': record_count,
        'filled_count': filled_count,
        'empty_count': empty_count,
        'completeness': completeness,
        'empty_rate': round(100 - completeness, 1) if record_count else 0.0,
        **_format_result(model, field, filled_count),
        **_range_result(model, field, filled_count),
        **_number_statistics(model, field, filled_count),
        **_date_statistics(model, field, filled_count),
    }


def _count_filled(model, field):
    """Number of profiled records where the field is not empty (BR-DPA-0001)."""
    run_ids = SQL.identifier(RUN_IDS_TABLE)
    if field.type == 'many2many':
        query = SQL(
            "SELECT count(DISTINCT rel.%(left)s) FROM %(rel)s rel JOIN %(ids)s ON %(ids)s.id = rel.%(left)s",
            left=SQL.identifier(field.column1), rel=SQL.identifier(field.relation), ids=run_ids)
    else:
        query = Query(model)
        value = model._field_to_sql(model._table, field.name, query)
        query.add_where(SQL(
            "%s IN (SELECT id FROM %s)", SQL.identifier(model._table, 'id'), run_ids))
        query.add_where(SQL("NOT (%s)", _empty_sql(field, value)))
        query = query.select(SQL("count(*)"))
    return model.env.execute_query(query)[0][0]


def _empty_sql(field, value):
    """SQL condition: the value is empty on the record (BR-DPA-0001)."""
    if field.type == 'boolean':
        return SQL("FALSE")
    if field.type in TEXT_TYPES:
        return SQL("%(value)s IS NULL OR btrim(%(value)s, %(blank)s) = ''", value=value, blank=WHITESPACE)
    if field.type == 'html':
        text = SQL("regexp_replace(%s, '<[^>]*>', '', 'g')", value)
        return SQL("%(value)s IS NULL OR btrim(%(text)s, %(blank)s) = ''", value=value, text=text, blank=WHITESPACE)
    return SQL("%s IS NULL", value)


def _filled_values_sql(model, field, with_record_id=False):
    """SQL selecting the filled value of the field (column ``v``), one row per record and value.

    With ``with_record_id`` the record's id comes as column ``record_id`` (not for a many2many).
    """
    run_ids = SQL.identifier(RUN_IDS_TABLE)
    if field.type == 'many2many':
        return SQL(
            "SELECT rel.%(right)s AS v FROM %(rel)s rel JOIN %(ids)s ON %(ids)s.id = rel.%(left)s",
            right=SQL.identifier(field.column2), left=SQL.identifier(field.column1),
            rel=SQL.identifier(field.relation), ids=run_ids)
    query = Query(model)
    value = model._field_to_sql(model._table, field.name, query)
    if field.type == 'boolean':
        value = SQL("COALESCE(%s, FALSE)", value)
    query.add_where(SQL("%s IN (SELECT id FROM %s)", SQL.identifier(model._table, 'id'), run_ids))
    query.add_where(SQL("NOT (%s)", _empty_sql(field, value)))
    if with_record_id:
        return query.select(SQL("%s AS record_id, %s AS v", SQL.identifier(model._table, 'id'), value))
    return query.select(SQL("%s AS v", value))


def _group_values(model, field):
    """Number of distinct filled values of a field and its ``(value, records)`` groups.

    The groups come most frequent first, equal counts by value (a selection by
    label). A selection returns every group, so that its unused values can be
    told; any other field returns the first MAX_LISTED_VALUES only. A long text
    returns no group: its count only is shown.

    Requirements: FR-DPA-0007, FR-DPA-0013, FR-DPA-0014
    """
    source = _filled_values_sql(model, field)
    distinct_count = model.env.execute_query(SQL("SELECT count(DISTINCT v) FROM (%s) filled", source))[0][0]
    if field.type in LONG_TEXT_TYPES:
        return distinct_count, []
    is_selection = field.type == 'selection'
    groups = model.env.execute_query(SQL(
        "SELECT v, count(*) FROM (%(source)s) filled GROUP BY v ORDER BY count(*) DESC, v %(limit)s",
        source=source, limit=SQL("") if is_selection else SQL("LIMIT %s", MAX_LISTED_VALUES)))
    if is_selection:
        labels = dict(field._description_selection(model.env))
        groups.sort(key=lambda group: (-group[1], labels.get(group[0], group[0])))
    return distinct_count, groups


def _value_lines(env, field, groups, empty_count, record_count):
    """The listed values of a field with their share, then the empty (or none) line.

    Requirements: FR-DPA-0007, FR-DPA-0013
    """
    labels = _value_labels(env, field, [value for value, _count in groups])
    lines = [
        {'value_label': labels[value], 'is_empty': False, 'count': count, 'share': _share(count, record_count)}
        for value, count in groups
    ]
    if empty_count:
        empty_label = env._("(none)") if field.type in RELATIONAL_TYPES else env._("(empty)")
        lines.append({
            'value_label': empty_label, 'is_empty': True,
            'count': empty_count, 'share': _share(empty_count, record_count),
        })
    return lines


def _share(count, record_count):
    """Percent of the profiled records, one decimal."""
    return round(count * 100 / record_count, 1) if record_count else 0.0


def _value_labels(env, field, values):
    """Label of each raw value of a field: a selection label, a linked record's name, or the value as text."""
    if field.type == 'selection':
        selection = dict(field._description_selection(env))
        return {value: selection.get(value, value) for value in values}
    if field.type in RELATIONAL_TYPES:
        return _linked_record_labels(env, field.comodel_name, values)
    if field.type == 'boolean':
        return {value: env._("Yes") if value else env._("No") for value in values}
    if field.type == 'datetime':
        return {value: str(fields.Datetime.context_timestamp(env.user, value).replace(tzinfo=None)) for value in values}
    return {value: str(value) for value in values}


def _linked_record_labels(env, comodel_name, ids):
    """Name of each linked record the runner may read; the others show as restricted (NFR-SEC-0002)."""
    records = env[comodel_name].browse(ids)
    readable = records._filtered_access('read')
    return {
        record.id: record.display_name if record in readable else env._("Restricted record")
        for record in records
    }


def _unused_selection_labels(env, field, groups):
    """Labels of the selection values no profiled record uses, or ``None`` when all are used (FR-DPA-0014)."""
    if field.type != 'selection':
        return False
    used = {value for value, _count in groups}
    unused = [label for key, label in field._description_selection(env) if key not in used]
    return ', '.join(unused) if unused else env._("None")


def _pattern_sql(value):
    """SQL expression of ``patterns.pattern_of`` on a value."""
    letters = SQL("regexp_replace(btrim(%s, %s), '[[:alpha:]]', 'A', 'g')", value, WHITESPACE)
    return SQL("regexp_replace(%s, '[[:digit:]]', '9', 'g')", letters)


def _format_result(model, field, filled_count):
    """Most common format of a single-line text field and the values in another format.

    Values are flagged only while the most common format is held by at least
    COMMON_FORMAT_SHARE of the filled values. Empty for any other field type.

    Requirements: FR-DPA-0008, FR-DPA-0009, BR-DPA-0003
    """
    if field.type != 'char':
        return {}
    pattern = _pattern_sql(SQL.identifier('filled', 'v'))
    top = model.env.execute_query(SQL(
        'SELECT pat, count(*) FROM (SELECT %(pattern)s AS pat FROM (%(source)s) filled) patterns '
        'GROUP BY pat ORDER BY count(*) DESC, pat COLLATE "C" LIMIT 1',
        pattern=pattern, source=_filled_values_sql(model, field)))
    if not top:
        return {'main_pattern': False, 'main_pattern_share': 0.0, 'has_common_format': False, 'findings': []}
    main_pattern, main_count = top[0]
    has_common_format = main_count / filled_count >= COMMON_FORMAT_SHARE
    flagged = model.env.execute_query(SQL(
        "SELECT record_id, v FROM (%(source)s) filled WHERE %(pattern)s <> %(main)s ORDER BY record_id",
        source=_filled_values_sql(model, field, with_record_id=True), pattern=pattern, main=main_pattern,
    )) if has_common_format else []
    return {
        'main_pattern': main_pattern,
        'main_pattern_share': _share(main_count, filled_count),
        'has_common_format': has_common_format,
        'findings': _findings(model, 'inconsistent_format', flagged),
    }


def _range_result(model, field, filled_count):
    """Usual range of a number field and the values outside it, from enough filled values.

    The quartiles are the database's linear percentiles, as a spreadsheet's QUARTILE.INC.
    Empty for any other field type.

    Requirements: FR-DPA-0010, BR-DPA-0004
    """
    if field.type not in NUMBER_TYPES:
        return {}
    if filled_count < USUAL_RANGE_MIN_VALUES:
        return {'range_computed': False, 'findings': []}
    source = _filled_values_sql(model, field, with_record_id=True)
    q1, q3 = model.env.execute_query(SQL(
        "SELECT percentile_cont(ARRAY[0.25, 0.75]) WITHIN GROUP (ORDER BY v::float8) FROM (%s) filled",
        source))[0][0]
    low, high = stats.usual_range(q1, q3)
    outside = model.env.execute_query(SQL(
        "SELECT record_id, v FROM (%(source)s) filled WHERE v::float8 < %(low)s OR v::float8 > %(high)s "
        "ORDER BY record_id", source=source, low=low, high=high))
    return {
        'range_computed': True, 'range_low': low, 'range_high': high,
        'findings': _findings(model, 'unusual_number', outside),
    }


def _findings(model, kind, rows):
    """Finding dicts of ``(record id, value)`` rows, with the name of each record."""
    names = {record.id: record.display_name for record in model.browse([record_id for record_id, _v in rows])}
    return [
        {
            'kind': kind, 'res_model': model._name, 'res_id': record_id,
            'record_name': names[record_id], 'value_label': str(value),
        }
        for record_id, value in rows
    ]


def _number_statistics(model, field, filled_count):
    """Minimum, maximum, sum, average, median and population standard deviation of a number field.

    Empty for any other field type or when no value is filled.

    Requirements: FR-DPA-0016
    """
    if field.type not in NUMBER_TYPES or not filled_count:
        return {}
    minimum, maximum, total, mean, median, deviation = model.env.execute_query(SQL(
        "SELECT min(v::float8), max(v::float8), sum(v::float8), avg(v::float8), "
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY v::float8), stddev_pop(v::float8) FROM (%s) filled",
        _filled_values_sql(model, field)))[0]
    return {
        'min_value': minimum, 'max_value': maximum, 'sum_value': total,
        'mean_value': mean, 'median_value': median, 'stddev_value': deviation,
    }


def _date_statistics(model, field, filled_count):
    """Earliest and latest value of a date or date-time field and the days between them.

    A date-time is read in the runner's time zone. Empty for any other field type or when no value is filled.

    Requirements: FR-DPA-0017
    """
    if field.type not in ('date', 'datetime') or not filled_count:
        return {}
    earliest, latest = model.env.execute_query(SQL(
        "SELECT min(v), max(v) FROM (%s) filled", _filled_values_sql(model, field)))[0]
    if field.type == 'datetime':
        earliest, latest = (fields.Datetime.context_timestamp(model.env.user, value).date() for value in (earliest, latest))
    return {'earliest_date': earliest, 'latest_date': latest, 'span_days': (latest - earliest).days}


def _outdated_result(model, outdated_days, run_date):
    """Finding dicts of the profiled records last updated more than ``outdated_days`` days before the run, oldest first.

    A record updated exactly that many days before is not outdated. A model that keeps no
    last update date has none.

    Requirements: FR-DPA-0011, BR-DPA-0005
    """
    if not model._log_access:
        return []
    last_update = SQL.identifier(model._table, 'write_date')
    query = Query(model)
    query.add_where(SQL("%s IN (SELECT id FROM %s)", SQL.identifier(model._table, 'id'), SQL.identifier(RUN_IDS_TABLE)))
    query.add_where(SQL("%s < %s", last_update, run_date - timedelta(days=outdated_days)))
    query.order = SQL("%s, %s", last_update, SQL.identifier(model._table, 'id'))
    rows = model.env.execute_query(query.select(SQL.identifier(model._table, 'id'), last_update))
    names = {record.id: record.display_name for record in model.browse([record_id for record_id, _date in rows])}
    return [
        {
            'kind': 'outdated', 'res_model': model._name, 'res_id': record_id, 'record_name': names[record_id],
            'last_update': updated, 'days_since_update': (run_date - updated).days,
        }
        for record_id, updated in rows
    ]
