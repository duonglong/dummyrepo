from odoo import fields, models

EXCEPTION_KINDS = [
    ('price', 'Price'), ('quantity', 'Quantity'), ('tax', 'Tax'),
    ('currency', 'Currency'), ('unit', 'Unit'), ('duplicate', 'Possible duplicate'),
]


class ProcurementExceptionRoute(models.Model):
    """Owner and approval need per exception kind and company.

    Requirements: FR-SPM-0075, FR-SPM-0077, FR-SPM-0108
    Built early for T-025 (its locked tests clear the routes); T-026 uses it to assign exceptions.
    """
    _name = 'procurement.exception.route'
    _inherit = ['procurement.audit.mixin']
    _description = 'Exception Route'
    _order = 'company_id, kind'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company,
                                 ondelete='cascade', index=True)
    kind = fields.Selection(EXCEPTION_KINDS, required=True)
    owner_id = fields.Many2one('res.users', string="Owner")
    owner_role = fields.Selection([('buyer', 'Buyer of the order'), ('finance', 'Finance reviewer')],
                                  required=True, default='finance')
    requires_approval = fields.Boolean(string="Needs approval before posting")

    _spm_audited_fields = ('company_id', 'kind', 'owner_id', 'owner_role', 'requires_approval')

    _kind_unique = models.Constraint('UNIQUE(company_id, kind)', 'One route per exception kind and company.')
