from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError


class ProcurementAssessmentAnswer(models.Model):
    """One answer of a qualification assessment.

    Requirements: FR-SPM-0009
    Answers are evidence: they change only while the assessment is a draft.
    """
    _name = 'procurement.assessment.answer'
    _description = 'Assessment Answer'
    _order = 'assessment_id, question_id'

    assessment_id = fields.Many2one(
        'procurement.supplier.assessment', string="Assessment", required=True, ondelete='cascade', index=True)
    question_id = fields.Many2one(
        'procurement.questionnaire.question', string="Question", required=True, ondelete='restrict')
    option_id = fields.Many2one(
        'procurement.questionnaire.option', string="Answer", ondelete='restrict')
    text_answer = fields.Text(string="Text answer")
    points = fields.Float(related='option_id.points', store=True)

    _one_answer_per_question = models.Constraint(
        'UNIQUE(assessment_id, question_id)', 'A question is answered once per assessment.')

    @api.constrains('assessment_id', 'question_id', 'option_id')
    def _check_answer_belongs(self):
        """Requirements: FR-SPM-0009"""
        for answer in self:
            if answer.question_id.questionnaire_id != answer.assessment_id.questionnaire_id:
                raise ValidationError(self.env._("The question is not part of the assessment's questionnaire."))
            if answer.option_id and answer.option_id.question_id != answer.question_id:
                raise ValidationError(self.env._("The chosen answer does not belong to the question."))

    def _spm_check_draft(self, assessments):
        """Refuse a change of the answers of an assessment that is no longer a draft."""
        if not self.env.su and any(assessment.state != 'draft' for assessment in assessments):
            raise AccessError(self.env._("The answers of a submitted assessment cannot be changed."))

    @api.model_create_multi
    def create(self, vals_list):
        """Requirements: FR-SPM-0009"""
        assessments = self.env['procurement.supplier.assessment'].browse(
            [vals['assessment_id'] for vals in vals_list if vals.get('assessment_id')])
        self._spm_check_draft(assessments)
        return super().create(vals_list)

    def write(self, vals):
        """Requirements: FR-SPM-0009"""
        self._spm_check_draft(self.assessment_id)
        if 'assessment_id' in vals:
            self._spm_check_draft(self.env['procurement.supplier.assessment'].browse(vals['assessment_id']))
        return super().write(vals)

    def unlink(self):
        """Requirements: FR-SPM-0009"""
        self._spm_check_draft(self.assessment_id)
        return super().unlink()
