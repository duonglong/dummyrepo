from odoo import Command
from odoo.exceptions import UserError

from .acceptance_m2b_helpers import M2BCase, REAL_RUN, call_first, choice


class TestCorrectionPolicy(M2BCase):
    """Unit tests of the correction policy engine beyond the acceptance scenarios.

    Requirements: BR-DGQ-0006, FR-DGQ-0047, FR-DGQ-0048
    """

    def setUp(self):
        super().setUp()
        self.critical_rule = self.make_rule(
            'res.partner', 'pattern', ['email'], severity='critical', pattern=r'^[a-z]+@[a-z]+\.com$')
        self.padded = self.partner('Padded', email=' ACME@Mail.COM ')

    def test_a_mapping_change_sends_an_approved_correction_back_to_draft(self):
        """FR-DGQ-0048: the mappings are part of what a correction does."""
        correction = self.correction(
            'res.partner', 'email', ['email'], rule=self.critical_rule, domain_ids=self.padded.ids)
        self.dry_run(correction)
        self.approve(correction, self.manager_b)
        self.assertEqual(correction.state, 'approved')
        correction.write({'mapping_ids': [Command.create({'source_text': 'UK'})]})
        correction.invalidate_recordset()
        self.assertEqual(correction.state, 'draft')
        self.assertFalse(correction.last_dry_run_id)

    def test_a_mark_set_after_saving_still_asks_for_approval(self):
        """BR-DGQ-0006: the need for approval is read when it matters, not when it was saved."""
        correction = self.correction('res.partner', 'email', ['email'], domain_ids=self.padded.ids)
        self.assertFalse(correction.needs_approval)
        self.env['data.quality.model.config'].search([('model_name', '=', 'res.partner')]).write(
            {'status': 'restricted'})
        correction.invalidate_recordset()
        self.assertTrue(correction.needs_approval)

    def test_a_dry_run_of_protected_records_skips_them(self):
        """FR-DGQ-0047: records of a restricted model are skipped as protected and nothing is written."""
        self.env['data.quality.model.config'].search([('model_name', '=', 'res.partner')]).write(
            {'status': 'restricted'})
        correction = self.correction('res.partner', 'email', ['email'], domain_ids=self.padded.ids)
        run = self.dry_run(correction)
        self.assertEqual(run.count_skipped, 1)
        self.assertIn('protected', run.log_ids.reason.lower())

    def test_a_stale_dry_run_does_not_allow_a_real_run(self):
        """FR-DGQ-0047: a change of the definition removes the dry run: a new one is needed."""
        correction = self.correction('res.partner', 'email', ['email'], domain_ids=self.padded.ids)
        self.dry_run(correction)
        correction.write({'action': self.correction_action(['trim'])})
        with self.assertRaises(UserError) as caught:
            call_first(correction, REAL_RUN)
        self.assertIn('dry run', str(caught.exception).lower())

    def correction_action(self, words):
        return choice(self.env, 'data.quality.correction', 'action', words)
