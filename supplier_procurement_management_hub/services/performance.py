"""Supplier performance metrics, the weighted score and its range (no state).

Requirements: BR-SPM-0012, BR-SPM-0013, FR-SPM-0079, FR-SPM-0080, FR-SPM-0081, FR-SPM-0082
Every computer reads with the caller's rights and returns one result dict: ``metric``,
``has_data``, ``value`` (0 to 100 for a share), ``numerator``, ``denominator``,
``missing_reason``, ``evidence_model`` and ``evidence_ids``.
"""
import calendar
from datetime import date, datetime, time, timedelta

from odoo import fields
from odoo.tools import float_compare

from . import delivery, sourcing

SCORE_MAX = 100.0
PERIOD_MONTHS = {'month': 1, 'quarter': 3, 'year': 12}
# Reasons of a discrepancy that reject the received goods.
REJECTING_REASONS = ('damaged', 'wrong_item')


def period_bounds(period, day):
    """Return the first and last date of the month, quarter or year that holds ``day``.

    Requirements: FR-SPM-0082
    """
    months = PERIOD_MONTHS[period]
    first_month = (day.month - 1) // months * months + 1
    last_month = first_month + months - 1
    return date(day.year, first_month, 1), date(day.year, last_month, calendar.monthrange(day.year, last_month)[1])


def _window(start, end):
    """The datetimes that bound the days ``start`` to ``end`` included."""
    return datetime.combine(start, time.min), datetime.combine(end + timedelta(days=1), time.min)


def _result(env, metric, numerator=0.0, denominator=0.0, evidence=None, missing_reason=False, value=None, **extra):
    """One metric result; no data when the denominator is 0."""
    has_data = bool(denominator)
    if value is None:
        value = SCORE_MAX * numerator / denominator if has_data else 0.0
    return dict({
        'metric': metric, 'has_data': has_data, 'value': value if has_data else 0.0,
        'numerator': numerator, 'denominator': denominator,
        'missing_reason': False if has_data else (missing_reason or env._("No source data in the period.")),
        'evidence_model': evidence._name if evidence is not None else False,
        'evidence_ids': evidence.ids if evidence is not None else [],
    }, **extra)


def _receipt_moves(env, partner, company, start, end):
    """The done stock moves of the supplier in the period, split into receipts and returns."""
    begin, finish = _window(start, end)
    moves = env['stock.move'].search([
        ('state', '=', 'done'), ('company_id', '=', company.id), ('date', '>=', begin), ('date', '<', finish),
        '|', ('purchase_line_id.order_id.partner_id', '=', partner.id),
        ('origin_returned_move_id.purchase_line_id.order_id.partner_id', '=', partner.id)])
    returns = moves.filtered(lambda move: move._is_purchase_return())
    return moves - returns, returns


def _in_line_unit(move, line):
    return move.uom_id._compute_quantity(move.quantity, line.uom_id or move.uom_id)


def on_time(env, partner, company, start, end):
    """Share of the receipt lines done in the period on or before the agreed date plus the tolerance.

    Requirements: FR-SPM-0079, BR-SPM-0012
    """
    receipts, _returns = _receipt_moves(env, partner, company, start, end)
    commitments = {c.order_line_id: c for c in env['procurement.delivery.commitment'].search(
        [('order_line_id', 'in', receipts.purchase_line_id.ids)])}
    tolerance = timedelta(days=company.spm_on_time_tolerance_days)

    def is_punctual(move):
        line = move.purchase_line_id
        commitment = commitments.get(line)
        agreed = delivery.agreed(commitment.confirmed_date, commitment.expected_date) if commitment else \
            delivery.agreed(False, line.date_planned)
        if not agreed:
            return False
        done_day = fields.Date.context_today(move, move.date)
        return done_day <= fields.Date.context_today(move, agreed) + tolerance

    punctual = receipts.filtered(is_punctual)
    return _result(env, 'on_time', len(punctual), len(receipts), receipts,
                   env._("No receipt line was done in the period."))


def fulfillment(env, partner, company, start, end):
    """Received quantity divided by the quantity due in the period, the lines due being the ones agreed in it.

    Requirements: FR-SPM-0080
    """
    begin, finish = _window(start, end)
    commitments = env['procurement.delivery.commitment'].search([
        ('partner_id', '=', partner.id), ('company_id', '=', company.id), ('state', '!=', 'cancel'),
        ('agreed_date', '>=', begin), ('agreed_date', '<', finish)])
    lines = commitments.order_line_id
    due = sum(lines.mapped('product_qty'))
    moves = env['stock.move'].search([
        ('purchase_line_id', 'in', lines.ids), ('state', '=', 'done'), ('date', '<', finish)])
    received = sum(_in_line_unit(move, move.purchase_line_id) for move in moves
                   if not move._is_purchase_return())
    return _result(env, 'fulfillment', received, due, lines, env._("No quantity was due in the period."))


def _rejected_discrepancies(env, partner, company, start, end):
    begin, finish = _window(start, end)
    return env['procurement.delivery.discrepancy'].search([
        ('partner_id', '=', partner.id), ('company_id', '=', company.id),
        ('create_date', '>=', begin), ('create_date', '<', finish)])


def quality(env, partner, company, start, end):
    """Received quantity minus returned and rejected quantity, divided by the received quantity.

    Requirements: FR-SPM-0080
    """
    receipts, returns = _receipt_moves(env, partner, company, start, end)
    received = sum(_in_line_unit(move, move.purchase_line_id) for move in receipts)
    returned = sum(move.uom_id._compute_quantity(move.quantity, move.origin_returned_move_id.uom_id)
                   for move in returns if move.origin_returned_move_id)
    rejected = sum(_rejected_discrepancies(env, partner, company, start, end).filtered(
        lambda d: d.kind in REJECTING_REASONS).mapped('quantity'))
    good = max(received - returned - rejected, 0.0)
    return _result(env, 'quality', good, received, receipts, env._("Nothing was received in the period."))


def complaints(env, partner, company, start, end):
    """Recorded complaints (delivery discrepancies) of the period, scored against the receipt lines done.

    Requirements: FR-SPM-0080
    The count is the value; the score falls from 100 by the share of receipt lines with a complaint.
    """
    receipts, _returns = _receipt_moves(env, partner, company, start, end)
    found = _rejected_discrepancies(env, partner, company, start, end)
    count = float(len(found))
    score = max(SCORE_MAX * (1 - count / len(receipts)), 0.0) if receipts else 0.0
    return _result(env, 'complaints', count, float(len(receipts)), found,
                   env._("No receipt line was done in the period."), value=score)


def _invitations(env, partner, company, start, end):
    """The responses of the supplier to the events it was invited to in the period."""
    begin, finish = _window(start, end)
    return env['procurement.sourcing.response'].search([
        ('partner_id', '=', partner.id), ('company_id', '=', company.id),
        ('event_id.state', '!=', 'cancelled'), ('create_date', '>=', begin), ('create_date', '<', finish)])


def responsiveness(env, partner, company, start, end):
    """Share of the invited events answered before the deadline, with the average answer time in hours.

    Requirements: FR-SPM-0081
    """
    responses = _invitations(env, partner, company, start, end)
    answered = env['procurement.sourcing.response']
    hours = []
    for response in responses:
        first = min(response.version_ids.mapped('submitted_date'), default=False)
        if first and (not response.effective_deadline or first <= response.effective_deadline):
            answered |= response
            hours.append((first - response.create_date).total_seconds() / 3600.0)
    return _result(env, 'responsiveness', len(answered), len(responses), responses,
                   env._("Not invited to any sourcing event in the period."),
                   average_answer_hours=sum(hours) / len(hours) if hours else 0.0)


def price_competitiveness(env, partner, company, start, end):
    """100 x the lowest comparable price divided by the supplier's price, averaged over its quoted lines.

    Requirements: FR-SPM-0081, BR-SPM-0007
    Only the lines of normalized events are compared; a line quoted by one supplier only scores 100.
    """
    ratios, lines = [], env['procurement.sourcing.response.line']
    for event in _invitations(env, partner, company, start, end).event_id.filtered('is_normalized'):
        prices = {}
        for response in event.response_ids.filtered('current_version_id'):
            for cell in sourcing._cells_of_response(env, event, response, True):
                if cell['row_kind'] == 'line' and not cell.get('missing') and cell['normalized_value'] > 0:
                    prices.setdefault(cell['event_line_id'], {})[response.partner_id.id] = cell['normalized_value']
        own = event.response_ids.filtered(lambda r: r.partner_id == partner).current_version_id
        for rline in own.line_ids:
            by_supplier = prices.get(rline.event_line_id.id, {})
            if partner.id in by_supplier:
                ratios.append(min(by_supplier.values()) / by_supplier[partner.id])
                lines |= rline
    return _result(env, 'price_competitiveness', sum(ratios), float(len(ratios)), lines,
                   env._("No comparable quoted line in the period."))


def weighted_score(results):
    """Return the weighted average score of the results with data and the metrics left out.

    Requirements: BR-SPM-0013
    ``results`` holds dicts with ``metric``, ``weight``, ``has_data`` and ``score``; the score is
    None when no metric with data has a weight.
    """
    counted = [row for row in results if row['has_data'] and row['weight'] > 0]
    left_out = [row['metric'] for row in results if not row['has_data']]
    total = sum(row['weight'] for row in counted)
    if not total:
        return None, left_out
    return sum(row['weight'] * row['score'] for row in counted) / total, left_out


def range_of(score, good_from, acceptable_from, rounding=0.001):
    """Return 'good', 'acceptable' or 'poor' for a score and the two limits (limits included).

    Requirements: FR-SPM-0082
    """
    if float_compare(score, good_from, precision_rounding=rounding) >= 0:
        return 'good'
    if float_compare(score, acceptable_from, precision_rounding=rounding) >= 0:
        return 'acceptable'
    return 'poor'


def evaluate(template, partner, start, end):
    """Compute every metric of the template for the supplier and period; return rows, score and range.

    Requirements: FR-SPM-0082, BR-SPM-0013
    """
    env = template.env
    computers = env['procurement.scorecard.template.metric']._spm_metric_computers()
    rows = []
    for metric in template.metric_ids:
        row = computers[metric.metric](env, partner, template.company_id, start, end)
        row['weight'] = metric.weight
        row['score'] = min(max(row['value'], 0.0), SCORE_MAX)
        rows.append(row)
    score, left_out = weighted_score(rows)
    return {
        'rows': rows, 'score': score, 'missing_metrics': left_out,
        'range': range_of(score, template.good_from, template.acceptable_from) if score is not None else False,
    }
