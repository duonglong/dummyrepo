from odoo import fields, models


class ProcurementSupplierStatus(models.Model):
    """A configurable supplier status tied to one of seven kinds.

    Requirements: FR-SPM-0106, BR-SPM-0005, FR-SPM-0108
    """
    _name = 'procurement.supplier.status'
    _inherit = ['procurement.audit.mixin']
    _description = 'Supplier Status'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    kind = fields.Selection([
        ('prospect', 'Prospect'),
        ('in_review', 'In review'),
        ('approved', 'Approved'),
        ('conditional', 'Conditional'),
        ('requalification_due', 'Re-qualification due'),
        ('suspended', 'Suspended'),
        ('blocked', 'Blocked'),
    ], required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    default_for_kind = fields.Boolean()

    _spm_audited_fields = ('name', 'kind', 'sequence', 'active', 'default_for_kind')
