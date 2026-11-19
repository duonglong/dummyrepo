"""Terms snapshot and difference view of a purchase contract.

Requirements: FR-SPM-0065, BR-SPM-0011, FR-SPM-0068, FR-SPM-0069
"""
from collections import defaultdict
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import fields

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


def _ancestor_ids(category):
    """Return the ids of a product category and of all its parents."""
    return [int(part) for part in (category.parent_path or '').split('/') if part] or category.ids


def find_contract_lines(contract_lines, order_lines):
    """Match order lines with the active contract lines of their company.

    Requirements: FR-SPM-0068, FR-SPM-0069
    ``contract_lines`` is the model to search with (the caller picks the rights). Returns
    ``{order line: (own, others)}``: ``own`` the line of the supplier's contract ending first (a line
    of the product before a line of its category), ``others`` the lines of other suppliers' contracts.
    """
    order_lines = order_lines.filtered(lambda line: line.product_id and not line.display_type)
    result = {}
    if not order_lines:
        return result
    categories = order_lines.product_id.categ_id
    candidates = contract_lines.search([
        ('contract_id.state', '=', 'active'),
        ('company_id', 'in', order_lines.company_id.ids),
        '|', ('product_id', 'in', order_lines.product_id.ids),
        '&', ('product_id', '=', False),
        ('product_category_id', 'in', [pid for category in categories for pid in _ancestor_ids(category)]),
    ])
    for line in order_lines:
        wanted = _ancestor_ids(line.product_id.categ_id)
        fits = candidates.filtered(lambda cl: cl.company_id == line.company_id and (
            cl.product_id == line.product_id or (not cl.product_id and cl.product_category_id.id in wanted)))
        supplier = line.partner_id.commercial_partner_id
        own = fits.filtered(lambda cl: cl.contract_id.partner_id.commercial_partner_id == supplier).sorted(
            lambda cl: (cl.contract_id.date_end, not cl.product_id, cl.id))
        result[line] = (own[:1], fits - own)
    return result


def consumption(contract_lines):
    """Return ``{contract line id: (quantity, value)}`` consumed by the confirmed orders.

    Requirements: BR-SPM-0011, FR-SPM-0068
    The quantity of the confirmed order lines (a cancelled quantity is already taken off the
    line, a cancelled order is not counted) minus what was returned to the supplier, in the
    unit of the contract line; the value at the unit price of the order line, converted into the
    contract currency at the order date.
    """
    # sudo: a reader of contracts may lack the right to read orders and stock moves; only
    # quantities and prices of the lines linked to the contracts read are used
    order_lines = contract_lines.env['purchase.order.line'].sudo().search([
        ('spm_contract_line_id', 'in', contract_lines.ids), ('order_id.state', '=', 'purchase')])
    returned = defaultdict(float)
    moves = contract_lines.env['stock.move'].sudo().search([
        ('origin_returned_move_id.purchase_line_id', 'in', order_lines.ids),
        ('state', '=', 'done'), ('location_dest_id.usage', '=', 'supplier')])
    for move in moves:
        line = move.origin_returned_move_id.purchase_line_id
        returned[line.id] += move.uom_id._compute_quantity(move.quantity, line.uom_id)
    totals = {cl.id: [0.0, 0.0] for cl in contract_lines}
    for line in order_lines:
        contract_line = line.spm_contract_line_id
        net = line.product_qty - returned[line.id]
        date = (line.order_id.date_order or fields.Datetime.now()).date()
        value = line.currency_id._convert(net * line.price_unit, contract_line.currency_id, line.company_id, date)
        total = totals[contract_line.id]
        total[0] += line.uom_id._compute_quantity(net, contract_line.uom_id or line.uom_id)
        total[1] += value
    return {key: tuple(value) for key, value in totals.items()}


def deviation_warnings(contract_lines, order_lines):
    """Return ``{order line: warning text}`` for the lines that deviate from a contract.

    Requirements: FR-SPM-0069
    Never blocks: price above the contract price, quantity below the minimum, expected date
    earlier than the lead time allows, a ceiling that would be exceeded, and a product under
    another supplier's contract.
    """
    env = contract_lines.env
    matches = find_contract_lines(contract_lines, order_lines)
    own_lines = contract_lines.browse([own.id for own, _others in matches.values() if own])
    used = consumption(own_lines.sudo()) if own_lines else {}  # sudo: only quantities and prices of linked lines are read (see consumption)
    warnings = {}
    for line, (own, others) in matches.items():
        messages = []
        if own:
            contract = own.contract_id
            order_date = (line.order_id.date_order or fields.Datetime.now()).date()
            price = own.uom_id._compute_price(own.price_unit * (1 - own.discount / 100), line.uom_id)
            price = own.currency_id._convert(price, line.currency_id, line.company_id, order_date)
            if line.currency_id.compare_amounts(line.price_unit, price) > 0:
                messages.append(env._("The unit price %(price)s is above the contract price %(contract_price)s.",
                                      price=line.price_unit, contract_price=line.currency_id.round(price)))
            minimum = own.uom_id._compute_quantity(own.min_qty, line.uom_id)
            if line.uom_id.compare(line.product_qty, minimum) < 0:
                messages.append(env._("The quantity %(qty)s is below the minimum order quantity %(minimum)s.",
                                      qty=line.product_qty, minimum=minimum))
            if own.lead_time_days and line.date_planned and (
                    line.date_planned.date() < order_date + timedelta(days=own.lead_time_days)):
                messages.append(env._("The expected date is earlier than the contract lead time of %(days)s days allows.",
                                      days=own.lead_time_days))
            messages += _ceiling_messages(env, line, own, used.get(own.id, (0.0, 0.0)), contract)
        elif others:
            suppliers = ", ".join(sorted(set(others.contract_id.partner_id.mapped('display_name'))))
            messages.append(env._("%(product)s is under contract with %(supplier)s.",
                                  product=line.product_id.display_name, supplier=suppliers))
        warnings[line] = "\n".join(messages)
    return warnings


def _ceiling_messages(env, line, own, used, contract):
    """Return the warnings about the ceilings of the contract line and of the contract."""
    counted = line.spm_contract_line_id == own and line.order_id.state == 'purchase'
    qty = line.uom_id._compute_quantity(line.product_qty, own.uom_id)
    order_date = (line.order_id.date_order or fields.Datetime.now()).date()
    value = line.currency_id._convert(line.product_qty * line.price_unit, own.currency_id, line.company_id, order_date)
    extra_qty, extra_value = (0.0, 0.0) if counted else (qty, value)
    messages = []
    if own.ceiling_qty and own.uom_id.compare(used[0] + extra_qty, own.ceiling_qty) > 0:
        messages.append(env._("The order would exceed the quantity ceiling %(ceiling)s of the contract line.",
                              ceiling=own.ceiling_qty))
    if contract.ceiling_value or contract.ceiling_qty:
        totals = consumption(contract.line_ids.sudo()).values()  # sudo: only quantities and prices of linked lines are read (see consumption)
        if contract.ceiling_value and contract.currency_id.compare_amounts(
                sum(total[1] for total in totals) + extra_value, contract.ceiling_value) > 0:
            messages.append(env._("The order would exceed the value ceiling %(ceiling)s of the contract.",
                                  ceiling=contract.ceiling_value))
        if contract.ceiling_qty and sum(total[0] for total in totals) + extra_qty > contract.ceiling_qty:
            messages.append(env._("The order would exceed the quantity ceiling %(ceiling)s of the contract.",
                                  ceiling=contract.ceiling_qty))
    return messages
