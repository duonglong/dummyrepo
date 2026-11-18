"""Matching of vendor bills with orders and receipts (FR-SPM-0071 to FR-SPM-0074).

Plain functions: they take an environment and records and hold no state. They
read bills and write only the hub's own reconciliation records (ADR-SPM-006).
"""
import logging
from datetime import timedelta

from odoo import fields
from odoo.fields import Domain
from odoo.sql_db import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY
from odoo.tools import float_compare, float_is_zero, float_round

_logger = logging.getLogger(__name__)

BILL_TYPES = ('in_invoice', 'in_refund')


def _most_specific(records, partner, category):
    """Return the most specific of the records that apply to the vendor and category.

    A vendor rule beats a category rule, which beats the company default.
    """
    candidates = records.filtered(
        lambda r: (not r.partner_id or r.partner_id == partner)
        and (not r.product_category_id or r.product_category_id == category))
    return min(
        candidates,
        key=lambda r: (0 if r.partner_id else 1 if r.product_category_id else 2, r.id),
        default=records.browse())


def policy_method(policies, partner, product) -> str:
    """Return ``two_way`` or ``three_way`` for a vendor and product.

    Requirements: FR-SPM-0071
    Services have no receipt and are always matched two-way. For goods the most
    specific policy of ``policies`` wins (vendor, then category, then company
    default); without one, goods are matched three-way.
    """
    if product.type != 'consu':
        return 'two_way'
    policy = _most_specific(policies, partner, product.categ_id)
    return policy.method if policy else 'three_way'


def tolerance_for(tolerances, kind, partner, category):
    """Return the tolerance of a kind that applies, or an empty recordset.

    Requirements: BR-SPM-0014
    ``tolerances`` are the company's active tolerances whose amount band already
    contains the bill total; the most specific one wins (vendor, category,
    company default).
    """
    return _most_specific(tolerances.filtered(lambda t: t.kind == kind), partner, category)


def within_tolerance(tolerance, difference, base, currency, company, date) -> bool:
    """Return whether a difference is accepted by the tolerance.

    Requirements: BR-SPM-0014
    ``difference`` and ``base`` are in ``currency``. Accepted when within the
    absolute amount (company currency, converted to ``currency``) or the
    percentage of the base; a difference of zero is always accepted.
    """
    gap = abs(difference)
    rounding = currency.rounding
    if float_is_zero(gap, precision_rounding=rounding):
        return True
    if not tolerance:
        return False
    absolute_limit = company.currency_id._convert(tolerance.absolute_amount, currency, company, date)
    return (float_compare(gap, absolute_limit, precision_rounding=rounding) <= 0
            or float_compare(gap, abs(base) * tolerance.percent / 100.0, precision_rounding=rounding) <= 0)


def _line_values(env, bill, move_line, policies, tolerances) -> dict:
    """Return the reconciliation line values of one bill line."""
    purchase_line = move_line.purchase_line_id
    order = purchase_line.order_id
    company = bill.company_id
    vendor = order.partner_id.commercial_partner_id
    category = move_line.product_id.categ_id
    date = bill.invoice_date or bill.date or fields.Date.context_today(bill)
    method = policy_method(policies, vendor, move_line.product_id)
    ordered_uom, billed_uom = purchase_line.uom_id, move_line.product_uom_id
    billed_qty = ordered_billed_qty = move_line.quantity
    # price per ordered unit of the net (after discount) billed price
    billed_price = billed_net = move_line.price_unit * (1 - move_line.discount / 100.0)
    units_convert = True
    if ordered_uom and billed_uom and billed_uom != ordered_uom:
        units_convert = _units_convert(ordered_uom, billed_uom)
        if units_convert:
            ordered_billed_qty = billed_uom._compute_quantity(billed_qty, ordered_uom, raise_if_failure=False)
            billed_net = billed_uom._compute_price(billed_net, ordered_uom)
    currency, order_currency = bill.currency_id, order.currency_id
    ordered_price = purchase_line.price_unit_discounted
    if order_currency != currency:
        ordered_price = order_currency._convert(ordered_price, currency, company, date)
    price_precision = env['decimal.precision'].precision_get('Product Price')
    price_difference = float_round(billed_net - ordered_price, precision_digits=price_precision)
    amount_difference = currency.round(price_difference * ordered_billed_qty)
    received_qty = purchase_line.qty_received
    ordered_qty = purchase_line.product_qty
    reference_qty = received_qty if method == 'three_way' else ordered_qty
    qty_difference = ordered_uom.round(ordered_billed_qty - reference_qty) if units_convert else 0.0
    price_tolerance = tolerance_for(tolerances, 'price', vendor, category)
    qty_tolerance = tolerance_for(tolerances, 'quantity', vendor, category)
    qty_gap = max(qty_difference, 0.0)
    return {
        'purchase_line_id': purchase_line.id,
        'product_id': move_line.product_id.id,
        'method': method,
        'ordered_qty': ordered_qty,
        'received_qty': received_qty,
        'billed_qty': billed_qty,
        'ordered_price': purchase_line.price_unit_discounted,
        'billed_price': billed_price,
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
            price_tolerance, amount_difference, ordered_price * ordered_billed_qty, currency, company, date),
        'qty_within_tolerance': within_tolerance(
            qty_tolerance, qty_gap * ordered_price, reference_qty * ordered_price, currency, company, date),
    }


def _refresh_one(env, bill) -> None:
    """Create or refresh the reconciliation and the exceptions of one bill."""
    reconciliation = env['procurement.reconciliation'].search([('move_id', '=', bill.id)])
    move_lines = bill.invoice_line_ids.filtered('purchase_line_id')
    duplicates = find_duplicates(bill)
    if not move_lines and not duplicates and not reconciliation.duplicate_cleared:
        reconciliation.unlink()
        return
    bill_total = abs(bill.amount_total_signed)
    policies = env['procurement.matching.policy'].search([('company_id', '=', bill.company_id.id)])
    tolerances = env['procurement.matching.tolerance'].search([
        ('company_id', '=', bill.company_id.id), ('amount_from', '<=', bill_total),
        '|', ('amount_to', '=', 0), ('amount_to', '>=', bill_total)])
    values = {move_line: _line_values(env, bill, move_line, policies, tolerances) for move_line in move_lines}
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
    flagged = duplicates - reconciliation.duplicate_reviewed_move_ids
    cleared = reconciliation.duplicate_cleared and not flagged
    reconciliation.write({
        'method': ('three_way' if 'three_way' in methods else 'two_way') if methods else False,
        'duplicate_move_ids': [(6, 0, flagged.ids)],
        'duplicate_cleared': cleared,
        'refreshed_date': fields.Datetime.now(),
    })
    wanted = _exception_values(env, reconciliation, flagged)
    env['procurement.reconciliation.exception']._spm_sync(reconciliation, wanted)
    reconciliation.state = 'review' if reconciliation._spm_undecided_exceptions() else 'matched'


def find_duplicates(bill):
    """Return the other vendor bills of the vendor that the bill may repeat.

    Requirements: BR-SPM-0015
    Cancelled bills are left out. A bill repeats another with the same vendor
    reference, or with the same total and currency and an invoice date within
    the company's number of days.
    """
    others = [
        ('id', '!=', bill.id), ('partner_id', '=', bill.partner_id.id), ('company_id', '=', bill.company_id.id),
        ('move_type', '=', bill.move_type), ('state', '!=', 'cancel'),
    ]
    ref = (bill.ref or '').strip()
    terms = []
    if ref:
        terms.append([('ref', '=', ref)])
    if bill.invoice_date:
        window = timedelta(days=bill.company_id.spm_duplicate_bill_days)
        terms.append([
            ('amount_total', '=', bill.amount_total), ('currency_id', '=', bill.currency_id.id),
            ('invoice_date', '>=', bill.invoice_date - window), ('invoice_date', '<=', bill.invoice_date + window)])
    if not bill.partner_id or not terms:
        return bill.browse()
    return bill.env['account.move'].search(Domain(others) & Domain.OR(terms))


def _label(amount, currency) -> str:
    return f"{amount:.2f} {currency.name}"


def _move_label(move) -> str:
    return move.name if move.name and move.name != '/' else (move.ref or move.display_name)


def _exception_values(env, reconciliation, duplicates) -> dict:
    """Return the discrepancies of a reconciliation, by ``(kind, line id)``.

    Requirements: FR-SPM-0073, FR-SPM-0074, FR-SPM-0075, FR-SPM-0076
    """
    wanted = {}
    currency = reconciliation.move_id.currency_id
    for line in reconciliation.line_ids:
        bill_line, purchase_line = line.move_line_id, line.purchase_line_id
        base = {
            'recon_line_id': line.id, 'move_line_id': bill_line.id, 'purchase_line_id': purchase_line.id, 'order': purchase_line.order_id,
            'stock_move_ids': [(6, 0, purchase_line.move_ids.ids)],
        }
        found = {}
        if not line.price_within_tolerance:
            found['price'] = (_label(line.ordered_price, line.order_currency_id),
                              _label(line.billed_price, line.currency_id), abs(line.amount_difference))
        if not line.qty_within_tolerance:
            reference = line.received_qty if line.method == 'three_way' else line.ordered_qty
            found['quantity'] = (str(reference), str(line.billed_qty),
                                 max(line.qty_difference, 0.0) * line.billed_price)
        if line.ordered_tax_ids != line.billed_tax_ids:
            found['tax'] = (', '.join(line.ordered_tax_ids.mapped('name')) or '-',
                            ', '.join(line.billed_tax_ids.mapped('name')) or '-', bill_line.price_subtotal)
        if line.currency_id != line.order_currency_id:
            found['currency'] = (line.order_currency_id.name, line.currency_id.name, bill_line.price_subtotal)
        if not _same_unit(line):
            found['unit'] = (line.ordered_uom_id.name, line.billed_uom_id.name, bill_line.price_subtotal)
        for kind, (expected, seen, amount) in found.items():
            wanted[(kind, bill_line.id)] = dict(
                base, expected_value=expected, found_value=seen,
                amount_at_stake=currency.round(amount))
    if duplicates:
        wanted[('duplicate', 0)] = {
            'expected_value': env._("No other bill with the same reference or amount"),
            'found_value': ', '.join(_move_label(move) for move in duplicates),
            'amount_at_stake': currency.round(reconciliation.move_id.amount_total),
        }
    return wanted


def _units_convert(ordered, billed) -> bool:
    """Return whether the billed unit equals the ordered one or converts to it."""
    return not ordered or not billed or ordered == billed or billed._has_common_reference(ordered)


def _same_unit(line) -> bool:
    """Return whether the line's billed unit converts to its ordered unit."""
    return _units_convert(line.ordered_uom_id, line.billed_uom_id)


def refresh(env, bills) -> None:
    """Create or refresh the reconciliation of each matchable draft vendor bill.

    Requirements: FR-SPM-0072, FR-SPM-0073, FR-SPM-0074
    The hub's own rows are written with elevated rights, whoever saved the
    bill. Each bill runs in a savepoint: a matching error never blocks the save
    of the bill; the reconciliation is then marked ``error``.
    """
    # sudo: the hub's own rows are written whoever saved the bill (FR-SPM-0072)
    bills = bills.sudo().filtered(lambda move: move.move_type in BILL_TYPES and move.state == 'draft')
    for bill in bills:
        try:
            with bill.env.cr.savepoint():
                _refresh_one(bill.env, bill)
        except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
            raise
        except Exception:  # noqa: BLE001 - matching must never block saving a bill
            _logger.exception("Matching of bill %s failed", bill.id)
            _mark_error(bill.env, bill)


def _mark_error(env, bill) -> None:
    """Record that matching the bill failed, dropping the figures of the last match."""
    reconciliation = env['procurement.reconciliation'].search([('move_id', '=', bill.id)])
    values = {'state': 'error', 'method': False, 'refreshed_date': fields.Datetime.now()}
    if reconciliation:
        reconciliation.line_ids.unlink()
        reconciliation.write(values)
    else:
        env['procurement.reconciliation'].create(dict(values, move_id=bill.id))
