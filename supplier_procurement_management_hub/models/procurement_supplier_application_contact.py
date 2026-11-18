from odoo import fields, models


class ProcurementSupplierApplicationContact(models.Model):
    """A person of the applying supplier; becomes a contact only at approval.

    Requirements: FR-SPM-0002
    """
    _name = 'procurement.supplier.application.contact'
    _description = 'Supplier Application Contact'
    _order = 'id'

    application_id = fields.Many2one(
        'procurement.supplier.application', required=True, ondelete='cascade', index=True)
    name = fields.Char(required=True)
    email = fields.Char()
    phone = fields.Char()
    function = fields.Char()
