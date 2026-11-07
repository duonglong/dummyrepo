from odoo.tests.common import tagged

from .common import PerfAuditCase


@tagged('post_install', '-at_install')
class TestFixScript(PerfAuditCase):
    """Requirements: FR-OPA-0028, BR-OPA-0004."""

    def setUp(self):
        super().setUp()
        self.run = self.env['perf.audit.run'].create({'state': 'done'})
        self.check = self.env['perf.audit.check'].search([('code', '=', 'A4')])

    def _finding(self, key, **values):
        base = {'run_id': self.run.id, 'check_id': self.check.id, 'identity_key': key,
                'severity': 'high', 'message': 'Message %s' % key}
        return self.env['perf.audit.finding'].create({**base, **values})

    def _script(self):
        return bytes(self.run.with_user(self.audit_admin).fix_script).decode()

    def test_evidence_and_filename_are_in_the_script(self):
        """FR-OPA-0028: the comment names the finding and its evidence; the file is a .sql file."""
        self._finding('k', table_name='res_partner', row_estimate=1200,
                      threshold_label='Dead rows', threshold_value=10000, found_value=25000,
                      fix_kind='analyze', fix_params={'table': 'res_partner'})
        script = self._script()
        self.assertIn('-- Evidence: table res_partner, about 1200 rows (estimate), '
                      'Dead rows: found 25000.0, threshold 10000.0', script)
        self.assertIn('ANALYZE "res_partner";', script)
        self.assertEqual(self.run.fix_script.filename, 'perf_audit_fix_script.sql')

    def test_unsafe_kind_has_no_statement_in_the_script(self):
        """BR-OPA-0004: a finding whose fix lacks its names adds no statement to the script."""
        self._finding('k', fix_kind='drop_index', fix_params={})
        self.assertNotIn('DROP', self._script())

    def test_multiline_message_stays_a_comment(self):
        """FR-OPA-0028: a line break inside a message cannot turn text into a statement."""
        self._finding('k', message='first\nDROP TABLE res_partner')
        self.assertTrue(all(
            line.startswith('--') for line in self._script().splitlines() if line.strip()))
