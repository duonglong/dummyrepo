from odoo import fields, models


class ProcurementSupplierTypeField(models.Model):
    """One application field of a supplier type marked shown, required or hidden.

    Requirements: FR-SPM-0108
    Other modules extend ``field_key`` with ``selection_add``.
    """
    _name = 'procurement.supplier.type.field'
    _description = 'Supplier Type Field'
    _order = 'type_id, id'

    type_id = fields.Many2one('procurement.supplier.type', required=True, ondelete='cascade', index=True)
    field_key = fields.Selection([
        ('phone', 'Phone'),
        ('website', 'Website'),
        ('vat', 'Tax identifier'),
        ('street', 'Address'),
        ('bank', 'Bank account'),
        ('categories', 'Product categories'),
        ('operating_locations', 'Operating locations'),
    ], required=True)
    visibility = fields.Selection([
        ('shown', 'Shown'),
        ('required', 'Required'),
        ('hidden', 'Hidden'),
    ], required=True, default='shown')

    _type_field_unique = models.Constraint(
        'unique(type_id, field_key)', "A field can be set only once for a supplier type.")
