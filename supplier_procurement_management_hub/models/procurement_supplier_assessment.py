from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..services import audit, supplier_control

MANAGER_GROUP = 'supplier_procurement_management_hub.group_spm_manager'
BUYER_GROUP = 'supplier_procurement_management_hub.group_spm_buyer'
# Fields that only the module's own actions write (after their access check).
RESULT_FIELDS = (
    'state', 'score', 'passed', 'failed_knockout_ids', 'open_items', 'approved_by_id', 'approved_date')
# Fields that stop changing once the assessment is submitted.
EVIDENCE_FIELDS = ('partner_id', 'application_id', 'questionnaire_id', 'answer_ids')


class ProcurementSupplierAssessment(models.Model):
    """A qualification assessment: answers, score, pass or fail and approval.

    Requirements: FR-SPM-0009, FR-SPM-0010
    """
    _name = 'procurement.supplier.assessment'
    _description = 'Supplier Assessment'
    _order = 'id desc'

    partner_id = fields.Many2one(
        'res.partner', string="Supplier", required=True, ondelete='restrict', index=True)
    application_id = fields.Many2one(
        'procurement.supplier.application', string="Application", ondelete='restrict', copy=False)
    questionnaire_id = fields.Many2one(
        'procurement.questionnaire', string="Questionnaire", required=True, ondelete='restrict',
        domain=[('purpose', '=', 'qualification')])
    state = fields.Selection([
        ('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'),
    ], required=True, default='draft', copy=False, index=True)
    answer_ids = fields.One2many('procurement.assessment.answer', 'assessment_id', string="Answers", copy=True)
    score = fields.Float(copy=False)
    passed = fields.Boolean(copy=False)
    failed_knockout_ids = fields.Many2many(
        'procurement.questionnaire.question', 'spm_assessment_failed_question_rel', 'assessment_id', 'question_id',
        string="Failed knock-out questions", copy=False)
    open_items = fields.Text(string="Open items", copy=False)
    approved_by_id = fields.Many2one('res.users', string="Approved by", copy=False, ondelete='set null')
    approved_date = fields.Datetime(copy=False)

    @api.constrains('partner_id', 'application_id')
    def _check_application_partner(self):
        """Requirements: FR-SPM-0010"""
        for assessment in self:
            application = assessment.application_id
            if application.partner_id and application.partner_id != assessment.partner_id:
                raise ValidationError(self.env._("The application belongs to another supplier."))

    # --- write guards ---------------------------------------------------

    def _spm_refuse_result_values(self, vals):
        """Refuse the result fields in the values of a user: the actions write them."""
        if not self.env.su and any(name in vals for name in RESULT_FIELDS):
            raise AccessError(self.env._(
                "The state, the score and the approval of an assessment change through its actions only."))

    @api.model_create_multi
    def create(self, vals_list):
        """Requirements: FR-SPM-0009"""
        if not self.env.su:
            # a context default is added inside super().create, after the check: drop it for a user
            self = self.with_context({key: value for key, value in self.env.context.items()
                                      if not (key.startswith('default_') and key[8:] in RESULT_FIELDS)})
        for vals in vals_list:
            self._spm_refuse_result_values(vals)
        return super(ProcurementSupplierAssessment, self).create(vals_list)

    def write(self, vals):
        """Requirements: FR-SPM-0009"""
        self._spm_refuse_result_values(vals)
        if not self.env.su and any(name in vals for name in EVIDENCE_FIELDS) \
                and any(assessment.state != 'draft' for assessment in self):
            raise AccessError(self.env._("A submitted assessment cannot be changed."))
        return super().write(vals)

    # --- scoring --------------------------------------------------------

    def _spm_scoring(self):
        """Return ``(score, failed knock-out questions)`` of the answers given.

        Requirements: FR-SPM-0009
        The maximum counts the questions that are required or answered; a
        knock-out question without an accepted answer fails.
        """
        self.ensure_one()
        answers = {answer.question_id: answer for answer in self.answer_ids}
        questions = self.questionnaire_id.question_ids
        counted = questions.filtered(lambda question: question.required or question in answers)
        maximum = sum(counted.mapped('max_points'))
        given = sum(answer.points for answer in self.answer_ids)
        score = given / maximum * 100 if maximum else 0.0
        failed = questions.filtered(
            lambda question: question.knockout
            and not (question in answers and answers[question].option_id.accepted))
        return score, failed

    def _spm_missing_answers(self):
        """Return the required questions without an answer."""
        self.ensure_one()
        answered = self.answer_ids.filtered(
            lambda answer: answer.option_id if answer.question_id.kind == 'choice'
            else (answer.text_answer or '').strip()).question_id
        return self.questionnaire_id.question_ids.filtered(
            lambda question: question.required and question not in answered)

    def action_spm_submit(self):
        """Submit the assessment: compute the score, the pass result and the failed knock-outs.

        Requirements: FR-SPM-0009
        """
        if not self.env.user.has_group(BUYER_GROUP):
            raise AccessError(self.env._("Only a Buyer or a Procurement manager can submit an assessment."))
        self.check_access('write')
        self.partner_id.check_access('read')
        self.lock_for_update()
        for assessment in self:
            if assessment.state != 'draft':
                raise UserError(self.env._("Only a draft assessment can be submitted."))
            missing = assessment._spm_missing_answers()
            if missing:
                raise UserError(self.env._(
                    "The assessment cannot be submitted. Unanswered: %s.", ", ".join(missing.mapped('name'))))
            score, failed = assessment._spm_scoring()
            passed = score >= assessment.questionnaire_id.pass_score and not failed
            # Elevated: the result fields are written by this action only (write guard).
            assessment.sudo().write({
                'state': 'submitted', 'score': score, 'passed': passed,
                'failed_knockout_ids': [(6, 0, failed.ids)]})
            audit.log(self.env, 'approval', assessment, {'state': 'draft'},
                      {'state': 'submitted', 'score': score, 'passed': passed}, '')
        return True

    # --- approval -------------------------------------------------------

    def _spm_open_items(self):
        """Return the open items of the supplier's required documents, one line each."""
        self.ensure_one()
        labels = dict(self.env['procurement.supplier.document']._fields['validity_state']
                      ._description_selection(self.env), missing=self.env._("Missing"))
        rows = supplier_control.compliance_rows(self.env, self.partner_id)
        return [f"{document_type.name}: {labels[state]}"
                for document_type, state in rows if state not in ('valid', 'expiring')]

    def action_spm_approve(self):
        """Approve the qualification: set the supplier status from the application, documents and result.

        Requirements: FR-SPM-0010
        """
        if not self.env.user.has_group(MANAGER_GROUP):
            raise AccessError(self.env._("Only a Procurement manager can approve a qualification."))
        self.check_access('write')
        self.lock_for_update()
        statuses = self.env['procurement.supplier.status'].sudo()  # elevated: configuration read, role checked above
        for assessment in self:
            if assessment.state != 'submitted':
                raise UserError(self.env._("Only a submitted assessment can be approved."))
            if not assessment.passed:
                raise UserError(self.env._(
                    "The assessment failed: it cannot be approved. Repeat the questionnaire."))
            application = assessment.application_id
            if application:
                application.check_access('read')
                if application.state != 'approved':
                    raise UserError(self.env._("The supplier's application is not approved yet."))
            partner = assessment.partner_id
            partner.check_access('read')
            open_items = assessment._spm_open_items()
            kind = 'conditional' if open_items else 'approved'
            status = statuses.search([('kind', '=', kind), ('default_for_kind', '=', True)], limit=1)
            if not status:
                raise UserError(self.env._("No default supplier status of the kind %s is configured.", kind))
            partner.action_spm_set_status(status)
            # Elevated: the result fields are written by this action only (write guard).
            assessment.sudo().write({
                'state': 'approved', 'open_items': '\n'.join(open_items) or False,
                'approved_by_id': self.env.user.id, 'approved_date': fields.Datetime.now()})
            audit.log(self.env, 'approval', assessment, {'state': 'submitted'},
                      {'state': 'approved', 'status_kind': kind}, '')
        return True
