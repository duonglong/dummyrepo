"""Terms snapshot and difference view of a purchase contract.

Requirements: FR-SPM-0065
"""
from markupsafe import Markup, escape

EDITABLE_STATES = ('draft', 'revision')
TERM_FIELDS = (
    'reference', 'date_start', 'date_end', 'notice_days', 'currency_id', 'payment_term_id',
    'scope_kind', 'product_category_ids', 'service_description', 'ceiling_value', 'ceiling_qty',
)
LINE_FIELDS = (
    'product_id', 'product_category_id', 'price_unit', 'uom_id', 'discount', 'min_qty',
    'lead_time_days', 'ceiling_qty',
)


def _plain(record, name):
    """Return the JSON-ready value of a field of one record."""
    value = record[name]
    kind = record._fields[name].type
    if kind == 'many2one':
        return value.id
    if kind == 'many2many':
        return sorted(value.ids)
    if kind == 'date':
        return value.isoformat() if value else False
    return value


def build_terms(contract):
    """Return the terms and lines of ``contract`` as plain data.

    Requirements: FR-SPM-0065
    """
    contract.ensure_one()
    terms = {name: _plain(contract, name) for name in TERM_FIELDS}
    terms['lines'] = [
        dict({name: _plain(line, name) for name in LINE_FIELDS}, id=line.id)
        for line in contract.line_ids.sorted('id')
    ]
    return terms


def _ids(value):
    """Return the record ids held by a snapshot value of a relational field."""
    return value if isinstance(value, list) else [value] if value else []


def _labels(env, wanted):
    """Return the display names of ``wanted`` ({model: ids}) with one read per model."""
    names = {}
    for model, ids in wanted.items():
        for record in env[model].browse(sorted(ids)).exists():
            names[model, record.id] = record.display_name
    return names


def _show(names, model, value):
    """Return the text of a snapshot value: names of the records of a relation, else the value."""
    if not model:
        return value
    return ", ".join(names.get((model, id_), str(id_)) for id_ in _ids(value))


def _line_changes(old_lines, new_lines):
    """Pair the lines of two snapshots by line id; a line seen once is added or removed."""
    old_by_id = {line.get('id'): line for line in old_lines}
    pairs = [(old_by_id.pop(line.get('id'), {}), line) for line in new_lines]
    return [(line, {}) for line in old_by_id.values()] + pairs


def diff_rows(env, old, new):
    """Return ``(label, before, after)`` for every term that differs between two snapshots.

    Requirements: FR-SPM-0065
    Lines are paired by their id, so a removed line is a removal and never a price change.
    """
    raw = []
    contract_fields = env['procurement.contract']._fields
    for name in TERM_FIELDS:
        if old.get(name) != new.get(name):
            field = contract_fields[name]
            raw.append((field.string, field.comodel_name if field.relational else None,
                        old.get(name), new.get(name)))
    line_fields = env['procurement.contract.line']._fields
    for before, after in _line_changes(old.get('lines', []), new.get('lines', [])):
        product = (after or before).get('product_id')
        for name in LINE_FIELDS:
            if before.get(name) != after.get(name):
                field = line_fields[name]
                raw.append(((product, field.string), field.comodel_name if field.relational else None,
                            before.get(name), after.get(name)))
    wanted = {}
    for label, model, before, after in raw:
        if model:
            wanted.setdefault(model, set()).update(_ids(before) + _ids(after))
        if isinstance(label, tuple) and label[0]:
            wanted.setdefault('product.product', set()).add(label[0])
    names = _labels(env, wanted)
    rows = []
    for label, model, before, after in raw:
        if isinstance(label, tuple):
            item = names.get(('product.product', label[0])) or env._("Line")
            label = f"{item}: {label[1]}"
        rows.append((label, _show(names, model, before), _show(names, model, after)))
    return rows


def diff_html(env, old, new):
    """Render the differences between two snapshots as an HTML table.

    Requirements: FR-SPM-0065
    """
    rows = diff_rows(env, old, new)
    if not rows:
        return Markup('')
    body = Markup('').join(
        Markup('<tr><td>%s</td><td>%s</td><td>%s</td></tr>') % (escape(label), escape(before), escape(after))
        for label, before, after in rows)
    return Markup('<table class="table table-sm"><tbody>%s</tbody></table>') % body
