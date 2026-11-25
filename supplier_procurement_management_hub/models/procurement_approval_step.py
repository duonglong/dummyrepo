from odoo import api, fields, models

from ..services import approval


class ProcurementApprovalStep(models.Model):
    """A step of an approval request: its resolved approvers, state and decisions.

    Requirements: FR-SPM-0047, FR-SPM-0049, FR-SPM-0051, FR-SPM-0054
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
    escalated = fields.Boolean(readonly=True, copy=False)
    escalated_date = fields.Datetime(readonly=True, copy=False)
    decision_ids = fields.One2many('procurement.approval.decision', 'step_id', string="Decisions", readonly=True)
    company_id = fields.Many2one(related='request_id.company_id', store=True, index=True)
    # the inbox columns: what the approver needs to decide without opening the order
    order_id = fields.Many2one(related='request_id.order_id', string="Order")
    partner_id = fields.Many2one(related='request_id.snapshot_partner_id', string="Vendor")
    currency_id = fields.Many2one(related='request_id.snapshot_currency_id')
    amount = fields.Monetary(related='request_id.snapshot_amount', currency_field='currency_id')
    cumulated_amount = fields.Monetary(related='request_id.cumulated_amount', currency_field='currency_id')
    requester_id = fields.Many2one('res.users', string="Requester", compute='_compute_inbox_columns')
    waiting_hours = fields.Float(string="Waiting", compute='_compute_inbox_columns')
    on_behalf_of_id = fields.Many2one('res.users', string="On behalf of", compute='_compute_inbox_columns')

    @api.depends('opened_date', 'state')
    @api.depends_context('uid')
    def _compute_inbox_columns(self):
        """Requirements: FR-SPM-0054 (the waiting time, the requester and the delegator the session user stands for)."""
        now = fields.Datetime.now()
        for step in self:
            step.requester_id = approval.order_requester(step.request_id.order_id.sudo())  # sudo: the requester is shown, not changed
            step.waiting_hours = (now - step.opened_date).total_seconds() / 3600 if step.opened_date else 0.0
            acting = approval.acting_for(step, step.env.user)
            step.on_behalf_of_id = acting if acting != step.env.user else False

    def _spm_acting_for(self, user):
        """Return the approver of this step the user decides as (himself, else a delegator), none when not entitled.

        Requirements: FR-SPM-0049, FR-SPM-0050
        """
        self.ensure_one()
        return approval.acting_for(self, user)

    @api.model
    def spm_inbox(self):
        """Return the open steps the session user can decide, oldest first.

        Requirements: FR-SPM-0054
        Each row holds step_id, order_id and on_behalf_of_id (False for his own steps).
        """
        return approval.inbox(self.env)

    @api.model
    def action_spm_open_inbox(self):
        """Open the approval inbox: the steps of ``spm_inbox`` in their order, oldest first.

        Requirements: FR-SPM-0054
        """
        ids = [row['step_id'] for row in self.spm_inbox()]
        return {
            'type': 'ir.actions.act_window', 'name': self.env._("Approval inbox"),
            'res_model': self._name, 'view_mode': 'list', 'domain': [('id', 'in', ids)],
            'views': [(self.env.ref('supplier_procurement_management_hub.procurement_approval_step_inbox_list').id, 'list')],
            'context': {'create': False},
        }

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

    def action_spm_open_approve_wizard(self):
        """Open the dialog taking the optional comment of an approval.

        Requirements: FR-SPM-0049
        """
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Approve"),
            'res_model': 'procurement.approval.approve.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_step_id': self.id},
        }

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


class ProcurementApprovalApproveWizard(models.TransientModel):
    """The comment dialog of an approval.

    Requirements: FR-SPM-0049
    """
    _name = 'procurement.approval.approve.wizard'
    _description = 'Approval Comment Dialog'

    step_id = fields.Many2one('procurement.approval.step', required=True, ondelete='cascade')
    comment = fields.Text()

    def action_confirm(self):
        """Approve the step with the comment; the checks are those of the step's action.

        Requirements: FR-SPM-0049
        """
        self.ensure_one()
        self.step_id.action_spm_approve(self.comment)
        return {'type': 'ir.actions.act_window_close'}
