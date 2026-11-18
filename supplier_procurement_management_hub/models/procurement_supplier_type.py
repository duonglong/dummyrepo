from odoo import fields, models


class ProcurementSupplierType(models.Model):
    """A kind of supplier deciding application fields and documents.

    Requirements: FR-SPM-0106, FR-SPM-0108
    """
    _name = 'procurement.supplier.type'
    _inherit = ['procurement.audit.mixin']
    _description = 'Supplier Type'
    _order = 'name'

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    field_ids = fields.One2many('procurement.supplier.type.field', 'type_id', string="Application fields")
    required_document_type_ids = fields.Many2many(
        'procurement.document.type', string="Required documents")
    questionnaire_id = fields.Many2one(
        'procurement.questionnaire', string="Qualification questionnaire", ondelete='restrict',
        domain=[('purpose', '=', 'qualification')])

    _spm_audited_fields = ('name', 'active', 'required_document_type_ids', 'questionnaire_id')
