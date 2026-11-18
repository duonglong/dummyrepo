"""Matching of vendor bills with orders and receipts (FR-SPM-0071 to FR-SPM-0074).

Plain functions: they take an environment and records and hold no state. They
read bills and write only the hub's own reconciliation records (ADR-SPM-006).
"""
import logging

from odoo import fields
from odoo.tools import float_compare, float_is_zero, float_round

_logger = logging.getLogger(__name__)

BILL_TYPES = ('in_invoice', 'in_refund')


def _rank(record, partner, category):
    """Return how specific a policy or tolerance is: 0 vendor, 1 category, 2 company default."""
    if record.partner_id:
        return 0
    return 1 if record.product_category_id else 2


def _most_specific(records, partner, category):
    """Return the most specific of the records that apply to the vendor and category."""
    candidates = records.filtered(
        lambda r: (not r.partner_id or r.partner_id == partner)
        and (not r.product_category_id or r.product_category_id == category))
    return min(candidates, key=lambda r: (_rank(r, partner, category), r.id), default=records.browse())


def policy_method(env, company, partner, product):
    """Return ``two_way`` or ``three_way`` for a vendor and product.

    Requirements: FR-SPM-0071
    The most specific active policy wins (vendor, then category, then company
    default); without one, goods are matched three-way and services two-way.
    """
    policies = env['procurement.matching.policy'].search([('company_id', '=', company.id)])
    policy = _most_specific(policies, partner, product.categ_id)
    if policy:
        return policy.method
    return 'three_way' if product.type == 'consu' else 'two_way'


def tolerance_for(env, company, kind, partner, category, bill_total):
    """Return the tolerance that applies, or an empty recordset.

    Requirements: BR-SPM-0014
    The most specific active tolerance (vendor, category, company default)
    whose amount band contains the bill total.
    """
    tolerances = env['procurement.matching.tolerance'].search([
        ('company_id', '=', company.id), ('kind', '=', kind),
        ('amount_from', '<=', bill_total), ('amount_to', '>=', bill_total)])
    return _most_specific(tolerances, partner, category)


def within_tolerance(tolerance, difference, base, currency):
    """Return whether a difference of amount is accepted by the tolerance.

    Requirements: BR-SPM-0014
    Accepted when within the absolute amount or the percentage of the base
    amount; a difference of zero is always accepted.
    """
    gap = abs(difference)
    if float_is_zero(gap, precision_rounding=currency.rounding):
        return True
    if not tolerance:
        return False
    rounding = currency.rounding
    return (float_compare(gap, tolerance.absolute_amount, precision_rounding=rounding) <= 0
            or float_compare(gap, abs(base) * tolerance.percent / 100.0, precision_rounding=rounding) <= 0)


def _line_values(env, bill, move_line, bill_total):
    """Return the reconciliation line values of one bill line."""
    purchase_line = move_line.purchase_line_id
    order = purchase_line.order_id
    company = bill.company_id
    vendor = order.partner_id.commercial_partner_id
    category = move_line.product_id.categ_id
    method = policy_method(env, company, vendor, move_line.product_id)
    ordered_uom, billed_uom = purchase_line.uom_id, move_line.product_uom_id
    billed_qty = move_line.quantity
    if ordered_uom and billed_uom and billed_uom != ordered_uom and billed_uom._has_common_reference(ordered_uom):
        billed_qty = billed_uom._compute_quantity(billed_qty, ordered_uom, raise_if_failure=False)
    currency, order_currency = bill.currency_id, order.currency_id
    ordered_price = purchase_line.price_unit
    if order_currency != currency:
        ordered_price = order_currency._convert(
            ordered_price, currency, company, bill.invoice_date or bill.date or fields.Date.context_today(bill))
    price_precision = env['decimal.precision'].precision_get('Product Price')
    price_difference = float_round(move_line.price_unit - ordered_price, precision_digits=price_precision)
    amount_difference = currency.round(price_difference * billed_qty)
    received_qty = purchase_line.qty_received
    ordered_qty = purchase_line.product_qty
    reference_qty = received_qty if method == 'three_way' else ordered_qty
    qty_difference = ordered_uom.round(billed_qty - reference_qty)
    price_tolerance = tolerance_for(env, company, 'price', vendor, category, bill_total)
    qty_tolerance = tolerance_for(env, company, 'quantity', vendor, category, bill_total)
    qty_gap = max(qty_difference, 0.0)
    return {
        'purchase_line_id': purchase_line.id,
        'product_id': move_line.product_id.id,
        'method': method,
        'ordered_qty': ordered_qty,
        'received_qty': received_qty,
        'billed_qty': billed_qty,
        'ordered_price': purchase_line.price_unit,
        'billed_price': move_line.price_unit,
        'currency_id': currency.id,
        'order_currency_id': order_currency.id,
        'ordered_tax_ids': [(6, 0, purchase_line.tax_ids.ids)],
        'billed_tax_ids': [(6, 0, move_line.tax_ids.ids)],
        'ordered_uom_id': ordered_uom.id,
        'billed_uom_id': billed_uom.id,
        'price_difference': price_difference,
        'qty_difference': qty_difference,
        'amount_difference': amount_difference,
        'price_within_tolerance': within_tolerance(
            price_tolerance, amount_difference, ordered_price * billed_qty, currency),
        'qty_within_tolerance': within_tolerance(
            qty_tolerance, qty_gap * ordered_price, reference_qty * ordered_price, currency),
    }


def _refresh_one(env, bill):
    """Create or refresh the reconciliation of one bill."""
    reconciliation = env['procurement.reconciliation'].search([('move_id', '=', bill.id)])
    move_lines = bill.invoice_line_ids.filtered('purchase_line_id')
    if not move_lines:
        reconciliation.unlink()
        return
    bill_total = abs(bill.amount_total_signed)
    values = {move_line: _line_values(env, bill, move_line, bill_total) for move_line in move_lines}
    methods = {line_values['method'] for line_values in values.values()}
    if not reconciliation:
        reconciliation = env['procurement.reconciliation'].create({'move_id': bill.id})
    existing = {line.move_line_id: line for line in reconciliation.line_ids}
    reconciliation.line_ids.filtered(lambda line: line.move_line_id not in values).unlink()
    for move_line, line_values in values.items():
        if move_line in existing:
            existing[move_line].write(line_values)
        else:
            env['procurement.reconciliation.line'].create(
                dict(line_values, reconciliation_id=reconciliation.id, move_line_id=move_line.id))
    review = any(not (line.price_within_tolerance and line.qty_within_tolerance)
                 or line.ordered_tax_ids != line.billed_tax_ids
                 or line.currency_id != line.order_currency_id
                 or not _same_unit(line)
                 for line in reconciliation.line_ids)
    reconciliation.write({
        'method': 'three_way' if 'three_way' in methods else 'two_way',
        'state': 'review' if review else 'matched',
        'refreshed_date': fields.Datetime.now(),
    })


def _same_unit(line):
    """Return whether the billed unit equals the ordered one or converts to it."""
    ordered, billed = line.ordered_uom_id, line.billed_uom_id
    return not ordered or not billed or ordered == billed or billed._has_common_reference(ordered)


def refresh(env, bills):
    """Create or refresh the reconciliation of each matchable draft vendor bill.

    Requirements: FR-SPM-0072, FR-SPM-0073, FR-SPM-0074
    Each bill runs in a savepoint: a matching error is logged and never blocks
    the save of the bill.
    """
    for bill in bills.filtered(lambda move: move.move_type in BILL_TYPES and move.state == 'draft'):
        try:
            with env.cr.savepoint():
                _refresh_one(env, bill)
        except Exception:  # noqa: BLE001 - matching must never block saving a bill
            _logger.exception("Matching of bill %s failed", bill.id)
            _mark_error(env, bill)


def _mark_error(env, bill):
    """Mark the reconciliation of the bill as failed, when the user may write it."""
    try:
        with env.cr.savepoint():
            reconciliation = env['procurement.reconciliation'].search([('move_id', '=', bill.id)])
            reconciliation.write({'state': 'error', 'refreshed_date': fields.Datetime.now()})
    except Exception:  # noqa: BLE001 - nothing more to do for the user
        _logger.warning("Matching error of bill %s could not be recorded", bill.id)
