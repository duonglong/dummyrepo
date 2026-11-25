"""Acceptance tests of T-007 (qualification questionnaires, scoring and
approval sets status), written from the specification.

Requirements: FR-SPM-0009, FR-SPM-0010

Names used (fixed here, the design names none):
procurement.supplier.assessment.action_spm_submit() computes score, passed and
failed_knockout_ids; .action_spm_approve() is the Procurement manager's
qualification approval.
"""
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase

MODULE = 'supplier_procurement_management_hub'


class QualificationCase(TransactionCase):

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        partner_manager = self.env.ref('base.group_partner_manager').id
        self.buyer = self.env['res.users'].create({
            'name': 'Bea Buyer', 'login': 'spm_buyer_t007', 'email': 'spm_buyer_t007@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_buyer').id, partner_manager])],
        })
        self.manager = self.env['res.users'].create({
            'name': 'Mia Manager', 'login': 'spm_manager_t007', 'email': 'spm_manager_t007@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_manager').id, partner_manager])],
        })
        # five required questions of at most 10 points each: maximum 50 points;
        # the first one is the knock-out question 'Valid safety policy'.
        Question = self.env['procurement.questionnaire.question']
        self.questionnaire = self.env['procurement.questionnaire'].create({
            'name': 'Qualification T007', 'purpose': 'qualification', 'pass_score': 70})
        self.questions = Question
        for index, name in enumerate(['Valid safety policy', 'Quality system', 'Capacity',
                                      'Ethics', 'Delivery record']):
            self.questions |= Question.create({
                'questionnaire_id': self.questionnaire.id, 'sequence': index, 'name': name,
                'kind': 'choice', 'required': True, 'knockout': index == 0})
        Option = self.env['procurement.questionnaire.option']
        self.options = {}
        for question in self.questions:
            for label, points in (('Best', 10), ('Good', 8), ('Fair', 5), ('Poor', 4), ('None', 0)):
                self.options[(question.name, points)] = Option.create({
                    'question_id': question.id, 'name': label, 'points': points})
        knockout = self.questions[0]
        self.options[(knockout.name, 'No')] = Option.create({
            'question_id': knockout.id, 'name': 'No', 'points': 5, 'accepted': False})
        self.supplier_type = self.env['procurement.supplier.type'].create({
            'name': 'Goods T007', 'questionnaire_id': self.questionnaire.id})
        self.acme = self.env['res.partner'].create({
            'name': 'Acme', 'is_company': True, 'spm_supplier_type_id': self.supplier_type.id})

    def _assessment(self, points, knockout='Best'):
        """points: the points chosen for the four other questions (list of 4)."""
        answers = [(0, 0, {
            'question_id': self.questions[0].id,
            'option_id': (self.options[(self.questions[0].name, 'No')]
                          if knockout == 'No' else self.options[(self.questions[0].name, 10)]).id})]
        for question, given in zip(self.questions[1:], points):
            answers.append((0, 0, {
                'question_id': question.id, 'option_id': self.options[(question.name, given)].id}))
        return self.env['procurement.supplier.assessment'].with_user(self.buyer).create({
            'partner_id': self.acme.id, 'questionnaire_id': self.questionnaire.id,
            'answer_ids': answers})


class TestAssessmentScoreAcceptance(QualificationCase):
    """Requirements: FR-SPM-0009"""

    def test_sc_0009_1_forty_of_fifty_points_passes_with_score_80(self):
        """SC-SPM-0009-1: pass score 70, maximum 50 points; answers worth 40
        points and no failed knock-out give score 80 and Passed."""
        assessment = self._assessment([10, 10, 10, 0])  # 10 + 30 = 40
        assessment.with_user(self.buyer).action_spm_submit()
        self.env.flush_all()
        self.assertAlmostEqual(assessment.score, 80.0)
        self.assertTrue(assessment.passed)
        self.assertFalse(assessment.failed_knockout_ids)

    def test_sc_0009_1_score_at_the_pass_score_passes(self):
        """FR-SPM-0009 boundary: 35 of 50 points is score 70, equal to the pass score: Passed."""
        assessment = self._assessment([10, 10, 5, 0])  # 10 + 25 = 35
        assessment.with_user(self.buyer).action_spm_submit()
        self.assertAlmostEqual(assessment.score, 70.0)
        self.assertTrue(assessment.passed)

    def test_sc_0009_1_score_just_below_the_pass_score_fails(self):
        """FR-SPM-0009 boundary: 34 of 50 points is score 68, below 70: not Passed,
        with no knock-out named."""
        assessment = self._assessment([10, 10, 4, 0])  # 10 + 24 = 34
        assessment.with_user(self.buyer).action_spm_submit()
        self.assertAlmostEqual(assessment.score, 68.0)
        self.assertFalse(assessment.passed)
        self.assertFalse(assessment.failed_knockout_ids)

    def test_sc_0009_2_a_failed_knockout_fails_a_high_score(self):
        """SC-SPM-0009-2: Acme scores 90 but answers No to the knock-out
        question 'Valid safety policy': the assessment is Failed and that
        question is named."""
        assessment = self._assessment([10, 10, 10, 10], knockout='No')  # 5 + 40 = 45
        assessment.with_user(self.buyer).action_spm_submit()
        self.assertAlmostEqual(assessment.score, 90.0)
        self.assertFalse(assessment.passed)
        self.assertEqual(assessment.failed_knockout_ids.mapped('name'), ['Valid safety policy'])


class TestQualificationApprovalAcceptance(QualificationCase):
    """Requirements: FR-SPM-0010"""

    def setUp(self):
        super().setUp()
        self.insurance = self.env['procurement.document.type'].create({
            'name': 'Insurance certificate', 'needs_expiry': False})
        self.supplier_type.write({'required_document_type_ids': [(6, 0, [self.insurance.id])]})
        application = self.env['procurement.supplier.application'].with_user(self.buyer).create({
            'email': 'sales@acme.example', 'legal_name': 'Acme', 'vat': 'BE0123456789',
            'supplier_type_id': self.supplier_type.id, 'user_id': self.buyer.id})
        self.application = application
        self.insurance_doc = self.env['procurement.supplier.document'].with_user(self.buyer).create({
            'application_id': application.id, 'document_type_id': self.insurance.id})
        application.with_user(self.buyer).action_spm_submit()
        application.with_user(self.manager).action_spm_approve()
        self.acme = application.partner_id
        self.acme.write({'spm_supplier_type_id': self.supplier_type.id})
        self.insurance_doc.write({'partner_id': self.acme.id})

    def _qualification(self, points):
        assessment = self._assessment(points)
        assessment.write({'partner_id': self.acme.id, 'application_id': self.application.id})
        assessment.with_user(self.buyer).action_spm_submit()
        return assessment

    def _verify_insurance(self):
        self.insurance_doc.with_user(self.buyer).write({'verification_state': 'verified'})

    def test_sc_0010_1_everything_in_order_gives_the_approved_status(self):
        """SC-SPM-0010-1: application approved, required documents verified,
        assessment passed; approving the qualification gives Acme a status of
        kind Approved."""
        self._verify_insurance()
        assessment = self._qualification([10, 10, 10, 10])
        assessment.with_user(self.manager).action_spm_approve()
        self.env.flush_all()
        self.acme.invalidate_recordset()
        self.assertEqual(self.acme.spm_status_kind, 'approved')
        self.assertEqual(assessment.state, 'approved')
        self.assertEqual(assessment.approved_by_id, self.manager)

    def test_sc_0010_2_a_document_to_verify_gives_a_conditional_approval(self):
        """SC-SPM-0010-2: assessment passed but the Insurance certificate is
        To verify; Acme becomes Conditionally approved and the certificate is
        listed as an open item."""
        assessment = self._qualification([10, 10, 10, 10])
        assessment.with_user(self.manager).action_spm_approve()
        self.env.flush_all()
        self.acme.invalidate_recordset()
        self.assertEqual(self.acme.spm_status_kind, 'conditional')
        self.assertIn('Insurance certificate', assessment.open_items)

    def test_sc_0010_3_a_failed_assessment_cannot_be_approved(self):
        """SC-SPM-0010-3: assessment failed; approving is refused with a
        message that the assessment failed, and the status is unchanged."""
        self._verify_insurance()
        assessment = self._qualification([0, 0, 0, 0])
        before = self.acme.spm_status_id
        with self.assertRaises((UserError, ValidationError)) as ctx:
            assessment.with_user(self.manager).action_spm_approve()
        self.assertIn('failed', str(ctx.exception).lower())
        self.assertEqual(self.acme.spm_status_id, before)

    def test_only_a_procurement_manager_approves_a_qualification(self):
        """FR-SPM-0010: the approval is the Procurement manager's; a Buyer is refused."""
        self._verify_insurance()
        assessment = self._qualification([10, 10, 10, 10])
        with self.assertRaises(AccessError):
            assessment.with_user(self.buyer).action_spm_approve()
