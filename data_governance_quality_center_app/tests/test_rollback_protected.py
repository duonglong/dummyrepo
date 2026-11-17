from unittest.mock import patch

from odoo.exceptions import UserError

from odoo.addons.data_governance_quality_center_app import const

from .acceptance_m2b_helpers import M2BCase, ROLLBACK, call_first


class TestRollbackProtected(M2BCase):
    """Regression tests for B-0209: a rollback never changes a protected record without authorization.

    A model marked restricted (or in the accounting list) after a correction run or a merge keeps
    its records as they are when the run or the merge is rolled back, unless an administrator
    authorized it. The rollback is reached through the buttons of the run and of the merge history.

    Requirements: BR-DGQ-0005, FR-DGQ-0050, FR-DGQ-0045, FR-DGQ-0051
    """

    def _restrict_partners(self):
        self.env['data.quality.model.config'].search([('model_name', '=', 'res.partner')]).write(
            {'status': 'restricted'})

    def _rollback_run(self, run):
        call_first(run.with_user(self.manager_a), ROLLBACK)
        rollback = self.env['data.quality.correction.run'].sudo().search([('rollback_of_id', '=', run.id)])
        return self.drain_job(rollback)

    def _trimmed_run(self, **vals):
        partner = self.partner('Ref', ref=' R1 ')
        correction = self.correction('res.partner', 'ref', ['trim'], domain_ids=partner.ids, **vals)
        self.dry_run(correction)
        if correction.needs_approval:
            self.approve(correction, self.manager_b)
        return partner, self.real_run(correction)

    def test_b_0209_rollback_of_a_run_skips_a_record_of_a_model_restricted_since(self):
        """B-0209: restricted after the run, the value stays and the line says the record is protected."""
        partner, run = self._trimmed_run()
        self.assertEqual(partner.ref, 'R1')
        self._restrict_partners()
        rollback = self._rollback_run(run)
        partner.invalidate_recordset()
        self.assertEqual(partner.ref, 'R1', "the rollback wrote a protected record")
        self.assertEqual((rollback.count_restored, rollback.count_not_restored), (0, 1))
        self.assertIn('protected', self.line_of(rollback, partner, 'not_restored').reason)

    def test_b_0209_rollback_of_a_run_skips_a_record_of_an_accounting_model(self):
        """B-0209: a model of the accounting list is protected at rollback time as at run time."""
        partner, run = self._trimmed_run()
        with patch.object(const, 'PROTECTED_MODELS', ('res.partner',)):
            rollback = self._rollback_run(run)
        partner.invalidate_recordset()
        self.assertEqual(partner.ref, 'R1')
        self.assertEqual(rollback.count_not_restored, 1)

    def test_b_0209_rollback_of_an_authorized_run_still_restores(self):
        """B-0209 guard: a correction an administrator authorized for protected records is rolled back."""
        self._restrict_partners()
        partner, run = self._trimmed_run(user=self.administrator, protected_authorized=True)
        self.assertEqual(partner.ref, 'R1')
        rollback = self._rollback_run(run)
        partner.invalidate_recordset()
        self.assertEqual(partner.ref, ' R1 ')
        self.assertEqual(rollback.count_restored, 1)

    def _merge(self):
        rule = self.contact_rule()
        first = self.partner('Acme Ltd', email='info@acme.com')
        second = self.partner('ACME Ltd.', email='INFO@acme.com')
        group = self.make_group(rule, first | second)
        wizard = self.open_preview(group=group, user=self.manager_a)
        wizard.action_confirm()
        self.env.flush_all()
        return self.merge_of(group), second

    def test_b_0209_rollback_of_a_merge_refuses_a_manager_once_the_model_is_restricted(self):
        """B-0209: the records stay merged and archived; the refusal names the protection."""
        merge, merged_away = self._merge()
        self._restrict_partners()
        with self.assertRaises(UserError) as caught:
            merge.with_user(self.manager_a).action_rollback()
        self.assertIn('protected', str(caught.exception))
        merged_away.invalidate_recordset()
        self.assertFalse(merged_away.active)
        self.assertEqual(merge.state, 'done')

    def test_b_0209_an_administrator_can_roll_back_a_merge_of_a_restricted_model(self):
        """B-0209 guard: the administrator's own rollback is the explicit authorization."""
        merge, merged_away = self._merge()
        self._restrict_partners()
        merge.with_user(self.administrator).action_rollback()
        merged_away.invalidate_recordset()
        self.assertTrue(merged_away.active)
