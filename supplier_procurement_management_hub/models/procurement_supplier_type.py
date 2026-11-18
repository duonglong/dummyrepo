from odoo import fields, models


class ProcurementSupplierType(models.Model):
    """A kind of supplier deciding application fields and documents.

    Requirements: FR-SPM-0106
    Built for T-005 (minimal: the T-001 acceptance test needs it).
    """
    _name = 'procurement.supplier.type'
    _description = 'Supplier Type'
    _order = 'name'

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
