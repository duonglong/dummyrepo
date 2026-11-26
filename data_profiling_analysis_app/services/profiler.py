"""The profiling engine: SQL aggregates on the records the runner may read.

The environment passed in is bound to the person running the profile (never
sudo), so record rules, the selected companies and archived records are applied
by the ORM's own search (BR-DPA-0006, NFR-SEC-0002).
"""
from odoo.exceptions import AccessError
from odoo.orm.query import Query
from odoo.tools import SQL

from .const import PROFILABLE_TYPES, RUN_IDS_TABLE, TECHNICAL_FIELDS, TEXT_TYPES, WHITESPACE


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

    Requirements: FR-DPA-0002, FR-DPA-0004, BR-DPA-0001, BR-DPA-0006, NFR-SEC-0002
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
    """Counts and completeness of one field."""
    filled_count = _count_filled(model, field)
    empty_count = record_count - filled_count
    completeness = round(filled_count * 100 / record_count, 1) if record_count else 0.0
    return {
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
