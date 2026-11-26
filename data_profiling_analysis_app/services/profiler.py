"""The profiling engine: SQL aggregates on the records the runner may read.

The environment passed in is bound to the person running the profile (never
sudo), so record rules, the selected companies and archived records are applied
by the ORM's own search (BR-DPA-0006, NFR-SEC-0002).
"""
from odoo import fields
from odoo.exceptions import AccessError
from odoo.orm.query import Query
from odoo.tools import SQL

from .const import MAX_LISTED_VALUES, PROFILABLE_TYPES, RUN_IDS_TABLE, TECHNICAL_FIELDS, TEXT_TYPES, WHITESPACE

LONG_TEXT_TYPES = ('text', 'html')  # their distinct values are counted, never listed (FR-DPA-0007)
RELATIONAL_TYPES = ('many2one', 'many2many')


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


def profile(env, model_name, field_names):
    """Profile the readable active records of a model, field by field.

    Returns ``{'record_count': int, 'fields': [dict, ...]}``, one dict per
    profiled field (the chosen ones still profilable, or every profilable one
    when none is chosen), in the order of the model's fields.

    Each dict also holds the distinct count, the listed values, the number of
    values beyond the listed ones and, for a selection, the unused values.

    Requirements: FR-DPA-0002, FR-DPA-0004, FR-DPA-0007, FR-DPA-0013, FR-DPA-0014,
    FR-DPA-0015, BR-DPA-0001, BR-DPA-0006, NFR-SEC-0002
    """
    model = env[model_name]
    if not model.has_access('read'):
        raise AccessError(env._("You are not allowed to read the records of this model."))
    profilable = profilable_fields(env, model_name)
    chosen = [name for name in profilable if name in field_names] if field_names else profilable
    record_count = _store_run_ids(model)
    fields_result = [_profile_field(model, model._fields[name], record_count) for name in chosen]
    env.cr.execute(SQL("DROP TABLE %s", SQL.identifier(RUN_IDS_TABLE)))
    return {'record_count': record_count, 'fields': fields_result}


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


def _filled_values_sql(model, field):
    """SQL selecting the filled value of the field (column ``v``), one row per record and value."""
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
