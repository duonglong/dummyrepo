from odoo import api, fields, models
from odoo.exceptions import ValidationError

PORTAL_FIELD_TYPES = ('char', 'text', 'boolean', 'integer', 'float', 'date', 'selection')
PORTAL_FORBIDDEN_PREFIXES = ('spm_status', 'spm_risk', 'bank')


class ProcurementPortalField(models.Model):
    """A contact field suppliers may see or edit in the portal.

    Requirements: FR-SPM-0108, BR-SPM-0004
    """
    _name = 'procurement.portal.field'
    _inherit = ['procurement.audit.mixin']
    _description = 'Portal Supplier Field'
    _order = 'sequence, id'

    field_id = fields.Many2one(
        'ir.model.fields', required=True, ondelete='cascade',
        domain="[('model', '=', 'res.partner'), ('ttype', 'in', %s)]" % (list(PORTAL_FIELD_TYPES),))
    editable = fields.Boolean(help="Suppliers can change the value, not only read it.")
    sequence = fields.Integer(default=10)

    _spm_audited_fields = ('field_id', 'editable')

    @api.constrains('field_id')
    def _check_field_id(self):
        """Requirements: BR-SPM-0004 (never a bank, status or risk field)."""
        for portal_field in self:
            field = portal_field.field_id
            if field.model != 'res.partner' or field.ttype not in PORTAL_FIELD_TYPES \
                    or field.name.startswith(PORTAL_FORBIDDEN_PREFIXES):
                raise ValidationError(self.env._(
                    "The field %s cannot be shown to suppliers.", field.field_description))
