from unittest.mock import patch

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

    def test_outside_a_test_the_statement_runs_after_the_request_commits(self):
        """FR-OPA-0029: the real path defers the statement so no snapshot of the request is open."""
        finding = self._finding().with_user(self.audit_admin)
        ran = []

        def run_alone(dbname, statement):
            ran.append(statement)
            return None

        with self.registry_test_mode(), \
                patch.object(type(finding), '_run_statement_alone', staticmethod(run_alone)):
            with patch('odoo.addons.perf_audit.models.perf_audit_finding.odoo_module.current_test', False):
                finding._apply_fix()
            self.assertEqual((ran, finding.apply_state), ([], 'none'), 'nothing runs inside the request')
            self.env.cr.postcommit.run()
        self.assertEqual(ran, ['ANALYZE "res_partner"'])
        self.assertEqual((finding.apply_state, finding.applied_by), ('applied', self.audit_admin))

    def test_deferred_failure_is_recorded(self):
        """FR-OPA-0029: a database error of the deferred statement is recorded as failed."""
        finding = self._finding()
        with patch.object(type(finding), '_run_statement_alone', staticmethod(lambda d, s: 'boom')), \
                self.registry_test_mode():
            finding._apply_after_commit(self.env.cr.dbname, finding.id, self.env.uid, 'x')
        self.assertEqual((finding.apply_state, finding.apply_error), ('failed', 'boom'))
