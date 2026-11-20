"""Figures the report views cannot hold in a database view.

Requirements: FR-SPM-0100
"""
from odoo import fields


def price_trend(env, product, date_from, date_to):
    """Return, per supplier, the price paid for ``product`` in the period and its variance from the average.

    Requirements: FR-SPM-0100
    Prices come from the price report view (company currency per product unit) and are weighted by
    quantity: ``price`` is the supplier's average, ``period_average`` the average of every supplier
    and ``variance`` the first minus the second. Rows follow the caller's rights and companies.
    """
    # rows are per quarter and dated by its first day: a period reads every quarter it touches
    first_quarter = fields.Date.to_date(date_from).replace(day=1, month=(fields.Date.to_date(date_from).month - 1) // 3 * 3 + 1)
    lines = env['procurement.report.price'].search([
        ('product_id', '=', product.id), ('date', '>=', first_quarter), ('date', '<=', date_to)])
    total_quantity = sum(lines.mapped('quantity'))
    if not total_quantity:
        return []
    period_average = sum(line.price_unit * line.quantity for line in lines) / total_quantity
    rows = []
    for partner in lines.partner_id:
        own = lines.filtered(lambda line: line.partner_id == partner)
        quantity = sum(own.mapped('quantity'))
        price = sum(line.price_unit * line.quantity for line in own) / quantity if quantity else 0.0
        rows.append({
            'partner_id': partner.id, 'price': price, 'period_average': period_average,
            'variance': price - period_average})
    return rows
