from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from ..services import audit
from .procurement_matching_policy import MATCHING_METHODS
from .procurement_reconciliation_exception import RELEASING_STATES

PROTECTED_FIELDS = {
    'state', 'method', 'duplicate_move_ids', 'duplicate_cleared', 'duplicate_cleared_by_id', 'duplicate_comment',
    'duplicate_reviewed_move_ids',
}


class ProcurementReconciliation(models.Model):
    """The matching of one vendor bill with its order and receipts.

    Requirements: FR-SPM-0071, FR-SPM-0072, FR-SPM-0075
    Results live here, never on the bill (ADR-SPM-006).
    """
    _name = 'procurement.reconciliation'
    _description = 'Bill Reconciliation'
    _order = 'refreshed_date desc, id desc'

    move_id = fields.Many2one('account.move', string="Vendor bill", required=True, ondelete='cascade', index=True)
    partner_id = fields.Many2one(related='move_id.partner_id', store=True)
    company_id = fields.Many2one(related='move_id.company_id', store=True, index=True)
    method = fields.Selection(MATCHING_METHODS, readonly=True)
    state = fields.Selection([
        ('matched', 'Matched'), ('review', 'To review'), ('error', 'Error'),
    ], required=True, default='matched', readonly=True)
    line_ids = fields.One2many('procurement.reconciliation.line', 'reconciliation_id', string="Lines")
    exception_ids = fields.One2many('procurement.reconciliation.exception', 'reconciliation_id', string="Exceptions")
    duplicate_move_ids = fields.Many2many(
        'account.move', 'spm_recon_duplicate_rel', 'reconciliation_id', 'move_id', string="Possible duplicates",
        readonly=True)
    duplicate_reviewed_move_ids = fields.Many2many(
        'account.move', 'spm_recon_duplicate_reviewed_rel', 'reconciliation_id', 'move_id',
        string="Bills cleared as not duplicates", readonly=True)
    duplicate_cleared = fields.Boolean(string="Not a duplicate", readonly=True)
    duplicate_cleared_by_id = fields.Many2one('res.users', string="Cleared by", readonly=True)
    duplicate_comment = fields.Text(string="Clearing comment", readonly=True)
    refreshed_date = fields.Datetime(readonly=True)

    _move_unique = models.Constraint('UNIQUE(move_id)', 'A vendor bill has one reconciliation.')

    @api.depends('move_id')
    def _compute_display_name(self):
        """Requirements: FR-SPM-0071

        The vendor bill that is matched.
        """
        for reconciliation in self:
            reconciliation.display_name = reconciliation.move_id.display_name

    @api.model_create_multi
    def create(self, vals_list):
        """Keep the duplicate decision and the match results to the hub's own code.

        Requirements: FR-SPM-0075
        A user's create with these fields would clear a flag without a comment.
        """
        if not self.env.su and any(PROTECTED_FIELDS & set(vals) for vals in vals_list):
            raise AccessError(self.env._("The matching result and the duplicate decision cannot be edited."))
        return super().create(vals_list)

    def write(self, vals):
        """Keep the duplicate decision and the match results to the hub's own code.

        Requirements: FR-SPM-0075
        A user's write of these fields would clear a flag without a comment.
        """
        if not self.env.su and PROTECTED_FIELDS & set(vals):
            raise AccessError(self.env._("The matching result and the duplicate decision cannot be edited."))
        return super().write(vals)

    def _spm_holding_exceptions(self):
        """Return the exceptions of these matchings that are neither accepted nor resolved."""
        return self.exception_ids.filtered(lambda exception: exception.state not in RELEASING_STATES)

    def action_spm_run_matching(self):
        """Match the bill again.

        Requirements: FR-SPM-0072
        """
        self.move_id.action_spm_run_matching()
        return True

    def action_spm_open_not_duplicate_wizard(self):
        """Open the dialog asking the comment to clear the duplicate flag."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': self.env._("Not a duplicate"),
            'res_model': 'procurement.reason.wizard', 'view_mode': 'form', 'target': 'new',
            'context': {'default_res_model': self._name, 'default_res_ids': self.ids,
                        'default_action': 'not_duplicate'},
        }

    def action_spm_mark_not_duplicate(self, comment):
        """Clear the possible-duplicate flag, with a mandatory comment.

        Requirements: FR-SPM-0075
        Only a Finance reviewer of the bill's company may (the Administrator implies the role). The duplicate
        exception is resolved with the same comment; the bill itself is not written.
        """
        user = self.env.user
        if not user.has_group('supplier_procurement_management_hub.group_spm_finance'):
            raise AccessError(self.env._("Only a Finance reviewer may mark a bill as not a duplicate."))
        # the company rule of the caller applies before the elevated write below
        self.check_access('write')
        comment = (comment or '').strip()
        if not comment:
            raise UserError(self.env._("A comment is required to mark a bill as not a duplicate."))
        # sudo: users hold no write access on matching records; the caller was checked above
        recon = self.sudo()
        previous = {'duplicate_cleared': recon.duplicate_cleared, 'duplicate_comment': recon.duplicate_comment}
        recon.write({
            'duplicate_cleared': True, 'duplicate_cleared_by_id': user.id, 'duplicate_comment': comment,
            'duplicate_reviewed_move_ids': [(4, move.id) for move in recon.duplicate_move_ids],
            'duplicate_move_ids': [(5, 0, 0)],
        })
        # the clearing is audited on the matching itself, whatever state its duplicate exception is in
        audit.log(self.env, 'exception', recon, old_values=previous,
                  new_values={'duplicate_cleared': True, 'duplicate_comment': comment}, comment=comment)
        duplicate_exceptions = recon._spm_holding_exceptions().filtered(lambda exc: exc.kind == 'duplicate')
        duplicate_exceptions._spm_take_ownership()
        duplicate_exceptions._spm_write_decision({
            'state': 'resolved', 'decided_by_id': user.id, 'decision_comment': comment})
        return True
