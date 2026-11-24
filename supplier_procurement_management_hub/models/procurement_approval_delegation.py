from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

from ..services import approval, naming


class ProcurementApprovalDelegation(models.Model):
    """The approval rights of one user given to another for a period.

    Requirements: FR-SPM-0050, BR-SPM-0018
    Within its dates the delegate may decide the steps of the delegator; the decision names both.
    """
    _name = 'procurement.approval.delegation'
    _inherit = ['procurement.audit.mixin']
    _description = 'Approval Delegation'
    _order = 'date_from desc, id desc'

    delegator_id = fields.Many2one(
        'res.users', string="Delegator", required=True, ondelete='cascade', index=True,
        default=lambda self: self.env.user)
    delegate_id = fields.Many2one('res.users', string="Delegate", required=True, ondelete='cascade', index=True)
    date_from = fields.Date(string="From", required=True)
    date_to = fields.Date(string="To", required=True)
    note = fields.Text()
    active = fields.Boolean(default=True)

    _spm_audited_fields = ('delegator_id', 'delegate_id', 'date_from', 'date_to', 'active')

    @api.depends('delegator_id', 'delegate_id')
    def _compute_display_name(self):
        """Requirements: FR-SPM-0050

        Who gave the approval rights and to whom.
        """
        for delegation in self:
            delegation.display_name = naming.join_name(
                [delegation.delegator_id.name, delegation.delegate_id.name], delegation._description)

    def write(self, vals):
        """Refuse to re-point a delegation to another delegator; only a hub admin does.

        Requirements: FR-SPM-0050, BR-SPM-0018
        The access rule is checked before the write only, so the delegator is fixed at creation.
        """
        if 'delegator_id' in vals and not self.env.su \
                and not self.env.user.has_group('supplier_procurement_management_hub.group_spm_admin'):
            if any(delegation.delegator_id.id != vals['delegator_id'] for delegation in self):
                raise AccessError(self.env._("The delegator of a delegation cannot be changed."))
        return super().write(vals)

    @api.constrains('delegator_id', 'delegate_id')
    def _check_not_oneself(self):
        """Requirements: FR-SPM-0050 (a delegation to oneself is refused)."""
        if any(delegation.delegator_id == delegation.delegate_id for delegation in self):
            raise ValidationError(self.env._("A delegation to oneself is not allowed."))

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        """Requirements: FR-SPM-0050 (the end date is not before the start date)."""
        if any(delegation.date_to < delegation.date_from for delegation in self):
            raise ValidationError(self.env._("The end date of a delegation cannot be before its start date."))


class ProcurementJob(models.Model):  # sdd-allow: model-split the job runner is extended per code by the task that owns the job (procurement_job.py, not a file of this task)
    """The hourly escalation of overdue approval steps runs as a job of the app (ADR-SPM-007).

    Requirements: FR-SPM-0051, FR-SPM-0096
    """
    _inherit = 'procurement.job'

    code = fields.Selection(selection_add=[('approval_escalation', 'Approval escalation')],
        ondelete={'approval_escalation': 'cascade'})

    def _spm_collect_approval_escalation(self):
        """Return the open steps whose escalation delay has passed and that are not escalated yet.

        Requirements: FR-SPM-0051, BR-SPM-0019
        """
        return approval.overdue_steps(self.env)

    def _spm_process_approval_escalation(self, step):
        """Escalate one step and return the notifications sent.

        Requirements: FR-SPM-0051, BR-SPM-0019
        """
        return approval.escalate(step)
