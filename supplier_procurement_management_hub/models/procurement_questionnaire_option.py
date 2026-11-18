from odoo import fields, models


class ProcurementQuestionnaireOption(models.Model):
    """An answer option with points and acceptance.

    Requirements: FR-SPM-0009
    """
    _name = 'procurement.questionnaire.option'
    _description = 'Questionnaire Option'
    _order = 'question_id, id'

    question_id = fields.Many2one(
        'procurement.questionnaire.question', string="Question", required=True, ondelete='cascade', index=True)
    name = fields.Char(required=True, translate=True)
    points = fields.Float()
    accepted = fields.Boolean(default=True, help="An answer that is not accepted fails a knock-out question.")

    _points_positive = models.Constraint('CHECK(points >= 0)', 'The points cannot be negative.')
