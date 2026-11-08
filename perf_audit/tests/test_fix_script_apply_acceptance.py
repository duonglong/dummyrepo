"""Acceptance tests of T-011: download the fix script and apply one fix after confirmation.

Requirements: BR-OPA-0004, FR-OPA-0028, FR-OPA-0029
"""
from datetime import timedelta

from odoo import sql_db
from odoo.tests.common import tagged

from .acceptance_base import AuditAcceptanceCase

# The spec names no method for "Apply" and "Confirm"; these are the names tried, in order.
APPLY_METHODS = ('action_apply', 'action_apply_fix', 'apply_fix', 'action_open_apply_wizard')
CONFIRM_METHODS = ('action_confirm', 'action_apply', 'confirm', 'action_apply_fix')

INDEX = 'perf_audit_acc_res_partner_function_idx'


class FixCase(AuditAcceptanceCase):
    """Runs and findings built directly, as the administrator sees them."""

    def make_run(self, hours_ago=1):
        return self.env['perf.audit.run'].sudo().create({
            'state': 'done', 'user_id': self.audit_admin.id,
            'start_date': self.now() - timedelta(hours=hours_ago),
            'end_date': self.now() - timedelta(hours=hours_ago),
        })

    def make_finding(self, run, code, key, **kw):
        check = self.env['perf.audit.check'].sudo().search([('code', '=', code)])
        vals = {
            'run_id': run.id, 'check_id': check.id, 'identity_key': key,
            'severity': 'high', 'message': 'Message of %s' % key,
        }
        vals.update(kw)
        return self.env['perf.audit.finding'].sudo().create(vals)

    def make_a2(self, run, index=INDEX):
        return self.make_finding(
            run, 'A2', 'A2:res.partner.function', table_name='res_partner', row_estimate=500000,
            fix_kind='create_index',
            fix_params={'table': 'res_partner', 'columns': ['function'], 'index_name': index})

    def index_exists(self, name):
        self.env.cr.execute("SELECT 1 FROM pg_indexes WHERE indexname = %s", [name])
        return bool(self.env.cr.fetchone())

    def drop_index_later(self, name):
        def drop():
            with sql_db.db_connect(self.env.cr.dbname).cursor() as cr:
                cr.execute('DROP INDEX IF EXISTS "%s"' % name)
        self.addCleanup(drop)


@tagged('post_install', '-at_install')
class TestFixScriptAcceptance(FixCase):
    """Requirements: FR-OPA-0028, BR-OPA-0004 (SC-OPA-0028-1)."""

    def script(self, run):
        run = run.with_user(self.audit_admin)
        data = run.fix_script
        self.assertTrue(data, 'the fix script of a run is a file')
        return bytes(data).decode()

    def test_script_holds_comments_and_statement_and_creates_nothing(self):
        """SC-OPA-0028-1: a comment per finding, the create-index statement, no index created."""
        run = self.make_run()
        a2 = self.make_a2(run)
        b1 = self.make_finding(
            run, 'B1', 'B1:res.partner.heavy', code_suggestion='Add store=True to the field')
        text = self.script(run)
        self.assertIn('CREATE INDEX CONCURRENTLY', text)
        self.assertIn(INDEX, text)
        self.assertIn(a2.message, text)
        self.assertIn(b1.message, text)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        self.assertTrue(any(line.startswith('--') and a2.message in line for line in lines))
        self.assertTrue(any(line.startswith('--') and b1.message in line for line in lines))
        statements = [line for line in lines if not line.startswith('--')]
        self.assertEqual(len(statements), 1, 'only the A2 finding has a database statement')
        self.assertIn('CREATE INDEX CONCURRENTLY', statements[0])
        self.assertFalse(self.index_exists(INDEX), 'downloading runs none of the statements')

    def test_script_without_database_fix_is_comments_only(self):
        """FR-OPA-0028: a run with no database fix gives a script with comments only."""
        run = self.make_run()
        self.make_finding(run, 'B1', 'B1:res.partner.heavy', code_suggestion='Hide the column')
        text = self.script(run)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        self.assertTrue(lines)
        self.assertTrue(all(line.startswith('--') for line in lines), text)

    def test_script_of_run_with_no_finding_is_still_a_file(self):
        """FR-OPA-0028: an empty run still downloads a script, with no statement."""
        run = self.make_run().with_user(self.audit_admin)
        text = bytes(run.fix_script or b"").decode()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        self.assertTrue(all(line.startswith('--') for line in lines))

    def test_script_only_holds_statements_of_this_run(self):
        """FR-OPA-0028: each finding of the run, and no finding of another run."""
        other = self.make_run(hours_ago=3)
        self.make_a2(other, index='perf_audit_acc_other_run_idx')
        run = self.make_run()
        self.make_a2(run)
        text = self.script(run)
        self.assertIn(INDEX, text)
        self.assertNotIn('perf_audit_acc_other_run_idx', text)


@tagged('post_install', '-at_install')
class TestApplyAcceptance(FixCase):
    """Requirements: FR-OPA-0029, BR-OPA-0004 (SC-OPA-0029-1 to SC-OPA-0029-4)."""

    def open_wizard(self, finding):
        finding = finding.with_user(self.audit_admin)
        for method in APPLY_METHODS:
            if hasattr(finding, method):
                action = getattr(finding, method)()
                break
        else:
            self.fail('perf.audit.finding has no Apply method (tried %s)' % ', '.join(APPLY_METHODS))
        self.assertEqual(action.get('res_model'), 'perf.audit.apply.wizard')
        if action.get('res_id'):
            return self.env['perf.audit.apply.wizard'].with_user(self.audit_admin).browse(action['res_id'])
        return self.env['perf.audit.apply.wizard'].with_user(self.audit_admin).with_context(
            **action.get('context', {})).create({'finding_id': finding.id})

    def confirm(self, wizard):
        for method in CONFIRM_METHODS:
            if hasattr(wizard, method):
                getattr(wizard, method)()
                break
        else:
            self.fail('perf.audit.apply.wizard has no Confirm method (tried %s)' % ', '.join(CONFIRM_METHODS))
        self.env.flush_all()

    def test_confirm_creates_the_index_and_records_the_outcome(self):
        """SC-OPA-0029-1: Apply + confirm builds the index; admin, time and outcome are recorded."""
        self.drop_index_later(INDEX)
        finding = self.make_a2(self.make_run())
        wizard = self.open_wizard(finding)
        self.assertEqual(wizard.statement, finding.fix_statement, 'the confirmation shows the statement')
        self.confirm(wizard)
        self.assertTrue(self.index_exists(INDEX))
        finding.invalidate_recordset()
        self.assertEqual(finding.apply_state, 'applied')
        self.assertEqual(finding.applied_by, self.audit_admin)
        self.assertTrue(finding.applied_date)
        self.assertFalse(finding.can_apply, 'an applied finding offers no Apply')

    def test_cancel_changes_nothing(self):
        """SC-OPA-0029-2: opening the confirmation and not confirming creates no index."""
        finding = self.make_a2(self.make_run())
        wizard = self.open_wizard(finding)
        self.assertTrue(wizard.statement)
        self.env.flush_all()
        self.assertFalse(self.index_exists(INDEX))
        self.assertEqual(finding.apply_state, 'none')
        self.assertFalse(finding.applied_by)

    def test_failing_statement_records_the_error_and_nothing_else(self):
        """SC-OPA-0029-3: a taken index name gives outcome failed with the database error."""
        finding = self.make_a2(self.make_run(), index='res_partner_pkey')
        self.confirm(self.open_wizard(finding))
        finding.invalidate_recordset()
        self.assertEqual(finding.apply_state, 'failed')
        self.assertIn('already exists', finding.apply_error)
        self.assertTrue(self.index_exists('res_partner_pkey'), 'the existing index is untouched')

    def test_no_apply_for_a_run_that_is_not_the_latest(self):
        """SC-OPA-0029-4: a finding of an older run offers no Apply; the latest run's does."""
        old_finding = self.make_a2(self.make_run(hours_ago=3))
        new_finding = self.make_a2(self.make_run(hours_ago=1))
        self.assertFalse(old_finding.can_apply)
        self.assertTrue(new_finding.can_apply)
