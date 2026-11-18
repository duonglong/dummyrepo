from odoo import api, fields, models
from odoo.exceptions import UserError

from .procurement_exception_route import EXCEPTION_KINDS

EXCEPTION_STATES = [
    ('open', 'Open'), ('in_review', 'In review'), ('resolved', 'Resolved'),
    ('accepted', 'Accepted'), ('rejected', 'Rejected'),
]
UNDECIDED_STATES = ('open', 'in_review')
RELEASING_STATES = ('accepted', 'resolved')
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
            raise UserError(self.env._("Only a Finance reviewer or a Procurement manager may decide an exception."))

    def _spm_decide(self, state, comment=None):
        """Write a decision of the caller on undecided exceptions.

        The rights of the caller are checked before; the write is the one
        narrow elevated step, because users hold read access only.
        """
        self._spm_check_decider()
        if any(exception.state not in UNDECIDED_STATES for exception in self):
            raise UserError(self.env._("Only an open exception or one in review can be decided."))
        # sudo: users hold read access only; the caller was checked above
        self.sudo().write({
            'state': state, 'decided_by_id': self.env.user.id,
            'decision_comment': (comment or '').strip() or False,
        })

    def action_spm_accept(self, comment):
        """Accept the exception, with a mandatory comment.

        Requirements: FR-SPM-0077
        """
        self._spm_check_decider()
        if not (comment or '').strip():
            raise UserError(self.env._("A comment is required to accept an exception."))
        self._spm_decide('accepted', comment)
        return True

    def action_spm_reject(self, comment):
        """Reject the exception, with a mandatory comment.

        Requirements: FR-SPM-0077
        """
        self._spm_check_decider()
        if not (comment or '').strip():
            raise UserError(self.env._("A comment is required to reject an exception."))
        self._spm_decide('rejected', comment)
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
        Only the exception is written, never the bill.
        """
        self._spm_decide('resolved')
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

        Requirements: FR-SPM-0076
        ``wanted`` maps ``(kind, matching line id)`` to the values of the
        discrepancy found now. Undecided exceptions that are no longer found
        are resolved; an accepted exception whose evidence changed is
        reopened; other decisions stay. Runs with elevated rights from the
        matching service.
        """
        routes = {route.kind: route for route in self.env['procurement.exception.route'].search(
            [('company_id', '=', reconciliation.company_id.id)])}
        existing = {(exc.kind, exc.recon_line_id.id): exc for exc in reconciliation.exception_ids}
        for key, values in wanted.items():
            route = routes.get(key[0], self.env['procurement.exception.route'])
            owner = self._spm_owner(route, values.pop('order', self.env['purchase.order']))
            values = dict(values, owner_id=owner.id, requires_approval=route.requires_approval)
            current = existing.get(key)
            if not current:
                self._spm_create(reconciliation, key, values)
                continue
            evidence = ('expected_value', 'found_value', 'amount_at_stake')
            changed = any(current[name] != values[name] for name in evidence)
            if current.state in UNDECIDED_STATES or (current.state == 'resolved' and not current.decided_by_id):
                current.write(dict(values, state='open' if current.state == 'resolved' else current.state))
            elif current.state == 'accepted' and changed:
                current.write(dict(values, state='open', decided_by_id=False, decision_comment=False))
            else:
                current.write({'requires_approval': values['requires_approval']})
        for key, current in existing.items():
            if key not in wanted and current.state in UNDECIDED_STATES:
                current.write({'state': 'resolved', 'decision_comment': self.env._(
                    "The discrepancy is no longer present.")})

    @api.model
    def _spm_create(self, reconciliation, key, values) -> None:
        """Create one exception and notify its owner."""
        exception = self.with_context(mail_create_nosubscribe=True).create(dict(
            values, reconciliation_id=reconciliation.id, kind=key[0], recon_line_id=key[1] or False))
        owner = exception.owner_id
        if owner:
            exception.message_subscribe(partner_ids=owner.partner_id.ids)
            exception.message_post(
                body=self.env._("%(kind)s exception on bill %(bill)s: expected %(expected)s, found %(found)s.",
                                kind=dict(EXCEPTION_KINDS)[key[0]], bill=exception.move_id.display_name,
                                expected=exception.expected_value, found=exception.found_value),
                partner_ids=owner.partner_id.ids, subtype_xmlid='mail.mt_note')
