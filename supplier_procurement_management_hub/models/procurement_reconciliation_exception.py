from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from .procurement_exception_route import EXCEPTION_KINDS

EXCEPTION_STATES = [
    ('open', 'Open'), ('in_review', 'In review'), ('resolved', 'Resolved'),
    ('accepted', 'Accepted'), ('rejected', 'Rejected'),
]
UNDECIDED_STATES = ('open', 'in_review')
RELEASING_STATES = ('accepted', 'resolved')
RESOLVABLE_STATES = UNDECIDED_STATES + ('rejected',)
DECIDER_GROUPS = (
    'supplier_procurement_management_hub.group_spm_finance',
    'supplier_procurement_management_hub.group_spm_manager',
)


class ProcurementReconciliationException(models.Model):
    """A discrepancy of a bill with evidence, an owner, a status and a decision.

    Requirements: FR-SPM-0076, FR-SPM-0077, FR-SPM-0078
    Users read exceptions; every change goes through the actions below, which
    check the right of the caller and then write with elevated rights. Nothing
    here writes the bill (BR-SPM-0016).
    """
    _name = 'procurement.reconciliation.exception'
    _inherit = ['mail.thread']
    _description = 'Bill Matching Exception'
    _order = 'id desc'

    reconciliation_id = fields.Many2one('procurement.reconciliation', required=True, ondelete='cascade', index=True)
    move_id = fields.Many2one(related='reconciliation_id.move_id', store=True, index=True)
    company_id = fields.Many2one(related='reconciliation_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='move_id.currency_id')
    kind = fields.Selection(EXCEPTION_KINDS, required=True)
    recon_line_id = fields.Many2one('procurement.reconciliation.line', string="Matching line", ondelete='set null')
    move_line_id = fields.Many2one('account.move.line', string="Bill line", ondelete='set null', index=True)
    purchase_line_id = fields.Many2one('purchase.order.line', string="Order line")
    stock_move_ids = fields.Many2many('stock.move', 'spm_exception_stock_move_rel', 'exception_id', 'move_id',
                                      string="Receipt lines")
    expected_value = fields.Char()
    found_value = fields.Char()
    amount_at_stake = fields.Monetary(currency_field='currency_id')
    owner_id = fields.Many2one('res.users', string="Owner")
    state = fields.Selection(EXCEPTION_STATES, required=True, default='open', tracking=True, index=True)
    requires_approval = fields.Boolean(string="Needs approval before posting")
    decided_by_id = fields.Many2one('res.users', string="Decided by")
    decision_comment = fields.Text()

    # decisions -----------------------------------------------------------
    def _spm_check_decider(self):
        """Refuse a caller that is neither a Finance reviewer nor a Procurement manager."""
        if not any(self.env.user.has_group(group) for group in DECIDER_GROUPS):
            raise AccessError(self.env._("Only a Finance reviewer or a Procurement manager may decide an exception."))

    def _spm_decide(self, state, comment=None, decidable=UNDECIDED_STATES, comment_required=False):
        """Write a decision of the caller on exceptions still in a decidable state.

        The rights of the caller are checked here, once; the write is the one
        narrow elevated step, because users hold read access only.
        """
        self._spm_check_decider()
        if any(exception.state not in decidable for exception in self):
            raise UserError(self.env._("This exception is already decided."))
        if comment_required and not (comment or '').strip():
            raise UserError(self.env._("A comment is required to decide an exception."))
        # sudo: users hold read access only; the caller was checked above
        self.sudo().write({
            'state': state, 'decided_by_id': self.env.user.id,
            'decision_comment': (comment or '').strip() or False,
        })

    def action_spm_accept(self, comment):
        """Accept the exception, with a mandatory comment.

        Requirements: FR-SPM-0077
        """
        self._spm_decide('accepted', comment, comment_required=True)
        return True

    def action_spm_reject(self, comment):
        """Reject the exception, with a mandatory comment.

        Requirements: FR-SPM-0077
        """
        self._spm_decide('rejected', comment, comment_required=True)
        return True

    def _spm_open_comment_wizard(self, action, name):
        """Open the comment dialog of a decision on these exceptions."""
        return {
            'type': 'ir.actions.act_window', 'name': name, 'res_model': 'procurement.reason.wizard',
            'view_mode': 'form', 'target': 'new',
            'context': {'default_res_model': self._name, 'default_res_ids': self.ids, 'default_action': action},
        }

    def action_spm_open_accept_wizard(self):
        """Open the dialog asking the comment to accept."""
        return self._spm_open_comment_wizard('accept_exception', self.env._("Accept exception"))

    def action_spm_open_reject_wizard(self):
        """Open the dialog asking the comment to reject."""
        return self._spm_open_comment_wizard('reject_exception', self.env._("Reject exception"))

    def action_spm_resolve(self):
        """Mark the exception resolved, for example after a credit note.

        Requirements: FR-SPM-0077, FR-SPM-0078
        Only the exception is written, never the bill. A rejected exception
        may be resolved too, once the bill is corrected or credited.
        """
        self._spm_decide('resolved', decidable=RESOLVABLE_STATES)
        return True

    def action_spm_review(self):
        """Start the review of the exception.

        Requirements: FR-SPM-0076
        """
        self._spm_check_decider()
        if any(exception.state != 'open' for exception in self):
            raise UserError(self.env._("Only an open exception can be put in review."))
        # sudo: users hold read access only; the caller was checked above
        self.sudo().write({'state': 'in_review'})
        return True

    # sync with the matching ----------------------------------------------
    @api.model
    def _spm_owner(self, route, order):
        """Return the user who owns an exception of the route."""
        if route.owner_id:
            return route.owner_id
        if route.owner_role == 'buyer':
            return order.user_id
        return self.env['res.users']

    @api.model
    def _spm_sync(self, reconciliation, wanted) -> None:
        """Create, update or close the exceptions of a reconciliation.

        Requirements: FR-SPM-0076, FR-SPM-0077
        ``wanted`` maps ``(kind, bill line id)`` (0 for the duplicate kind) to
        the values of the discrepancy found now. Exceptions no longer found
        are resolved unless a person released them; an accepted or person-
        resolved exception whose evidence changed is reopened; other decisions
        stay. Runs with elevated rights from the matching service.
        """
        routes = {route.kind: route for route in self.env['procurement.exception.route'].search(
            [('company_id', '=', reconciliation.company_id.id)])}
        existing = {(exc.kind, exc.move_line_id.id): exc for exc in reconciliation.exception_ids
                    if exc.kind == 'duplicate' or exc.move_line_id}
        for key, values in wanted.items():
            route = routes.get(key[0], self.env['procurement.exception.route'])
            owner = self._spm_owner(route, values.pop('order', self.env['purchase.order']))
            values = dict(values, owner_id=owner.id, requires_approval=route.requires_approval)
            current = existing.get(key)
            if not current:
                self._spm_create(reconciliation, key[0], values)
                continue
            self._spm_refresh_one(current, values)
        for exception in reconciliation.exception_ids:
            if exception.state in RESOLVABLE_STATES and (exception.kind, exception.move_line_id.id) not in wanted:
                exception.write({'state': 'resolved', 'decision_comment': self.env._(
                    "The discrepancy is no longer present.")})

    def _spm_refresh_one(self, current, values) -> None:
        """Bring one exception up to the discrepancy found now."""
        currency = current.currency_id
        previous_owner = current.owner_id
        changed = (
            current.expected_value != values['expected_value'] or current.found_value != values['found_value']
            or currency.compare_amounts(current.amount_at_stake, values['amount_at_stake']) != 0)
        released_by_person = current.state == 'accepted' or (current.state == 'resolved' and current.decided_by_id)
        if current.state in UNDECIDED_STATES or (current.state == 'resolved' and not current.decided_by_id):
            new_state = 'open' if current.state == 'resolved' else current.state
            current.write(dict(values, state=new_state))
        elif released_by_person and changed:
            current.write(dict(values, state='open', decided_by_id=False, decision_comment=False))
        else:
            current.write({'requires_approval': values['requires_approval']})
        if current.owner_id and current.owner_id != previous_owner:
            current._spm_notify_owner()

    @api.model
    def _spm_create(self, reconciliation, kind, values) -> None:
        """Create one exception and notify its owner."""
        exception = self.with_context(mail_create_nosubscribe=True).create(dict(
            values, reconciliation_id=reconciliation.id, kind=kind))
        exception._spm_notify_owner()

    def _spm_notify_owner(self) -> None:
        """Subscribe the owner of the exception and tell them about it, once."""
        self.ensure_one()
        owner = self.owner_id
        if not owner:
            return
        self.message_subscribe(partner_ids=owner.partner_id.ids)
        self.message_post(
            body=self.env._("%(kind)s exception on bill %(bill)s: expected %(expected)s, found %(found)s.",
                            kind=dict(EXCEPTION_KINDS)[self.kind], bill=self.move_id.display_name,
                            expected=self.expected_value, found=self.found_value),
            partner_ids=owner.partner_id.ids, subtype_xmlid='mail.mt_note')
