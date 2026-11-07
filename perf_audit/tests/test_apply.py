from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .common import PerfAuditCase


@tagged('post_install', '-at_install')
class TestApply(PerfAuditCase):
    """Requirements: FR-OPA-0029, BR-OPA-0004."""

    def setUp(self):
        super().setUp()
        self.run = self.env['perf.audit.run'].create({'state': 'done'})
        self.check = self.env['perf.audit.check'].search([('code', '=', 'A4')])

    def _finding(self, **values):
        base = {'run_id': self.run.id, 'check_id': self.check.id, 'identity_key': 'k',
                'severity': 'high', 'message': 'm', 'fix_kind': 'analyze',
                'fix_params': {'table': 'res_partner'}}
        return self.env['perf.audit.finding'].create({**base, **values})

    def test_wizard_shows_statement_and_confirm_records_outcome(self):
        """FR-OPA-0029: the dialog shows the statement; confirming records who, when and applied."""
        finding = self._finding().with_user(self.audit_admin)
        action = finding.action_apply()
        wizard = self.env['perf.audit.apply.wizard'].with_user(self.audit_admin).with_context(
            **action['context']).create({})
        self.assertEqual(wizard.statement, 'ANALYZE "res_partner"')
        wizard.action_confirm()
        self.assertEqual(
            (finding.apply_state, finding.applied_by, bool(finding.applied_date)),
            ('applied', self.audit_admin, True))

    def test_failed_statement_keeps_the_error(self):
        """FR-OPA-0029: a database error is recorded and the finding can be tried again."""
        finding = self._finding(fix_params={'table': 'no_such_table'})
        finding._apply_fix()
        self.assertEqual(finding.apply_state, 'failed')
        self.assertIn('no_such_table', finding.apply_error)
        self.assertTrue(finding.can_apply)

    def test_applied_finding_cannot_be_applied_again(self):
        """FR-OPA-0029: an applied finding offers no Apply and refuses a second one."""
        finding = self._finding()
        finding._apply_fix()
        self.assertFalse(finding.can_apply)
        with self.assertRaises(UserError):
            finding.action_apply()
        with self.assertRaises(UserError):
            finding._apply_fix()

    def test_finding_of_a_running_or_older_run_has_no_apply(self):
        """FR-OPA-0029: only the latest done run offers Apply."""
        newer = self.env['perf.audit.run'].create({
            'state': 'done', 'start_date': '2999-01-01 00:00:00'})
        self.assertFalse(self._finding().can_apply)
        self.assertTrue(self._finding(run_id=newer.id).can_apply)
