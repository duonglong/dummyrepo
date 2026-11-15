"""Statistics of a profile, computed by SQL aggregates so that memory does not grow with the data.

Every figure is read from the stored column of one field of one model, for the records of one
company; nothing is written to the profiled model. Identifiers and values go through ``SQL``.
"""
from datetime import timedelta

from odoo import fields
from odoo.fields import Domain
from odoo.tools import SQL

from ..const import PROFILE_OUTLIER_FACTOR, PROFILE_TOP_COUNT

TEXT_TYPES = ('char', 'text')
NUMBER_TYPES = ('integer', 'float', 'monetary')


def _source(env, model_name, company):
    """Return ``(model, from_sql, where_sql)`` of the active records of the company, as root."""
    # sudo: a profile reads the whole company's records whoever started it; access to the
    # result is limited by the profile's own rules
    model = env[model_name].sudo()
    domain = Domain.TRUE
    if 'company_id' in model._fields and model._fields['company_id'].store:
        domain &= Domain('company_id', '=', company.id)
    query = model._search(domain)
    where = SQL("WHERE %s", query.where_clause) if query.where_clause else SQL()
    return model, query, SQL("FROM %s %s", query.from_clause, where)


def _and(query, condition):
    """Add a condition to the source: the joiner and the condition in brackets, so an OR stays inside."""
    return SQL("%s (%s)", SQL("AND") if query.where_clause else SQL("WHERE"), condition)


def _is_empty(model, field_name, value):
    """SQL condition: the value is empty (null; also blank for text, ``false`` is a value of a checkbox)."""
    ttype = model._fields[field_name].type
    if ttype in TEXT_TYPES or ttype == 'html':
        return SQL("(%s IS NULL OR btrim(%s) = '')", value, value)
    return SQL("%s IS NULL", value)


def count_records(env, model_name, company):
    """Number of active records of the company.

    Requirements: FR-DGQ-0053
    """
    _model, _query, source = _source(env, model_name, company)
    env.cr.execute(SQL("SELECT COUNT(*) %s", source))
    return env.cr.fetchone()[0]


def count_stale(env, model_name, company, days):
    """Number of active records of the company not updated for more than ``days`` days.

    Requirements: FR-DGQ-0055
    """
    model, query, source = _source(env, model_name, company)
    if not model._log_access:
        return 0
    limit = fields.Datetime.now() - timedelta(days=days)
    written = model._field_to_sql(model._table, 'write_date', query)
    env.cr.execute(SQL("SELECT COUNT(*) %s %s", source, _and(query, SQL("%s < %s", written, limit))))
    return env.cr.fetchone()[0]


def _top(env, query, source, value, filled, limit=PROFILE_TOP_COUNT):
    """The ``limit`` most frequent values with their counts: ``[(value, count)]``."""
    env.cr.execute(SQL(
        "SELECT %s, COUNT(*) AS n %s %s GROUP BY 1 ORDER BY n DESC, 1 LIMIT %s",
        value, source, _and(query, filled), limit))
    return env.cr.fetchall()


def _quartile_stats(env, query, source, value):
    """Minimum, maximum, mean, median and unusual count of a number column."""
    number = SQL("CAST(%s AS double precision)", value)
    env.cr.execute(SQL(
        "SELECT MIN(%(n)s), MAX(%(n)s), AVG(%(n)s),"
        " percentile_cont(0.25) WITHIN GROUP (ORDER BY %(n)s),"
        " percentile_cont(0.5) WITHIN GROUP (ORDER BY %(n)s),"
        " percentile_cont(0.75) WITHIN GROUP (ORDER BY %(n)s) %(source)s %(where)s",
        n=number, source=source, where=_and(query, SQL("%s IS NOT NULL", number))))
    low, high, mean, first, median, third = env.cr.fetchone()
    if low is None:
        return {'min_value': 0.0, 'max_value': 0.0, 'mean_value': 0.0, 'median_value': 0.0, 'outlier_count': 0}
    spread = (third - first) * PROFILE_OUTLIER_FACTOR
    unusual = SQL("%s < %s OR %s > %s", number, first - spread, number, third + spread)
    env.cr.execute(SQL("SELECT COUNT(*) %s %s", source, _and(query, unusual)))
    return {
        'min_value': low, 'max_value': high, 'mean_value': mean, 'median_value': median,
        'outlier_count': env.cr.fetchone()[0],
    }


def _shape_of(value):
    """SQL of the shape of a text: letters as A, digits as 9."""
    return SQL(
        "regexp_replace(regexp_replace(%s, '[[:alpha:]]', 'A', 'g'), '[[:digit:]]', '9', 'g')", value)


def _unused_choices(env, model, field, query, source, value):
    """The declared choices of a fixed list that no record uses; empty for a computed list."""
    declared = model._fields[field].selection
    if not isinstance(declared, (list, tuple)):
        return []
    env.cr.execute(SQL(
        "SELECT DISTINCT %s %s %s", value, source, _and(query, SQL("%s IS NOT NULL", value))))
    used = {row[0] for row in env.cr.fetchall()}
    return [key for key, _label in declared if key not in used]


def profile_field(env, model_name, field, company):
    """Return the values of the profile line of one stored field of a model for a company.

    Requirements: FR-DGQ-0053, FR-DGQ-0054, FR-DGQ-0055
    """
    model, query, source = _source(env, model_name, company)
    python_field = model._fields[field]
    value = model._field_to_sql(model._table, field, query)
    empty = _is_empty(model, field, value)
    filled = SQL("NOT (%s)", empty)
    env.cr.execute(SQL(
        "SELECT COUNT(*), COUNT(*) FILTER (WHERE %s), COUNT(DISTINCT %s) FILTER (WHERE %s) %s",
        empty, value, filled, source))
    total, empty_count, distinct_count = env.cr.fetchone()
    share = 100.0 * empty_count / total if total else 0.0
    values = {
        'field_type': python_field.type, 'empty_count': empty_count, 'distinct_count': distinct_count,
        'empty_share': share,
    }
    ttype = python_field.type
    if ttype == 'many2one':
        names = env[python_field.comodel_name].sudo()  # sudo: names of linked records, not their data
        rows = _top(env, query, source, value, filled)
        linked = names.browse([row[0] for row in rows])
        links = [[record.display_name, row[1]] for record, row in zip(linked, rows)]
        values.update(top_values=links, link_distribution=links, link_empty_share=share)
    else:
        text = SQL("CAST(%s AS text)", value)
        values['top_values'] = [list(row) for row in _top(env, query, source, text, filled)]
    if ttype in TEXT_TYPES:
        values['shapes'] = [list(row) for row in _top(
            env, query, source, _shape_of(value), filled)]
    if ttype in NUMBER_TYPES:
        values.update(_quartile_stats(env, query, source, value))
    if ttype == 'selection':
        values['unused_choices'] = _unused_choices(env, model, field, query, source, value)
    return values
