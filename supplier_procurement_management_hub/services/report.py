"""Figures the report views cannot hold in a database view.

Requirements: FR-SPM-0100, FR-SPM-0102, FR-SPM-0103
"""
from odoo import fields
from odoo.exceptions import UserError

BASELINE_KINDS = [
    ('first_average', 'Average of first offers'), ('last_price', 'Last purchase price'), ('budget', 'Budget')]
ORDER_CONFIRMED_STATES = ('purchase', 'done')


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


def _first_offers(env, line):
    """The first offer of each response for an event line, in the order they were answered."""
    offers = env['procurement.sourcing.response.line'].search([('event_line_id', '=', line.id)])
    first = {}
    for offer in offers.sorted(lambda o: (o.version_id.version, o.id)):
        first.setdefault(offer.version_id.response_id, offer)
    return list(first.values())


def _last_price_total(env, line, company):
    """The total of an event line at the price of the last confirmed purchase of its product."""
    last = env['purchase.order.line'].search([
        ('product_id', '=', line.product_id.id), ('company_id', '=', company.id),
        ('order_id.state', 'in', ORDER_CONFIRMED_STATES), ('product_qty', '>', 0)],
        order='date_order desc, id desc', limit=1)
    if not last:
        raise UserError(env._("There is no earlier purchase of %(product)s: choose another baseline.",
                              product=line.product_id.display_name or line.name))
    # the order's own currency rate converts the price to the company currency
    unit = last.price_subtotal / last.product_qty / (last.order_id.currency_rate or 1.0)
    return unit * line.quantity


def award_savings(event, choices, kind, budget=0.0):
    """Return the baseline, the awarded total and the savings of an award, in the company currency.

    Requirements: FR-SPM-0103
    ``choices`` maps an event line to the response that wins it. The baseline is the average first
    offer of every response that quoted the line (default), the price of the last purchase of the
    product, or the budget the buyer gave; savings are the baseline minus the awarded total.
    """
    env = event.env
    company = event.company_id
    target = company.currency_id
    today = fields.Date.context_today(event)

    def convert(amount, currency):
        return currency._convert(amount, target, company, today)

    awarded = sum(
        convert(offer.price_subtotal, offer.currency_id)
        for line, response in choices.items()
        for offer in response.current_version_id.line_ids if offer.event_line_id == line)
    if kind == 'budget':
        if budget <= 0:
            raise UserError(env._("State the budget the award is compared with."))
        baseline = budget
    elif kind == 'last_price':
        baseline = sum(_last_price_total(env, line, company) for line in choices)
    else:
        baseline = 0.0
        for line in choices:
            offers = _first_offers(env, line)
            baseline += sum(convert(o.price_subtotal, o.currency_id) for o in offers) / len(offers)
    return {'baseline_kind': kind, 'budget_amount': budget if kind == 'budget' else 0.0,
            'baseline_amount': baseline, 'awarded_amount': awarded, 'savings_amount': baseline - awarded}
