from odoo import fields, models


class ProcurementQuestionnaire(models.Model):
    """A questionnaire for qualification or a risk category, with a pass score.

    Requirements: FR-SPM-0009
    """
    _name = 'procurement.questionnaire'
    _inherit = ['procurement.audit.mixin']
    _description = 'Procurement Questionnaire'
    _order = 'name'

    name = fields.Char(required=True, translate=True)
    purpose = fields.Selection([
        ('qualification', 'Qualification'), ('risk', 'Risk'),
    ], required=True, default='qualification')
    pass_score = fields.Float(string="Pass score")
    question_ids = fields.One2many('procurement.questionnaire.question', 'questionnaire_id', string="Questions")
    active = fields.Boolean(default=True)

    _spm_audited_fields = ('name', 'purpose', 'pass_score', 'active')

    _pass_score_range = models.Constraint(
        'CHECK(pass_score >= 0 AND pass_score <= 100)', 'The pass score is a percentage between 0 and 100.')
