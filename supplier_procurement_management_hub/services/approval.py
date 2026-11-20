"""Evaluation of the approval policies of a purchase order.

Requirements: BR-SPM-0009, FR-SPM-0043, FR-SPM-0044, FR-SPM-0045, FR-SPM-0046,
BR-SPM-0010, FR-SPM-0047, FR-SPM-0048, FR-SPM-0049, FR-SPM-0052, FR-SPM-0053, FR-SPM-0050,
FR-SPM-0051, FR-SPM-0054, BR-SPM-0019
"""
from datetime import timedelta

from dateutil.relativedelta import relativedelta
from markupsafe import Markup

from odoo import fields
from odoo.exceptions import AccessError, UserError

from . import audit

# States of the orders that count as bought or pending in a cumulative period.
COUNTED_STATES = ('purchase', 'to approve')
# Orders still in draft or sent, held for approval or approved: the cumulative periods count them and
# the material-change review watches them.
WATCHED_ORDER_STATES = ('draft', 'sent')
WATCHED_APPROVAL_STATES = ('pending', 'approved')
# Deferral marker of the line hooks inside an order save: compared by identity, so a caller cannot forge it.
REVIEW_DEFERRED = object()


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
    # The period is the current one: an RFQ with a deadline in another month is still counted against today's.
    start, end = _period_bounds(period, fields.Date.context_today(order))
    # sudo: the evaluating user may see only their own orders, the sum covers every buyer's orders
    # of this vendor and company, which the domain below fixes.
    others = order.sudo().search([
        ('id', '!=', order.id), ('company_id', '=', order.company_id.id),
        ('partner_id', '=', order.partner_id.id),
        '|', ('state', 'in', COUNTED_STATES),
        '&', ('state', 'in', WATCHED_ORDER_STATES), ('spm_approval_state', 'in', WATCHED_APPROVAL_STATES),
        ('date_order', '>=', fields.Datetime.to_datetime(start)),
        ('date_order', '<', fields.Datetime.to_datetime(end + relativedelta(days=1)))])
    return _company_amount(order) + sum(_company_amount(other) for other in others)


def _linked_requests(order):
    # sudo: the requester and department of the purchase requests an order was made from are read to
    # evaluate the policies; the buyer may not read every request, nothing of them is shown or written
    return order.sudo().spm_request_ids


def order_requester(order):
    """Return the user the policies treat as the requester of the order.

    Requirements: FR-SPM-0043, FR-SPM-0044, FR-SPM-0047
    The requester set on the order, else the single requester of its purchase requests, else the
    buyer (also when the requests come from several requesters).
    """
    if order.spm_requester_id:
        return order.spm_requester_id
    requesters = _linked_requests(order).requester_id
    return requesters if len(requesters) == 1 else order.user_id


def requester_department(order):
    """Return the department of the order's requester: that of its single request, else of the employee.

    Requirements: FR-SPM-0043, FR-SPM-0047
    """
    requests = _linked_requests(order)
    if not order.spm_requester_id and len(requests.requester_id) == 1 and len(requests.department_id) == 1:
        return requests.department_id
    return requester_employee(order).department_id


def requester_employee(order):
    """Return the employee of the order's requester (the buyer without one), none when there is no user.

    Requirements: FR-SPM-0043, FR-SPM-0044
    """
    requester = order_requester(order)
    if not requester:
        return order.env['hr.employee']
    return order.env['hr.employee'].search([
        ('user_id', '=', requester.id), ('company_id', '=', order.company_id.id)], limit=1)


def _matches_conditions(order, policy, amount):
    """Return whether every condition set on the policy holds for the order."""
    lines = order.order_line.filtered(lambda line: not line.display_type)
    if amount < policy.amount_min or (policy.amount_max and amount >= policy.amount_max):
        return False
    if policy.partner_ids and not (order.partner_id | order.partner_id.commercial_partner_id) & policy.partner_ids:
        return False
    if policy.product_ids and not lines.product_id & policy.product_ids:
        return False
    if policy.product_category_ids:
        # a category condition includes its subcategories (answer to FR-SPM-0043)
        categories = order.env['product.category'].search([('id', 'child_of', policy.product_category_ids.ids)])
        if not lines.product_id.categ_id & categories:
            return False
    if policy.department_ids:
        if not requester_department(order) & policy.department_ids:
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


# Submission, decisions, the confirmation guard and the material change ---------

def latest_request(order):
    """Return the newest approval request of the order (empty when it never had one)."""
    return order.sudo().spm_approval_request_ids.sorted('id')[-1:]  # sudo: reading only; a Buyer sees the requests of his own orders through the record rules, the check is the caller's


def is_approved(order):
    """Return whether the order's newest request is approved for the order's current revision and the order
    still matches the approved snapshot (so a change made while the line hooks were deferred cannot pass)."""
    request = latest_request(order)
    return (bool(request) and request.state == 'approved' and request.revision == order.spm_revision
            and not _differs_from_request(order, request) and not _total_beyond_tolerance(order))


def check_confirmable(orders):
    """Refuse the confirmation of a controlled order that is not approved for its current revision.

    Requirements: FR-SPM-0048
    """
    for order in orders.filtered(lambda o: o.spm_controlled and o.state in WATCHED_ORDER_STATES + ('to approve',)):
        if is_approved(order):
            continue
        state = latest_request(order).state
        reasons = {
            'pending': order.env._("approval is pending"),
            'rejected': order.env._("approval was rejected"),
        }
        raise UserError(order.env._(
            "The purchase order %(order)s cannot be confirmed: %(reason)s.",
            order=order.display_name, reason=reasons.get(state, order.env._("it needs an approval"))))


def _notify(order, partners, text):
    """Tell the partners about the order in its chatter (the note reaches their inbox)."""
    if partners:
        # sudo: the actor of an approval may not write on the order's thread; only this note is posted
        order.sudo().message_post(
            body=Markup("<p>%s</p>") % text, partner_ids=partners.ids, subtype_xmlid='mail.mt_note')


def _snapshot_lines(order):
    return [{
        'id': line.id, 'product_id': line.product_id.id, 'qty': line.product_qty,
        'price_unit': line.price_unit, 'analytic_distribution': line.analytic_distribution or {},
    } for line in order.order_line if not line.display_type]


def submit(order):
    """Submit a draft order for approval: evaluate the policies, freeze its revision and open the steps.

    Requirements: FR-SPM-0047, FR-SPM-0049
    The caller must be able to write the order; the request and the order's approval fields are
    then written with elevated rights because no role may write them directly.
    """
    order.ensure_one()
    order.check_access('write')
    if order.company_id not in order.env.companies:
        raise AccessError(order.env._("This order belongs to a company you cannot work in."))
    order.lock_for_update()
    order.invalidate_recordset(['state', 'spm_approval_state', 'spm_revision'])
    if order.state not in WATCHED_ORDER_STATES:
        raise UserError(order.env._("Only a draft order can be submitted for approval."))
    if order.spm_approval_state in WATCHED_APPROVAL_STATES:
        raise UserError(order.env._("This order is already submitted for approval."))
    revision = order.spm_revision + (1 if order.spm_approval_state == 'rejected' else 0)
    return _start(order, revision)


def _start(order, revision):
    """Evaluate the policies for the order's revision and open the request; return it (empty if none needed)."""
    env = order.env
    result = evaluate(env, order)
    # sudo: the approval fields and requests are written only here, after the caller's checks
    order = order.sudo()
    if not result['steps']:
        order.write({'spm_revision': revision, 'spm_controlled': False, 'spm_approval_state': 'not_required'})
        return env['procurement.approval.request']
    first = min(step['sequence'] for step in result['steps'])
    now = fields.Datetime.now()
    request = env['procurement.approval.request'].sudo().create({  # sudo: creating the request is allowed to no role; the caller's checks passed in submit
        'order_id': order.id, 'revision': revision, 'policy_ids': [(6, 0, result['policies'].ids)],
        'submitted_by_id': env.user.id, 'submitted_date': now,
        'snapshot_amount': order.amount_total, 'snapshot_currency_id': order.currency_id.id,
        'snapshot_partner_id': order.partner_id.id,
        'snapshot_payment_term_id': order.payment_term_id.id, 'snapshot_lines': _snapshot_lines(order),
        'cumulated_amount': result['cumulated_amount'],
        'step_ids': [(0, 0, {
            'policy_id': step['policy_id'], 'sequence': step['sequence'], 'name': step['name'],
            'mode': step['mode'], 'approver_ids': [(6, 0, step['approver_ids'])],
            'state': 'open' if step['sequence'] == first else 'waiting',
            'opened_date': now if step['sequence'] == first else False,
        }) for step in result['steps']],
    })
    order.write({'spm_revision': revision, 'spm_controlled': True, 'spm_approval_state': 'pending'})
    _tell_approvers(request, request.step_ids.filtered(lambda s: s.state == 'open'))
    return request


def _tell_approvers(request, steps):
    order = request.order_id
    _notify(order, steps.approver_ids.partner_id, order.env._(
        "Approval requested for %(order)s (revision %(revision)s).",
        order=order.display_name, revision=request.revision))


def decide(step, decision, text):
    """Record the session user's decision on an open step and move the request on.

    Requirements: FR-SPM-0049, FR-SPM-0053
    The id comes from the client: the step must exist, belong to an allowed company and have the
    user (or whom he stands for) among its approvers; a rejection needs a reason.
    """
    env = step.env
    step = step.sudo().exists()  # sudo: the entitlement below is the check, not the record rule
    if not step or step.company_id not in env.companies:
        raise AccessError(env._("You cannot decide on this step."))
    acting = step._spm_acting_for(env.user)
    if not acting:
        raise AccessError(env._("You are not an approver of this step."))
    request = step.request_id
    request.lock_for_update()
    step.invalidate_recordset(['state'])
    request.invalidate_recordset(['state'])
    if step.state != 'open' or request.state != 'pending':
        raise UserError(env._("This step is not open for a decision."))
    text = (text or '').strip()
    if decision == 'rejected' and not text:
        raise UserError(env._("A rejection needs a reason."))
    if acting in _decided_approvers(step):
        raise UserError(env._("You already decided on this step."))
    record = env['procurement.approval.decision'].sudo().create({  # sudo: no role creates a decision; the approver check above passed
        'step_id': step.id, 'approver_id': env.user.id,
        'on_behalf_of_id': acting.id if acting != env.user else False, 'decision': decision,
        'date': fields.Datetime.now(), 'comment': text or False, 'revision': request.revision,
        'amount': request.snapshot_amount, 'currency_id': request.snapshot_currency_id.id,
        'partner_id': request.snapshot_partner_id.id,
    })
    audit.log(env, 'approval', record, {}, {
        'decision': decision, 'step': step.name, 'revision': request.revision,
        'amount': request.snapshot_amount, 'on_behalf_of': record.on_behalf_of_id.id},
        text)
    if decision == 'rejected':
        _reject(request, step, text)
    elif step.mode == 'any' or step.approver_ids <= _decided_approvers(step):
        step.write({'state': 'approved'})
        _advance(request)
    return record


def _decided_approvers(step):
    """Return the approvers of the step who already approved (a delegate counts for his delegator)."""
    approvals = step.decision_ids.filtered(lambda d: d.decision == 'approved')
    return approvals.approver_id.browse(
        [(d.on_behalf_of_id or d.approver_id).id for d in approvals])


def _reject(request, step, reason):
    """Close the request as rejected, send the order back to draft and tell the buyer."""
    order = request.order_id
    step.write({'state': 'rejected'})
    request.step_ids.filtered(lambda s: s.state in ('open', 'waiting')).write({'state': 'cancelled'})
    request.write({'state': 'rejected', 'decided_date': fields.Datetime.now()})
    values = {'spm_approval_state': 'rejected'}
    if order.state in WATCHED_ORDER_STATES:
        values['state'] = 'draft'
    order.write(values)
    _notify(order, (order.user_id | request.submitted_by_id).partner_id, order.env._(
        "Approval of %(order)s was rejected by %(user)s: %(reason)s",
        order=order.display_name, user=order.env.user.name, reason=reason))


def _advance(request):
    """Open the next sequence once the current one is approved; approve the request after the last."""
    steps = request.step_ids
    if steps.filtered(lambda s: s.state == 'open'):
        return
    waiting = steps.filtered(lambda s: s.state == 'waiting')
    if waiting:
        following = waiting.filtered(lambda s: s.sequence == min(waiting.mapped('sequence')))
        following.write({'state': 'open', 'opened_date': fields.Datetime.now()})
        _tell_approvers(request, following)
        return
    order = request.order_id
    request.write({'state': 'approved', 'decided_date': fields.Datetime.now()})
    order.write({'spm_approval_state': 'approved'})
    _notify(order, (order.user_id | request.submitted_by_id).partner_id, order.env._(
        "%(order)s is approved and can be confirmed.", order=order.display_name))


# Material change (BR-SPM-0010, FR-SPM-0052) -----------------------------------

def is_watched(order):
    """Return whether changes of the order are compared with its approval."""
    return order.state in WATCHED_ORDER_STATES and order.spm_approval_state in WATCHED_APPROVAL_STATES


def _lines_key(lines):
    """Return {line id: (product, analytic distribution)} of the goods lines an approval applies to."""
    return {line['id']: (line['product_id'], line['analytic_distribution'] or {}) for line in lines}


def _differs_from_request(order, request):
    """Return whether the vendor, currency, payment terms or the lines of the order differ from the request's."""
    header = (order.partner_id, order.currency_id, order.payment_term_id)
    approved = (request.snapshot_partner_id, request.snapshot_currency_id, request.snapshot_payment_term_id)
    return header != approved or _lines_key(_snapshot_lines(order)) != _lines_key(request.snapshot_lines or [])


def _total_beyond_tolerance(order):
    """Return whether the total rose above the approved one by more than the policy's tolerance."""
    request = latest_request(order)
    if not request:
        return False
    company = order.company_id
    day = _order_day(order)
    approved = request.snapshot_currency_id._convert(
        request.snapshot_amount, company.currency_id, company, day)
    tolerance = min(request.policy_ids.sudo().mapped('reapproval_tolerance') or [0.0])  # sudo: the tolerance is policy configuration a Buyer may not read
    increase = _company_amount(order) - approved
    return company.currency_id.compare_amounts(increase, tolerance) > 0


def review_changes(orders):
    """Cancel and re-evaluate the approval of every watched order that no longer matches its request.

    Requirements: BR-SPM-0010, FR-SPM-0052
    The order is compared with the snapshot of its newest request (vendor, currency, payment terms,
    line products, analytic distributions, added and removed lines) and its total with the
    policy's tolerance, so no state is kept between the writes of a transaction.
    """
    for order in orders.filtered(is_watched):
        request = latest_request(order)
        if request and (_differs_from_request(order, request) or _total_beyond_tolerance(order)):
            reapprove(order)


def reapprove(order):
    """Cancel the current request because of a material change and evaluate the policies again.

    Requirements: FR-SPM-0052
    """
    request = latest_request(order)
    involved = request.step_ids.filtered(lambda s: s.state != 'waiting').approver_ids
    involved |= request.step_ids.decision_ids.approver_id | request.step_ids.decision_ids.on_behalf_of_id
    request.step_ids.filtered(lambda s: s.state in ('open', 'waiting')).write({'state': 'cancelled'})
    request.write({'state': 'cancelled', 'cancel_reason': 'material_change',
                   'decided_date': fields.Datetime.now()})
    new = _start(order, order.spm_revision + 1)
    _notify(order, involved.partner_id, order.env._(
        "The approval of %(order)s was cancelled by a material change; revision %(revision)s %(outcome)s.",
        order=order.display_name, revision=order.spm_revision,
        outcome=order.env._("needs a new approval") if new else order.env._("needs no approval")))
    return new


# Delegation, escalation and inbox (FR-SPM-0050, FR-SPM-0051, FR-SPM-0054) ----------

def active_delegators(env, user):
    """Return the users who delegated their approvals to ``user`` today (company time zone of the session).

    Requirements: FR-SPM-0050
    The first and the last day of a delegation count.
    """
    today = fields.Date.context_today(env['procurement.approval.delegation'])
    # sudo: the delegations of the delegate are read to check the entitlement; the caller shows none of them
    return env['procurement.approval.delegation'].sudo().search([
        ('delegate_id', '=', user.id), ('date_from', '<=', today), ('date_to', '>=', today),
    ]).delegator_id


def acting_for(step, user):
    """Return the approver of the step ``user`` decides as: himself, else a delegator; none when not entitled.

    Requirements: FR-SPM-0050
    A user who is an approver and also a delegate of another approver of an "all" step decides for
    the first of them who has not decided yet.
    """
    approvers = step.sudo().approver_ids  # sudo: read to check the entitlement, not shown
    candidates = (user | active_delegators(step.env, user)) & approvers
    undecided = candidates - _decided_approvers(step.sudo())
    return (undecided or candidates)[:1]


def _escalation_hours(step):
    """Return the delay in hours after which the open step is escalated (the policy's, else the field default)."""
    policy = step.policy_id
    if policy:
        return policy.escalation_hours
    return step.env['procurement.approval.policy'].default_get(['escalation_hours'])['escalation_hours']


def overdue_steps(env):
    """Return the open, not yet escalated steps of pending requests whose escalation delay has passed.

    Requirements: FR-SPM-0051, BR-SPM-0019
    """
    now = fields.Datetime.now()
    steps = env['procurement.approval.step'].search([
        ('state', '=', 'open'), ('escalated', '=', False), ('request_id.state', '=', 'pending'),
        ('opened_date', '!=', False)], order='opened_date, id')
    return steps.filtered(lambda s: s.opened_date + timedelta(hours=_escalation_hours(s)) <= now)


def escalation_users(step):
    """Return who is told of an overdue step: the policy's escalation user, else the approvers' managers."""
    if step.policy_id.escalation_user_id:
        return step.policy_id.escalation_user_id
    employees = step.env['hr.employee'].search([
        ('user_id', 'in', step.approver_ids.ids), ('company_id', 'in', (False, step.company_id.id))])
    return employees.parent_id.user_id


def escalate(step):
    """Escalate one overdue step once: an activity for the escalation user, a note to the approvers, the mark.

    Requirements: FR-SPM-0051, BR-SPM-0019
    The step keeps its approvers: escalation does not move the decision. Returns the notes sent.
    """
    step.lock_for_update()
    step.invalidate_recordset(['escalated', 'state'])
    if step.escalated or step.state != 'open':
        return 0
    order = step.request_id.order_id
    step.write({'escalated': True, 'escalated_date': fields.Datetime.now()})
    for user in escalation_users(step):
        order.activity_schedule(
            'mail.mail_activity_data_todo', user_id=user.id,
            summary=order.env._("Overdue approval: %(order)s", order=order.display_name),
            note=order.env._("The step %(step)s waits for %(approvers)s.",
                             step=step.name, approvers=", ".join(step.approver_ids.mapped('name'))))
    _notify(order, step.approver_ids.partner_id, order.env._(
        "Your approval of %(order)s is overdue (step %(step)s).", order=order.display_name, step=step.name))
    return 1


def inbox(env):
    """Return the open steps the session user can decide, oldest first, as dicts.

    Requirements: FR-SPM-0054
    Keys: step_id, order_id, on_behalf_of_id (the delegator's id or False). Steps of the user's
    companies and of pending requests only; a step he already decided leaves the list.
    """
    user = env.user
    steps = env['procurement.approval.step'].search([
        ('state', '=', 'open'), ('request_id.state', '=', 'pending'),
        ('approver_ids', 'in', (user | active_delegators(env, user)).ids)], order='opened_date, id')
    rows = []
    for step in steps:
        acting = acting_for(step, user)
        if acting and acting not in _decided_approvers(step.sudo()):
            rows.append({
                'step_id': step.id, 'order_id': step.request_id.order_id.id,
                'on_behalf_of_id': acting.id if acting != user else False,
            })
    return rows
