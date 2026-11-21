from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services import audit, risk, supplier_control

MANAGER_GROUP = 'supplier_procurement_management_hub.group_spm_manager'
# Fields that only the Complete action writes.
RESULT_FIELDS = ('state', 'score', 'level', 'is_current')
SCORE_RESULT_FIELDS = ('weight', 'score', 'document_ids', 'incident_ids', 'evidence')


def _is_real_change(records, name, value):
    """Return True when ``value`` for the result field ``name`` differs from what the records hold.

    A new record holds the empty value of the field (the draft state for ``state``). A
    form sends every field it shows, so a value equal to the current one is no change.
    """
    field = records._fields[name]
    if field.type in ('one2many', 'many2many'):
        current = set(records.mapped(name).ids)
        return any(not (command[0] == Command.CLEAR
                        or (command[0] == Command.SET and set(command[2]) == current)
                        or (command[0] == Command.LINK and command[1] in current))
                   for command in value or [])
    held = {record[name] for record in records} if records else {records.default_get([name]).get(name, False)}
    return any((held_value or False) != (value or False) for held_value in held)


class ProcurementRiskAssessment(models.Model):
    """A risk assessment of a supplier: category scores, total, level and evidence.

    Requirements: BR-SPM-0017, FR-SPM-0088
    """
    _name = 'procurement.risk.assessment'
    _description = 'Risk Assessment'
    _order = 'date desc, id desc'

    partner_id = fields.Many2one(
        'res.partner', string="Supplier", required=True, ondelete='cascade', index=True)
    date = fields.Date(required=True, default=fields.Date.context_today)
    assessed_by_id = fields.Many2one(
        'res.users', string="Assessed by", default=lambda self: self.env.user, ondelete='restrict')
    state = fields.Selection([('draft', 'Draft'), ('done', 'Done')],
                             required=True, default='draft', copy=False, index=True, readonly=True)
    category_score_ids = fields.One2many(
        'procurement.risk.category.score', 'assessment_id', string="Category scores", copy=True)
    screening_ids = fields.One2many('procurement.screening', 'assessment_id', string="Screenings")
    score = fields.Float(copy=False, readonly=True,
                         help="Weighted average of the category scores, 0 (no risk) to 100.")
    level = fields.Selection([('low', 'Low'), ('medium', 'Medium'), ('high', 'High')], copy=False, readonly=True)
    is_current = fields.Boolean(string="Current", copy=False, index=True, readonly=True)

    # --- write guards ---------------------------------------------------

    def _spm_refuse_result_values(self, vals):
        """Refuse a change of the result fields by a user: the Complete action writes them.

        Values that equal what the record holds are no change: the form sends them all.
        """
        if not self.env.su and any(
                name in vals and _is_real_change(self, name, vals[name]) for name in RESULT_FIELDS):
            raise AccessError(self.env._("The result of a risk assessment is set by completing it."))

    @api.model_create_multi
    def create(self, vals_list):
        """Requirements: FR-SPM-0088"""
        if not self.env.su:
            # a context default is added inside super().create, after the check: drop it for a user
            self = self.with_context({key: value for key, value in self.env.context.items()
                                      if not (key.startswith('default_') and key[8:] in RESULT_FIELDS)})
        for vals in vals_list:
            self._spm_refuse_result_values(vals)
        return super(ProcurementRiskAssessment, self).create(vals_list)

    def write(self, vals):
        """Requirements: FR-SPM-0088"""
        self._spm_refuse_result_values(vals)
        if not self.env.su and any(assessment.state != 'draft' for assessment in self) \
                and set(vals) - {'screening_ids'}:
            raise AccessError(self.env._("A completed risk assessment cannot be changed."))
        return super().write(vals)

    def unlink(self):
        """Requirements: FR-SPM-0088 (a completed assessment is evidence)."""
        if not self.env.su and any(assessment.state != 'draft' for assessment in self):
            raise AccessError(self.env._("A completed risk assessment cannot be deleted."))
        return super().unlink()

    # --- completion -----------------------------------------------------

    def action_spm_complete(self):
        """Complete the assessment: score each category, the total and level, and set the supplier's level.

        Requirements: BR-SPM-0017, FR-SPM-0088
        """
        if not self.env.user.has_group(MANAGER_GROUP):
            raise AccessError(self.env._("Only a Procurement manager can complete a risk assessment."))
        self.check_access('write')
        self.lock_for_update()
        for assessment in self:
            assessment.partner_id.check_access('read')
            if assessment.state != 'draft':
                raise UserError(self.env._("Only a draft risk assessment can be completed."))
            if not assessment.category_score_ids:
                raise UserError(self.env._("Add at least one risk category before completing."))
            missing = [f"{line.category_id.name}: {question.name}"
                       for line in assessment.category_score_ids if line.category_id.questionnaire_id
                       for question in risk.missing_questions(line.category_id.questionnaire_id, line.answer_ids)]
            if missing:
                raise UserError(self.env._(
                    "The assessment cannot be completed. Unanswered: %s.", "; ".join(missing)))
            sourceless = assessment.category_score_ids.filtered(
                lambda line: not risk.has_source(line.category_id, line.answer_ids))
            if sourceless:
                raise UserError(self.env._(
                    "The assessment cannot be completed: no answer or document to score for %s.",
                    ", ".join(sourceless.category_id.mapped('name'))))
            scores = [(line._spm_compute_score(), line.category_id.weight)
                      for line in assessment.category_score_ids]
            if not sum(weight for _score, weight in scores):
                raise UserError(self.env._("The risk categories of this assessment have no weight."))
            total = risk.weighted_total(scores)
            level = risk.level_for(self.env, total)
            previous = self.sudo().search([  # elevated: result fields, see the write guard
                ('partner_id', '=', assessment.partner_id.id), ('is_current', '=', True)])
            previous.write({'is_current': False})  # elevated: the result fields are written here only
            assessment.sudo().write({  # elevated: the result fields are written here only
                'state': 'done', 'score': total, 'level': level, 'is_current': True})
            assessment.partner_id.sudo().write({'spm_risk_level': level})  # elevated: restricted field
            audit.log(self.env, 'configuration', assessment, {'state': 'draft'},
                      {'state': 'done', 'score': total, 'level': level}, '')
        return True


class ProcurementRiskCategoryScore(models.Model):
    """One category's score with the answers and documents it comes from.

    Requirements: BR-SPM-0017, FR-SPM-0088
    """
    _name = 'procurement.risk.category.score'
    _description = 'Risk Category Score'
    _order = 'assessment_id, id'

    assessment_id = fields.Many2one(
        'procurement.risk.assessment', string="Assessment", required=True, ondelete='cascade', index=True)
    category_id = fields.Many2one(
        'procurement.risk.category', string="Category", required=True, ondelete='restrict')
    category_questionnaire_id = fields.Many2one(related='category_id.questionnaire_id')
    weight = fields.Float(copy=False, readonly=True)
    score = fields.Float(copy=False, readonly=True)
    answer_ids = fields.One2many('procurement.risk.answer', 'score_id', string="Answers", copy=True)
    document_ids = fields.Many2many(
        'procurement.supplier.document', 'spm_risk_score_document_rel', 'score_id', 'document_id',
        string="Documents", copy=False, readonly=True)
    incident_ids = fields.Many2many(
        'procurement.supplier.review', 'spm_risk_score_incident_rel', 'score_id', 'review_id',
        string="Incidents", copy=False, readonly=True)
    evidence = fields.Text(copy=False, readonly=True)

    def _spm_check_draft(self, assessments):
        """Refuse a change of the scores of an assessment that is no longer a draft."""
        if not self.env.su and any(assessment.state != 'draft' for assessment in assessments):
            raise AccessError(self.env._("The scores of a completed risk assessment cannot be changed."))

    def _spm_refuse_result_values(self, vals):
        """Refuse a change of the result fields by a user: the Complete action writes them."""
        if not self.env.su and any(
                name in vals and _is_real_change(self, name, vals[name]) for name in SCORE_RESULT_FIELDS):
            raise AccessError(self.env._("The score of a category is set by completing the assessment."))

    @api.model_create_multi
    def create(self, vals_list):
        """Requirements: FR-SPM-0088"""
        self._spm_check_draft(self.env['procurement.risk.assessment'].browse(
            [vals['assessment_id'] for vals in vals_list if vals.get('assessment_id')]))
        for vals in vals_list:
            self._spm_refuse_result_values(vals)
        return super().create(vals_list)

    def write(self, vals):
        """Requirements: FR-SPM-0088"""
        self._spm_check_draft(self.assessment_id)
        if 'assessment_id' in vals:
            self._spm_check_draft(self.env['procurement.risk.assessment'].browse(vals['assessment_id']))
        self._spm_refuse_result_values(vals)
        return super().write(vals)

    def unlink(self):
        """Requirements: FR-SPM-0088"""
        self._spm_check_draft(self.assessment_id)
        return super().unlink()

    def _spm_compute_score(self):
        """Compute and store the category score and its evidence; return the score.

        Requirements: BR-SPM-0017, FR-SPM-0088
        The questionnaire's answers and the supplier's required documents are the
        evidence; a category that uses neither scores 0. When both count, the score
        is their average. The incidents of the supplier are listed as evidence when the
        category uses them; they do not change the score.
        """
        self.ensure_one()
        category = self.category_id
        partner = self.assessment_id.partner_id
        parts, lines, documents = [], [], self.env['procurement.supplier.document']
        incidents = self.env['procurement.supplier.review']
        if category.questionnaire_id:
            parts.append(risk.answers_score(category.questionnaire_id, self.answer_ids))
            lines += [f"{answer.question_id.name}: {answer.option_id.name} ({answer.option_id.points:g})"
                      for answer in self.answer_ids if answer.option_id]
        if category.uses_documents:
            score, documents = risk.documents_score(self.env, partner)
            parts.append(score)
            labels = dict(documents._fields['validity_state']._description_selection(self.env),
                          missing=self.env._("Missing"))
            lines += [f"{document_type.name}: {labels[state]}"
                      for document_type, state in supplier_control.compliance_rows(self.env, partner)]
        if category.uses_incidents:
            incidents = risk.incidents_of(self.env, partner)
            lines += [self.env._("Incident: %s", incident.display_name) for incident in incidents]
        score = sum(parts) / len(parts) if parts else 0.0
        # Elevated: the result fields are written by the Complete action only (write guard).
        self.sudo().write({'weight': category.weight, 'score': score, 'document_ids': [(6, 0, documents.ids)],
                    'incident_ids': [(6, 0, incidents.ids)],
                    'evidence': '\n'.join(lines) or False})
        return score


class ProcurementRiskAnswer(models.Model):
    """The answer to one question of a risk category: evidence of its score.

    Requirements: FR-SPM-0088
    """
    _name = 'procurement.risk.answer'
    _description = 'Risk Answer'
    _order = 'score_id, id'

    score_id = fields.Many2one(
        'procurement.risk.category.score', string="Category score", required=True, ondelete='cascade', index=True)
    question_id = fields.Many2one(
        'procurement.questionnaire.question', string="Question", required=True, ondelete='restrict')
    option_id = fields.Many2one(
        'procurement.questionnaire.option', string="Answer", ondelete='restrict')
    points = fields.Float(related='option_id.points', store=True)

    _question_once = models.Constraint(
        'UNIQUE(score_id, question_id)', 'A question can be answered once per category.')

    @api.constrains('score_id', 'question_id', 'option_id')
    def _check_answer_belongs(self):
        """Requirements: FR-SPM-0088"""
        for answer in self:
            if answer.question_id.questionnaire_id != answer.score_id.category_id.questionnaire_id:
                raise ValidationError(self.env._("The question is not part of the category's questionnaire."))
            if answer.option_id and answer.option_id.question_id != answer.question_id:
                raise ValidationError(self.env._("The chosen answer does not belong to the question."))

    def _spm_check_draft(self, scores):
        """Refuse a change of the answers of an assessment that is no longer a draft."""
        if not self.env.su and any(score.assessment_id.state != 'draft' for score in scores):
            raise AccessError(self.env._("The answers of a completed risk assessment cannot be changed."))

    @api.model_create_multi
    def create(self, vals_list):
        """Requirements: FR-SPM-0088"""
        self._spm_check_draft(self.env['procurement.risk.category.score'].browse(
            [vals['score_id'] for vals in vals_list if vals.get('score_id')]))
        return super().create(vals_list)

    def write(self, vals):
        """Requirements: FR-SPM-0088"""
        self._spm_check_draft(self.score_id)
        if 'score_id' in vals:
            self._spm_check_draft(self.env['procurement.risk.category.score'].browse(vals['score_id']))
        return super().write(vals)

    def unlink(self):
        """Requirements: FR-SPM-0088"""
        self._spm_check_draft(self.score_id)
        return super().unlink()
