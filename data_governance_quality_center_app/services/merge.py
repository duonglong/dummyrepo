"""What a merge of duplicate records needs to know: links, comparable fields, values.

Plain functions that receive an environment and plain arguments. The link counts follow the
foreign keys of the database, like Odoo's own contact merge, so a count holds for any model.
"""
from odoo.models import MAGIC_COLUMNS
from odoo.tools import SQL

from ..const import MERGE_TECHNICAL_MODEL_PREFIXES
from .matching import field_score


def foreign_keys(env, table):
    """Return ``(table, column)`` of every foreign key that points to ``table``'s id.

    Requirements: FR-DGQ-0040
    """
    env.cr.execute(SQL(
        """
        SELECT cl1.relname, att1.attname
          FROM pg_constraint con
          JOIN pg_class cl1 ON con.conrelid = cl1.oid
          JOIN pg_class cl2 ON con.confrelid = cl2.oid
          JOIN pg_attribute att1 ON att1.attrelid = cl1.oid AND con.conkey[1] = att1.attnum
         WHERE con.contype = 'f'
           AND array_length(con.conkey, 1) = 1
           AND cl2.relname = %s
           AND cl2.relnamespace = current_schema::regnamespace
        """, table))
    return env.cr.fetchall()


def _document_models(env):
    """Return ``{table: model name}`` of the business models whose rows count as linked documents."""
    excluded = ('data.quality.', *MERGE_TECHNICAL_MODEL_PREFIXES)
    return {
        model._table: name for name, model in env.registry.items()
        if model._auto and not model._transient and not name.startswith(excluded)
    }


def linked_counts(env, model_name, res_ids):
    """Count, per record, the documents that link to it: ``{id: {model name: count}}``.

    Only rows of business models count (relation tables, messages, followers, activities and user
    accounts do not); a row
    of the same model that is itself one of the records is left out, it is not a document.

    Requirements: FR-DGQ-0040, FR-DGQ-0042
    """
    env.flush_all()
    ids = list(res_ids)
    counts = {res_id: {} for res_id in ids}
    table = env[model_name]._table
    documents = _document_models(env)
    for fk_table, column in foreign_keys(env, table):
        document = documents.get(fk_table)
        if not document or not ids:
            continue
        query = SQL(
            "SELECT %(column)s, COUNT(*) FROM %(table)s WHERE %(column)s = ANY(%(ids)s)",
            column=SQL.identifier(column), table=SQL.identifier(fk_table), ids=ids)
        if fk_table == table:
            query = SQL("%s AND NOT (id = ANY(%s))", query, ids)
        env.cr.execute(SQL("%s GROUP BY %s", query, SQL.identifier(column)))
        for res_id, count in env.cr.fetchall():
            counts[res_id][document] = counts[res_id].get(document, 0) + count
    return counts


def _unique_key_columns(env, table, column):
    """Return the column lists of the unique keys of ``table`` that contain ``column``."""
    env.cr.execute(SQL(
        """
        SELECT array_agg(a.attname ORDER BY k.ord)
          FROM pg_index i
          JOIN pg_class c ON c.oid = i.indrelid
          CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord)
          JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
         WHERE i.indisunique AND i.indexprs IS NULL
           AND c.relname = %s AND c.relnamespace = current_schema::regnamespace
         GROUP BY i.indexrelid
        """, table))
    return [columns for (columns,) in env.cr.fetchall() if column in columns]


def irreversible_reason(env, model_name, kept_id, other_ids):
    """Return why moving the links of the other records to the kept one cannot be undone, or False.

    A move cannot be undone when a unique key would collide: the native mover then deletes the
    row instead of moving it. The answer names the table that would lose rows.

    Requirements: FR-DGQ-0042, BR-DGQ-0008
    """
    env.flush_all()
    table = env[model_name]._table
    others = list(other_ids)
    for fk_table, column in foreign_keys(env, table):
        for columns in _unique_key_columns(env, fk_table, column):
            rest = [SQL.identifier(name) for name in columns if name != column]
            same = SQL(' AND ').join(SQL("a.%s = b.%s", name, name) for name in rest) if rest else SQL('TRUE')
            env.cr.execute(SQL(
                """
                SELECT 1 FROM %(table)s a JOIN %(table)s b ON %(same)s
                 WHERE a.%(column)s = ANY(%(others)s)
                   AND (b.%(column)s = %(kept)s OR (b.%(column)s = ANY(%(others)s) AND b.ctid <> a.ctid))
                 LIMIT 1
                """,
                table=SQL.identifier(fk_table), same=same, column=SQL.identifier(column),
                others=others, kept=kept_id))
            if env.cr.fetchone():
                return env._("Moving the links in %s would make two rows the same: one would be lost.", fk_table)
    return False


def comparable_fields(env, model_name, rule_field_names=()):
    """Return the names of the fields shown in a comparison: stored, plain, not sensitive.

    The matching fields of the rule come first; sensitive and excluded fields are left out.

    Requirements: FR-DGQ-0040, BR-DGQ-0010
    """
    usable = env['data.quality.field.config']._get_usable_field_names(model_name)
    model = env[model_name]
    names = [
        name for name, field in model._fields.items()
        if name in usable and name not in MAGIC_COLUMNS
        and field.type not in ('one2many', 'many2many', 'binary', 'properties', 'json')
    ]
    first = [name for name in rule_field_names if name in names]
    return first + [name for name in names if name not in first]


def display_value(record, field_name):
    """Return the value of a field as the text a person reads; empty text when not set."""
    field = record._fields[field_name]
    value = field.convert_to_export(record[field_name], record)
    return '' if value is False or value is None else str(value)


def field_rows(env, model_name, res_ids, rule_lines=()):
    """Return the comparison of records field by field, matching fields first.

    ``rule_lines`` are the ``(field name, method, weight)`` of the rule. Each row is a dict:
    ``name``, ``label``, ``writable``, ``is_match``, ``values`` (``{id: text}``), ``scores``
    (``{id: score against the first record}``, matching fields only) and ``differs`` (the texts
    are not all the same, a gap included). A field set on no record is left out unless it matches.

    Requirements: FR-DGQ-0040, FR-DGQ-0042, BR-DGQ-0010
    """
    ids = list(res_ids)
    records = env[model_name].browse(ids).exists()
    ids = [res_id for res_id in ids if res_id in set(records.ids)]
    match = {name: method for name, method, _weight in rule_lines}
    rows = []
    for name in comparable_fields(env, model_name, list(match)):
        field = records._fields[name]
        values = {record.id: display_value(record, name) for record in records}
        is_match = name in match
        if not is_match and not any(values.values()):
            continue
        scores = {}
        if is_match and ids:
            first = values[ids[0]]
            for res_id in ids[1:]:
                scores[res_id] = field_score(match[name], first, values[res_id], name)
        rows.append({
            'name': name, 'label': field._description_string(env), 'writable': not field.readonly,
            'is_match': is_match, 'values': values, 'scores': scores,
            'differs': len({values[res_id] for res_id in ids}) > 1,
        })
    return rows
