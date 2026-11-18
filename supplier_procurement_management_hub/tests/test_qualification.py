"""Own tests of T-007 (scoring edge cases and guards).

Requirements: FR-SPM-0009, FR-SPM-0010
"""
from odoo.exceptions import AccessError, UserError

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
