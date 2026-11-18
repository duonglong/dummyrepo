from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ProcurementDocumentType(models.Model):
    """A kind of supplier document with its reminder period.

    Requirements: FR-SPM-0108
    """
    _name = 'procurement.document.type'
    _inherit = ['procurement.audit.mixin']
    _description = 'Supplier Document Type'
    _order = 'name'

    name = fields.Char(required=True, translate=True)
    reminder_days = fields.Integer(default=30, help="Days before expiry when a reminder is sent.")
    needs_expiry = fields.Boolean(string="Has an expiry date")
    portal_upload = fields.Boolean(string="Suppliers can upload it")
    active = fields.Boolean(default=True)

    _spm_audited_fields = ('name', 'reminder_days', 'needs_expiry', 'portal_upload', 'active')

    @api.constrains('reminder_days')
    def _check_reminder_days(self):
        """Requirements: FR-SPM-0108"""
        if any(document_type.reminder_days < 0 for document_type in self):
            raise ValidationError(self.env._("The reminder period cannot be negative."))
