from odoo import fields, models


class ProcurementSupplierApplication(models.Model):
    """An onboarding request filled by a supplier or a buyer.

    Requirements: FR-SPM-0106
    Built for T-005 (minimal: the T-001 acceptance test needs it).
    """
    _name = 'procurement.supplier.application'
    _description = 'Supplier Application'
    _order = 'id desc'

    name = fields.Char(copy=False)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('changes_requested', 'Changes requested'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ], default='draft', required=True)
    email = fields.Char(required=True)
    supplier_type_id = fields.Many2one(
        'procurement.supplier.type', required=True, ondelete='restrict')
    legal_name = fields.Char(required=True)
    company_id = fields.Many2one(
        'res.company', default=lambda self: self.env.company, required=True)
