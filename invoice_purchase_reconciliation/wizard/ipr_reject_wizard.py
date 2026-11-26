from odoo import fields, models


class IprRejectWizard(models.TransientModel):
    _name = 'ipr.reject.wizard'
    _description = 'Reconciliation decision dialog'

    move_id = fields.Many2one('account.move', string='Bill', required=True, ondelete='cascade')
    decision = fields.Selection(
        [('approve', 'Approve'), ('reject', 'Reject')], required=True)
    reason = fields.Text(string='Reason')

    def action_confirm(self):
        """Record the decision on the bill; the bill's own checks apply.

        Requirements: FR-IPR-0012, FR-IPR-0013
        """
        self.ensure_one()
        if self.decision == 'reject':
            self.move_id.action_ipr_reject(self.reason)
        else:
            self.move_id.action_ipr_approve(self.reason)
        return {'type': 'ir.actions.act_window_close'}
