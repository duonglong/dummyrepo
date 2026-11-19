"""Evaluation of the approval policies of a purchase order.

Requirements: BR-SPM-0009, FR-SPM-0043, FR-SPM-0044, FR-SPM-0045, FR-SPM-0046
"""
from dateutil.relativedelta import relativedelta

from odoo import fields
from odoo.exceptions import UserError

# States of the orders that count as bought or pending in a cumulative period.
COUNTED_STATES = ('purchase', 'to approve')


def _period_bounds(period, day):
    """Return the first and last day of the month, quarter or year holding ``day``."""
    if period == 'month':
        start = day.replace(day=1)
        return start, start + relativedelta(months=1, days=-1)
    if period == 'quarter':
        start = day.replace(month=3 * ((day.month - 1) // 3) + 1, day=1)
        return start, start + relativedelta(months=3, days=-1)
    start = day.replace(month=1, day=1)
    return start, day.replace(month=12, day=31)


def _order_day(order):
    return fields.Date.to_date(order.date_order) or fields.Date.context_today(order)


def _company_amount(order):
    """Return the total of an order in the currency of its company."""
    return order.currency_id._convert(
        order.amount_total, order.company_id.currency_id, order.company_id, _order_day(order))


def _cumulated_amount(order, period):
    """Return the order's amount plus the same vendor's counted orders of its company in the period."""
    start, end = _period_bounds(period, _order_day(order))
    # sudo: the evaluating user may see only their own orders, the sum covers every buyer's orders
    # of this vendor and company, which the domain below fixes.
    others = order.sudo().search([
        ('id', '!=', order.id), ('company_id', '=', order.company_id.id),
        ('partner_id', '=', order.partner_id.id), ('state', 'in', COUNTED_STATES),
        ('date_order', '>=', fields.Datetime.to_datetime(start)),
        ('date_order', '<', fields.Datetime.to_datetime(end + relativedelta(days=1)))])
    return _company_amount(order) + sum(_company_amount(other) for other in others)


def _matches_conditions(order, policy, amount):
    """Return whether every condition set on the policy holds for the order."""
    lines = order.order_line.filtered(lambda line: not line.display_type)
    if amount < policy.amount_min or (policy.amount_max and amount >= policy.amount_max):
        return False
    if policy.partner_ids and not (order.partner_id | order.partner_id.commercial_partner_id) & policy.partner_ids:
        return False
    if policy.product_ids and not lines.product_id & policy.product_ids:
        return False
    if policy.product_category_ids and not lines.product_id.categ_id & policy.product_category_ids:
        return False
    if policy.department_ids:
        requester = order.spm_requester_id or order.user_id
        departments = order.env['hr.employee'].search([
            ('user_id', '=', requester.id), ('company_id', '=', order.company_id.id)]).department_id
        if not departments & policy.department_ids:
            return False
    if policy.analytic_account_ids:
        used = {int(key) for line in lines for key in (line.analytic_distribution or {})
                for key in key.split(',')}
        if not used & set(policy.analytic_account_ids.ids):
            return False
    return True


def evaluate(env, order):
    """Return ``policies``, ordered ``steps`` and ``cumulated_amount`` for the order.

    Requirements: BR-SPM-0009, FR-SPM-0043, FR-SPM-0044, FR-SPM-0045, FR-SPM-0046
    Raises a UserError naming the step when a matching step has no active approver.
    """
    order.ensure_one()
    # sudo: a buyer may not read the approval configuration; the search is bound to the order's company.
    policies = env['procurement.approval.policy'].sudo().search([('company_id', '=', order.company_id.id)])
    order = order.sudo()
    own_amount = _company_amount(order)
    matched = env['procurement.approval.policy'].sudo()
    cumulated = own_amount
    for policy in policies:
        amount = own_amount
        if policy.cumulative_period != 'none':
            amount = _cumulated_amount(order, policy.cumulative_period)
        if _matches_conditions(order, policy, amount):
            matched |= policy
            cumulated = max(cumulated, amount) if policy.cumulative_period != 'none' else cumulated
    return {
        'policies': matched.with_env(env),
        'steps': _merged_steps(matched, order),
        'cumulated_amount': cumulated,
    }


def _merged_steps(policies, order):
    """Return the steps of the policies by policy then step sequence, same-approver steps of a sequence merged."""
    merged = {}
    for policy in policies.sorted(lambda p: (p.sequence, p.id)):
        for step in policy.step_ids.sorted(lambda s: (s.sequence, s.id)):
            approvers = step.spm_resolve_approvers(order)
            if not approvers:
                raise UserError(order.env._(
                    "The approval step %(step)s of the policy %(policy)s has no active approver.",
                    step=step.name, policy=policy.name))
            key = (step.sequence, frozenset(approvers))
            if key in merged:
                known = merged[key]
                known['name'] = f"{known['name']} / {step.name}"
                if step.mode == 'all':
                    known['mode'] = 'all'
                continue
            merged[key] = {'policy_id': policy.id, 'sequence': step.sequence, 'name': step.name,
                           'mode': step.mode, 'approver_ids': approvers}
    return list(merged.values())
