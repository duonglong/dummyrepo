from odoo import api, fields, models
from odoo.exceptions import ValidationError

# Application fields that are always asked (FR-SPM-0002): the supplier cannot be identified without them.
ALWAYS_REQUIRED_KEYS = ('legal_name', 'email')


class ProcurementSupplierTypeField(models.Model):
    """One application field of a supplier type marked shown, required or hidden.

    Requirements: FR-SPM-0108, FR-SPM-0002
    Other modules extend ``field_key`` with ``selection_add``.
    """
    _name = 'procurement.supplier.type.field'
    _description = 'Supplier Type Field'
    _order = 'type_id, id'

    type_id = fields.Many2one('procurement.supplier.type', required=True, ondelete='cascade', index=True)
    field_key = fields.Selection([
        ('legal_name', 'Legal name'),
        ('email', 'E-mail'),
        ('contacts', 'Contacts'),
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

    @api.constrains('field_key', 'visibility')
    def _check_always_required(self):
        """Requirements: FR-SPM-0002 (legal name and e-mail are never hidden)."""
        labels = dict(self._fields['field_key']._description_selection(self.env))
        for rule in self:
            if rule.field_key in ALWAYS_REQUIRED_KEYS and rule.visibility == 'hidden':
                raise ValidationError(self.env._(
                    "%s is always required: it cannot be hidden.", labels[rule.field_key]))
