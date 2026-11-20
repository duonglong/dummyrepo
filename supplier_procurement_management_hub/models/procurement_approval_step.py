from odoo import fields, models

from ..services import approval


class ProcurementApprovalStep(models.Model):
    """A step of an approval request: its resolved approvers, state and decisions.

    Requirements: FR-SPM-0047, FR-SPM-0049
    """
    _name = 'procurement.approval.step'
    _description = 'Approval Step'
    _order = 'request_id, sequence, id'

    request_id = fields.Many2one('procurement.approval.request', required=True, ondelete='cascade', index=True,
                                 readonly=True)
    policy_id = fields.Many2one('procurement.approval.policy', ondelete='set null', readonly=True)
    sequence = fields.Integer(required=True, readonly=True)
    name = fields.Char(required=True, readonly=True)
    mode = fields.Selection([('all', 'Every approver'), ('any', 'Any approver')], required=True, readonly=True)
    approver_ids = fields.Many2many('res.users', 'procurement_approval_step_user_rel', 'step_id', 'user_id',
                                    string="Approvers", required=True, readonly=True)
    state = fields.Selection([
        ('waiting', 'Waiting'), ('open', 'Open'), ('approved', 'Approved'), ('rejected', 'Rejected'),
        ('cancelled', 'Cancelled'),
    ], required=True, default='waiting', index=True, readonly=True)
    opened_date = fields.Datetime(readonly=True)
    decision_ids = fields.One2many('procurement.approval.decision', 'step_id', string="Decisions", readonly=True)
    company_id = fields.Many2one(related='request_id.company_id', store=True, index=True)

    def _spm_acting_for(self, user):
        """Return the approver of this step the user decides as (the user himself), none when not entitled.

        Requirements: FR-SPM-0049
        Extended by the delegation to answer the delegator of a delegate.
        """
        self.ensure_one()
        return user if user in self.sudo().approver_ids else self.env['res.users']  # sudo: the approvers of a step are read to check the entitlement, not shown

    def action_spm_approve(self, comment=None):
        """Approve the step as the session user, with an optional comment.

        Requirements: FR-SPM-0049, FR-SPM-0053
        """
        self.ensure_one()
        approval.decide(self, 'approved', comment)
        return True

    def action_spm_reject(self, reason=None):
        """Reject the step as the session user; the reason is required.

        Requirements: FR-SPM-0049, FR-SPM-0053
        """
        self.ensure_one()
        approval.decide(self, 'rejected', reason)
        return True

    def action_spm_open_reject_wizard(self):
        """Open the dialog asking the reason of a rejection.

        Requirements: FR-SPM-0049
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Reject"),
            'res_model': 'procurement.approval.reject.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_step_id': self.id},
        }


class ProcurementApprovalRejectWizard(models.TransientModel):
    """The reason dialog of a rejection.

    Requirements: FR-SPM-0049
    """
    _name = 'procurement.approval.reject.wizard'
    _description = 'Approval Rejection Dialog'

    step_id = fields.Many2one('procurement.approval.step', required=True, ondelete='cascade')
    reason = fields.Text(required=True)

    def action_confirm(self):
        """Reject the step with the reason; the checks are those of the step's action.

        Requirements: FR-SPM-0049
        """
        self.ensure_one()
        self.step_id.action_spm_reject(self.reason)
        return {'type': 'ir.actions.act_window_close'}
