from odoo import api, fields, models


class ProcurementQuestionnaireQuestion(models.Model):
    """A question with options, points and a knock-out flag.

    Requirements: FR-SPM-0009
    """
    _name = 'procurement.questionnaire.question'
    _description = 'Questionnaire Question'
    _order = 'questionnaire_id, sequence, id'

    questionnaire_id = fields.Many2one(
        'procurement.questionnaire', string="Questionnaire", required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    name = fields.Text(string="Question", required=True, translate=True)
    kind = fields.Selection([('choice', 'Choice'), ('text', 'Text')], required=True, default='choice')
    required = fields.Boolean(default=True)
    knockout = fields.Boolean(string="Knock-out")
    max_points = fields.Float(
        string="Maximum points", compute='_compute_max_points', store=True)
    option_ids = fields.One2many('procurement.questionnaire.option', 'question_id', string="Options")

    @api.depends('kind', 'option_ids.points')
    def _compute_max_points(self):
        """Requirements: FR-SPM-0009

        The most points one answer can bring: the best option of a choice question.
        """
        for question in self:
            question.max_points = max(question.option_ids.mapped('points'), default=0.0) \
                if question.kind == 'choice' else 0.0
