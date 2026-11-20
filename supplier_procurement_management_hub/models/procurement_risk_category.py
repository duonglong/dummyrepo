from odoo import fields, models


class ProcurementRiskCategory(models.Model):
    """A risk category with its weight, questionnaire and use of the supplier's documents.

    Requirements: BR-SPM-0017, FR-SPM-0088
    """
    _name = 'procurement.risk.category'
    _inherit = ['procurement.audit.mixin']
    _description = 'Risk Category'
    _order = 'sequence, id'

    name = fields.Char(required=True, translate=True)
    weight = fields.Float(default=1.0)
    questionnaire_id = fields.Many2one(
        'procurement.questionnaire', string="Questionnaire", ondelete='set null',
        domain=[('purpose', '=', 'risk')])
    uses_documents = fields.Boolean(
        string="Uses documents", help="Expired, missing or rejected required documents raise this category's score.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _spm_audited_fields = ('name', 'weight', 'questionnaire_id', 'uses_documents', 'active')

    _weight_positive = models.Constraint('CHECK(weight >= 0)', 'The weight cannot be negative.')
