from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..services import approval


class ProcurementApprovalPolicyStep(models.Model):
    """A step of a policy: who approves and whether every approver or any one must.

    Requirements: FR-SPM-0044, FR-SPM-0045
    """
    _name = 'procurement.approval.policy.step'
    _description = 'Approval Policy Step'
    _order = 'policy_id, sequence, id'

    policy_id = fields.Many2one('procurement.approval.policy', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='policy_id.company_id', store=True, index=True)
    sequence = fields.Integer(required=True, default=10)
    name = fields.Char(required=True)
    approver_type = fields.Selection([
        ('users', 'Fixed users'), ('manager', "Requester's managers"), ('group', 'Members of a group'),
    ], required=True, default='users')
    user_ids = fields.Many2many('res.users', 'procurement_approval_policy_step_user_rel', 'step_id', 'user_id',
                                string="Approvers")
    manager_levels = fields.Integer(string="Levels", default=1)
    group_id = fields.Many2one('res.groups', string="Group", ondelete='restrict')
    mode = fields.Selection([('all', 'Every approver'), ('any', 'Any approver')], required=True, default='any')

    @api.constrains('approver_type', 'manager_levels')
    def _check_manager_levels(self):
        """Refuse a manager step of less than one level."""
        for step in self:
            if step.approver_type == 'manager' and step.manager_levels < 1:
                raise ValidationError(self.env._("The step %s needs at least one manager level.", step.name))

    @api.model
    def _spm_approver_resolvers(self):
        """Return the resolver method name of each approver type; extend it to add a type.

        Requirements: FR-SPM-0044
        """
        return {
            'users': '_spm_resolve_users',
            'manager': '_spm_resolve_manager',
            'group': '_spm_resolve_group',
        }

    def spm_resolve_approvers(self, order):
        """Return the active approvers of this step for an order, in approval order.

        Requirements: FR-SPM-0044
        """
        self.ensure_one()
        method = self._spm_approver_resolvers().get(self.approver_type)
        users = getattr(self, method)(order) if method else self.env['res.users']
        return [user.id for user in users if user.active and order.company_id in user.company_ids]

    def _spm_resolve_users(self, order):
        return self.user_ids.sorted('id')

    def _spm_resolve_group(self, order):
        return self.group_id.all_user_ids.sorted('id')

    def _spm_resolve_manager(self, order):
        """Walk the employee hierarchy up from the requester, up to the step's levels."""
        employee = approval.requester_employee(order)
        managers = self.env['res.users']
        seen = employee
        for _level in range(self.manager_levels):
            employee = employee.parent_id
            if not employee or employee in seen:
                break
            seen |= employee
            managers |= employee.user_id
        return managers
