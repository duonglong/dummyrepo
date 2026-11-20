from odoo import fields, models
from odoo.exceptions import UserError


class ProcurementApprovalDecision(models.Model):
    """One immutable decision on a step, with the figures of the revision it applied to.

    Requirements: FR-SPM-0053, BR-SPM-0018
    Nobody changes or deletes a decision, not even through elevated rights.
    """
    _name = 'procurement.approval.decision'
    _description = 'Approval Decision'
    _order = 'date, id'

    step_id = fields.Many2one('procurement.approval.step', required=True, ondelete='restrict', index=True, readonly=True)
    request_id = fields.Many2one(related='step_id.request_id', store=True, index=True)
    approver_id = fields.Many2one('res.users', required=True, ondelete='restrict', readonly=True)
    on_behalf_of_id = fields.Many2one('res.users', string="On behalf of", ondelete='restrict', readonly=True)
    decision = fields.Selection([('approved', 'Approved'), ('rejected', 'Rejected')], required=True, readonly=True)
    date = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)
    comment = fields.Text(readonly=True)
    revision = fields.Integer(readonly=True)
    amount = fields.Monetary(currency_field='currency_id', readonly=True)
    currency_id = fields.Many2one('res.currency', ondelete='restrict', readonly=True)
    partner_id = fields.Many2one('res.partner', string="Vendor", ondelete='restrict', readonly=True)
    company_id = fields.Many2one(related='step_id.company_id', store=True, index=True)

    def write(self, vals):
        """Requirements: FR-SPM-0053 (refused for everyone)."""
        raise UserError(self.env._("An approval decision cannot be changed."))

    def unlink(self):
        """Requirements: FR-SPM-0053 (refused for everyone)."""
        raise UserError(self.env._("An approval decision cannot be deleted."))
