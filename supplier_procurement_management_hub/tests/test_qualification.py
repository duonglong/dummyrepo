"""Own tests of T-007 (scoring edge cases and guards).

Requirements: FR-SPM-0009, FR-SPM-0010
"""
from lxml import etree

from odoo.exceptions import AccessError, UserError, ValidationError

from .test_qualification_acceptance import QualificationCase


class TestQualificationRules(QualificationCase):
    """Requirements: FR-SPM-0009, FR-SPM-0010"""

    def test_a_missing_required_answer_refuses_the_submission(self):
        """FR-SPM-0009: a required question left unanswered stops the submission."""
        assessment = self._assessment([10, 10, 10, 10])
        assessment.answer_ids[-1].unlink()
        with self.assertRaises(UserError):
            assessment.with_user(self.buyer).action_spm_submit()

    def test_a_submitted_assessment_cannot_be_changed_or_scored_by_hand(self):
        """FR-SPM-0009: result fields and answers are written by the actions only."""
        assessment = self._assessment([10, 10, 10, 10])
        with self.assertRaises(AccessError):
            assessment.write({'passed': True})
        assessment.action_spm_submit()
        with self.assertRaises(AccessError):
            assessment.answer_ids[0].write({'option_id': self.options[(self.questions[1].name, 0)].id})
        with self.assertRaises(AccessError):
            assessment.write({'questionnaire_id': self.questionnaire.id})

    def test_an_approval_needs_the_default_status_of_the_kind(self):
        """FR-SPM-0010: without a default status the approval is refused and nothing changes."""
        assessment = self._assessment([10, 10, 10, 10])
        assessment.action_spm_submit()
        self.env['procurement.supplier.status'].search(
            [('kind', '=', 'approved')]).write({'default_for_kind': False})
        with self.assertRaises(UserError):
            assessment.with_user(self.manager).action_spm_approve()
        self.assertEqual(assessment.state, 'submitted')

    def test_an_empty_optional_answer_does_not_lower_the_score(self):
        """FR-SPM-0009: a blank answer row of an optional question is not counted."""
        optional = self.env['procurement.questionnaire.question'].create({
            'questionnaire_id': self.questionnaire.id, 'sequence': 9, 'name': 'Optional',
            'kind': 'choice', 'required': False})
        self.env['procurement.questionnaire.option'].create(
            {'question_id': optional.id, 'name': 'Top', 'points': 10})
        assessment = self._assessment([10, 10, 10, 0])
        self.env['procurement.assessment.answer'].create(
            {'assessment_id': assessment.id, 'question_id': optional.id})
        assessment.action_spm_submit()
        self.assertAlmostEqual(assessment.score, 80.0)

    def test_the_questionnaire_cannot_change_under_existing_answers(self):
        """FR-SPM-0009: old answers must not inflate the score of another questionnaire."""
        assessment = self._assessment([10, 10, 10, 10])
        other = self.env['procurement.questionnaire'].create({
            'name': 'Other', 'purpose': 'qualification', 'pass_score': 10})
        with self.assertRaises(ValidationError):
            assessment.write({'questionnaire_id': other.id})


class TestSupplierFormQualification(QualificationCase):
    """Requirements: FR-SPM-0010, FR-SPM-0086"""

    def test_bug_b_0273_the_supplier_shows_the_score_of_its_latest_approved_qualification(self):
        """Regression test for B-0273: the supplier holds the score of its latest approved assessment.

        Requirements: FR-SPM-0010
        """
        self.assertFalse(self.acme.spm_qualification_score)
        assessment = self._assessment([10, 10, 10, 0])
        assessment.with_user(self.buyer).action_spm_submit()
        self.assertFalse(self.acme.spm_qualification_score, "A submitted assessment is not a qualification yet")
        assessment.with_user(self.manager).action_spm_approve()
        self.assertEqual(self.acme.spm_qualification_score, 80.0)

    def test_bug_b_0273_the_procurement_tab_shows_score_actions_and_status_kind(self):
        """Regression test for B-0273: the Procurement tab of a buyer carries the score, the open corrective
        actions and the kind of the status, and no heading stands over an empty value.

        Requirements: FR-SPM-0010, FR-SPM-0086
        It cannot reach the rendered tab: it reads the form the server sends to the buyer.
        """
        view = self.env['res.partner'].with_user(self.buyer).get_view(
            self.env.ref('base.view_partner_form').id, 'form')
        arch = etree.fromstring(view['arch'])
        tab = arch.xpath("//page[@name='spm_procurement']")[0]
        shown = {node.get('name'): node for node in tab.iter('field')}
        for name in ('spm_qualification_score', 'spm_open_action_count', 'spm_status_kind'):
            self.assertIn(name, shown)
        for name in ('spm_compliance_state', 'spm_compliance_summary'):
            self.assertIn('not ' + name, shown[name].get('invisible', ''))
