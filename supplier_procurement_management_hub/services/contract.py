"""Terms snapshot and difference view of a purchase contract.

Requirements: FR-SPM-0065
"""
from markupsafe import Markup, escape

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
        {name: _plain(line, name) for name in LINE_FIELDS}
        for line in contract.line_ids.sorted('id')
    ]
    return terms


def _label(env, model, value):
    """Return the display name of a record id, or the plain value."""
    if model and value:
        record = env[model].browse(value).exists()
        return record.display_name if record else value
    return value


def diff_rows(env, old, new):
    """Return ``(label, before, after)`` for every term that differs between two snapshots.

    Requirements: FR-SPM-0065
    """
    rows = []
    contract_fields = env['procurement.contract']._fields
    for name in TERM_FIELDS:
        if old.get(name) != new.get(name):
            field = contract_fields[name]
            comodel = field.comodel_name if field.relational else None
            rows.append((field.string, _label(env, comodel, old.get(name)),
                         _label(env, comodel, new.get(name))))
    old_lines, new_lines = old.get('lines', []), new.get('lines', [])
    line_fields = env['procurement.contract.line']._fields
    for index in range(max(len(old_lines), len(new_lines))):
        before = old_lines[index] if index < len(old_lines) else {}
        after = new_lines[index] if index < len(new_lines) else {}
        item = _label(env, 'product.product', (after or before).get('product_id')) or env._("Line")
        for name in LINE_FIELDS:
            if before.get(name) != after.get(name):
                field = line_fields[name]
                comodel = field.comodel_name if field.relational else None
                rows.append((f"{item}: {field.string}", _label(env, comodel, before.get(name)),
                             _label(env, comodel, after.get(name))))
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
