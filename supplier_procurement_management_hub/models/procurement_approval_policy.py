from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..services import approval

CUMULATIVE_PERIODS = [('none', 'None'), ('month', 'Month'), ('quarter', 'Quarter'), ('year', 'Year')]


class ProcurementApprovalPolicy(models.Model):
    """Conditions, cumulative period and steps deciding who approves a purchase order.

    Requirements: BR-SPM-0009, FR-SPM-0043, FR-SPM-0046
    """
    _name = 'procurement.approval.policy'
    _inherit = ['procurement.audit.mixin']
    _description = 'Approval Policy'
    _order = 'sequence, id'
    _check_company_auto = True

    name = fields.Char(required=True)
    sequence = fields.Integer(required=True, default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company, ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='company_id.currency_id', string="Currency")
    amount_min = fields.Monetary(string="Amount from", help="Matches orders of this amount or more; 0 sets no lower limit.")
    amount_max = fields.Monetary(string="Amount below", help="Matches orders below this amount; 0 sets no upper limit.")
    department_ids = fields.Many2many('hr.department', string="Departments", check_company=True)
    product_category_ids = fields.Many2many('product.category', string="Product categories")
    product_ids = fields.Many2many('product.product', string="Products", check_company=True)
    partner_ids = fields.Many2many('res.partner', string="Vendors")
    analytic_account_ids = fields.Many2many('account.analytic.account', string="Analytic accounts", check_company=True)
    cumulative_period = fields.Selection(CUMULATIVE_PERIODS, required=True, default='none')
    reapproval_tolerance = fields.Monetary(default=0.0)
    escalation_hours = fields.Integer(default=48)
    escalation_user_id = fields.Many2one('res.users', string="Escalate to", ondelete='set null')
    step_ids = fields.One2many('procurement.approval.policy.step', 'policy_id', string="Steps", copy=True)

    _spm_audited_fields = (
        'name', 'sequence', 'active', 'company_id', 'amount_min', 'amount_max', 'department_ids',
        'product_category_ids', 'product_ids', 'partner_ids', 'analytic_account_ids',
        'cumulative_period', 'reapproval_tolerance', 'escalation_hours', 'escalation_user_id')

    @api.constrains('amount_min', 'amount_max')
    def _check_amounts(self):
        """Refuse a negative amount or an upper limit not above the lower one."""
        for policy in self:
            if policy.amount_min < 0 or policy.amount_max < 0:
                raise ValidationError(self.env._("Amounts of a policy cannot be negative."))
            if policy.amount_max and policy.amount_max <= policy.amount_min:
                raise ValidationError(self.env._("The upper amount of a policy must be above its lower amount."))

    @api.model
    def spm_evaluate(self, order):
        """Return the matching policies, ordered steps and cumulated amount of a purchase order.

        Requirements: BR-SPM-0009, FR-SPM-0043, FR-SPM-0044, FR-SPM-0045, FR-SPM-0046
        """
        return approval.evaluate(self.env, order)
