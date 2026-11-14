"""What a merge of duplicate records needs to know: links, comparable fields, values.

Plain functions that receive an environment and plain arguments. The link counts follow the
foreign keys of the database, like Odoo's own contact merge, so a count holds for any model.
"""
import hashlib
import json

from psycopg2.errors import LockNotAvailable

from odoo.models import MAGIC_COLUMNS
from odoo.tools import SQL, json_default

from ..const import MERGE_ASSOCIATION_TABLES, MERGE_TECHNICAL_MODEL_PREFIXES
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


def _columns(env, table):
    """Return the column names of ``table``."""
    env.cr.execute(SQL(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = %s AND table_schema = current_schema ORDER BY ordinal_position", table))
    return [name for (name,) in env.cr.fetchall()]


def _is_association(env, table):
    """Whether a row of ``table`` only links two records (no id of its own, or a listed table)."""
    return table in MERGE_ASSOCIATION_TABLES or 'id' not in _columns(env, table)


def _owned_link(env, model_name):
    """Return the ``(table, column)`` of the rows a record owns and that never move, or None.

    A product variant belongs to its template: its template column stays, only the links to the
    variant move.
    """
    if model_name == 'product.template':
        return env['product.product']._table, 'product_tmpl_id'
    return None


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
        if _is_association(env, fk_table) or (fk_table, column) == _owned_link(env, model_name):
            continue
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


def preview_signature(env, model_name, res_ids):
    """Return a hash of the state of the records: which exist, whether active, last write.

    The merge compares it with the one taken when the preview was saved: a record edited,
    archived or deleted since then gives another hash.

    Requirements: FR-DGQ-0044
    """
    env.flush_all()
    env.cr.execute(SQL(
        "SELECT id, active, write_date FROM %s WHERE id = ANY(%s) ORDER BY id" if 'active' in env[model_name]._fields
        else "SELECT id, TRUE, write_date FROM %s WHERE id = ANY(%s) ORDER BY id",
        SQL.identifier(env[model_name]._table), sorted(res_ids)))
    return hashlib.sha256(json.dumps(env.cr.fetchall(), default=str).encode()).hexdigest()


def lock_records(env, model_name, res_ids):
    """Lock the records of a merge without waiting; return False when another transaction holds one.

    Requirements: FR-DGQ-0043
    """
    env.flush_all()
    try:
        with env.cr.savepoint(flush=False):
            env.cr.execute(SQL(
                "SELECT 1 FROM %s WHERE id = ANY(%s) FOR UPDATE NOWAIT",
                SQL.identifier(env[model_name]._table), sorted(res_ids)))
    except LockNotAvailable:
        return False
    return True


def _targets(env, model_name, kept_id, other_id):
    """Return the rows whose links move together: the record and, for a product, its variant.

    Each target is ``{model, table, kept, source, skip}``; ``skip`` is the ``(table, column)`` of a
    link that is not moved (the variant belongs to its template).
    """
    model = env[model_name]
    targets = [{'model': model_name, 'table': model._table, 'kept': kept_id, 'source': other_id, 'skip': None}]
    if model_name == 'product.template':
        Product = env['product.product'].with_context(active_test=False)
        kept_variant = Product.search([('product_tmpl_id', '=', kept_id)], limit=1)
        other_variant = Product.search([('product_tmpl_id', '=', other_id)], limit=1)
        targets[0]['skip'] = _owned_link(env, model_name)
        targets.append({
            'model': 'product.product', 'table': Product._table, 'kept': kept_variant.id,
            'source': other_variant.id, 'skip': None})
    return targets


def _reference_fields(env):
    """Return ``(model, field)`` of the stored reference fields of business models."""
    excluded = ('data.quality.', 'ir.model.data')
    return [
        (model, field) for name, model in env.registry.items()
        if model._auto and not model._transient and not name.startswith(excluded)
        for field in model._fields.values()
        if field.store and field.type in ('many2one_reference', 'reference')
    ]


def _not_duplicate(env, table, column, kept_id, alias='t'):
    """Return the SQL condition that a row does not repeat one the record kept already has."""
    clauses = []
    for columns in _unique_key_columns(env, table, column):
        same = SQL(' AND ').join(
            SQL("k.%s = %s.%s", SQL.identifier(name), SQL.identifier(alias), SQL.identifier(name))
            for name in columns if name != column) or SQL('TRUE')
        clauses.append(SQL(
            "NOT EXISTS (SELECT 1 FROM %s k WHERE k.%s = %s AND %s)",
            SQL.identifier(table), SQL.identifier(column), kept_id, same))
    return SQL(' AND ').join(clauses) or SQL('TRUE')


def _link_entries(env, model_name, kept_id, other_id, ids):
    """Return the recorded description of every row that points to ``other_id``, not yet moved.

    Three kinds: ``fk`` (rows with an id behind a foreign key), ``rel`` (rows of a table without an
    id, kept by the value of the other column) and ``reference`` (rows that name a model and an id,
    or a ``model,id`` text). A row that only repeats a link of the record kept stays where it is.
    """
    entries = []
    for target in _targets(env, model_name, kept_id, other_id):
        source, kept = target['source'], target['kept']
        for table, column in foreign_keys(env, target['table']):
            if (table, column) == target['skip']:
                continue
            where = SQL("t.%s = %s", SQL.identifier(column), source)
            if table == env[model_name]._table:
                where = SQL("%s AND NOT (t.id = ANY(%s))", where, ids)
            columns = _columns(env, table)
            if _is_association(env, table):
                where = SQL("%s AND %s", where, _not_duplicate(env, table, column, kept))
            if 'id' in columns:
                env.cr.execute(SQL("SELECT t.id FROM %s t WHERE %s", SQL.identifier(table), where))
                found = [row[0] for row in env.cr.fetchall()]
                entry = {'kind': 'fk', 'ids': found}
            else:
                others = [name for name in columns if name != column]
                if len(others) != 1:
                    raise _Unrecordable(table)
                env.cr.execute(SQL(
                    "SELECT t.%s FROM %s t WHERE %s", SQL.identifier(others[0]), SQL.identifier(table), where))
                found = [row[0] for row in env.cr.fetchall()]
                entry = {'kind': 'rel', 'other_column': others[0], 'ids': found}
            if found:
                entries.append(dict(entry, table=table, column=column, source=source, target=kept))
        entries += _reference_entries(env, target)
    return entries


class _Unrecordable(Exception):
    """A table without an id that links more than one other column: its rows cannot be recorded."""


def _reference_entries(env, target):
    """Return the ``reference`` entries of the rows naming the target record by model and id."""
    entries = []
    source, kept = target['source'], target['kept']
    for model, field in _reference_fields(env):
        table, column = model._table, field.name
        if field.type == 'many2one_reference':
            where = SQL("t.%s = %s AND t.%s = %s", SQL.identifier(field.model_field), target['model'],
                        SQL.identifier(column), source)
            kind = 'ref_id'
        else:
            where = SQL("t.%s = %s", SQL.identifier(column), '%s,%s' % (target['model'], source))
            kind = 'ref_text'
        if table in MERGE_ASSOCIATION_TABLES:
            where = SQL("%s AND %s", where, _not_duplicate(env, table, column, kept))
        env.cr.execute(SQL("SELECT t.id FROM %s t WHERE %s", SQL.identifier(table), where))
        found = [row[0] for row in env.cr.fetchall()]
        if found:
            entries.append({
                'kind': kind, 'table': table, 'column': column, 'ids': found, 'source': source,
                'target': kept, 'model': target['model'],
                'model_column': getattr(field, 'model_field', False)})
    return entries


def _apply_entry(env, entry, forward=True):
    """Point the rows of ``entry`` to the record kept (``forward``) or back to the source."""
    to, frm = (entry['target'], entry['source']) if forward else (entry['source'], entry['target'])
    table, column = SQL.identifier(entry['table']), SQL.identifier(entry['column'])
    text = entry['kind'] == 'ref_text'
    new = '%s,%s' % (entry['model'], to) if text else to
    old = '%s,%s' % (entry['model'], frm) if text else frm
    if entry['kind'] == 'rel':
        where = SQL("t.%s = %s AND t.%s = ANY(%s)", column, frm, SQL.identifier(entry['other_column']), entry['ids'])
    else:
        where = SQL("t.id = ANY(%s) AND t.%s = %s", entry['ids'], column, old)
    if entry['table'] in MERGE_ASSOCIATION_TABLES or entry['kind'] == 'rel':
        where = SQL("%s AND %s", where, _not_duplicate(env, entry['table'], entry['column'], to))
    env.cr.execute(SQL("UPDATE %s t SET %s = %s WHERE %s", table, column, new, where))


def move_links(env, model_name, kept_id, other_ids):
    """Point every link of the other records to the record kept; return what moved.

    The answer is ``{other id: [entry, ...]}``, the record the merge history keeps to undo it. A
    unique key that two rows would then share stops the move with an error naming the table.

    Requirements: FR-DGQ-0043, BR-DGQ-0008
    """
    env.flush_all()
    moved = {}
    ids = [kept_id, *other_ids]
    for other_id in other_ids:
        entries = _link_entries(env, model_name, kept_id, other_id, ids)
        for entry in entries:
            _apply_entry(env, entry)
        moved[str(other_id)] = entries
    env.invalidate_all()
    return moved


def move_links_back(env, entries):
    """Point the recorded rows that still point to the record kept back to their source.

    Requirements: FR-DGQ-0045
    """
    env.flush_all()
    for entry in entries:
        _apply_entry(env, entry, forward=False)
    env.invalidate_all()


def storable(value):
    """Return ``value`` as plain JSON data (dates as text)."""
    return json.loads(json.dumps(value, default=json_default))


def field_values(record, field_names):
    """Return ``{field: value as written}`` of the record, ready to store."""
    return {
        name: storable(record._fields[name].convert_to_write(record[name], record))
        for name in field_names}


def refusal_of_contacts(env, records):
    """Return why contacts cannot be merged, or None: a company with its own contact, several users.

    Requirements: FR-DGQ-0044
    """
    if records.filtered(lambda record: record.parent_id in records):
        return env._("A company contact cannot be merged with one of its own contacts.")
    users = env['res.users'].sudo().with_context(active_test=False).search([('partner_id', 'in', records.ids)])  # sudo: the manager may not read users; only the count of users is used
    if len(users.partner_id) > 1:
        return env._("Contacts of more than one user cannot be merged.")
    return None


def refusal_of_products(env, records):
    """Return why product templates cannot be merged, or None: several variants, different units.

    Requirements: FR-DGQ-0044
    """
    variants = records.with_context(active_test=False).product_variant_ids
    if any(len(record.with_context(active_test=False).product_variant_ids) > 1 for record in records) or (
            len(variants) != len(records)):
        return env._("Products with several variants cannot be merged.")
    if len(records.uom_id) > 1:
        return env._("Products with different units of measure cannot be merged.")
    return None


BUILTIN_STRATEGIES = {
    'res.partner': {'key': 'res.partner', 'model': 'res.partner', 'handler': refusal_of_contacts},
    'product.template': {
        'key': 'product.template', 'model': 'product.template', 'handler': refusal_of_products},
}


def strategy_of(env, model_name):
    """Return the merge strategy of a model: the one an installed module registers, else the app's own.

    A strategy is a dict with ``handler(env, records)``, which answers the reason a merge of the
    records is refused or ``None``; ``reversible`` set to ``False`` makes its merges irreversible.
    A model with none has only the generic link move.

    Requirements: FR-DGQ-0044, BR-DGQ-0008
    """
    registered = next((
        descriptor for descriptor in env['data.quality.registry']._dgq_descriptors('merge_strategies')
        if descriptor.get('model') == model_name), None)
    return registered or BUILTIN_STRATEGIES.get(model_name)
