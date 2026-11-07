from datetime import timedelta

from odoo import fields
from odoo.tests.common import tagged

from .acceptance_base import AuditAcceptanceCase


@tagged('post_install', '-at_install')
class TestCronCheck(AuditAcceptanceCase):
    """Requirements: FR-OPA-0022, FR-OPA-0023, FR-OPA-0024, FR-OPA-0025"""

    def _cron(self, name, **vals):
        action = self.env['ir.actions.server'].create({
            'name': name, 'model_id': self.env['ir.model']._get('res.partner').id,
            'state': 'code', 'code': 'pass'})
        values = {
            'ir_actions_server_id': action.id, 'user_id': self.env.ref('base.user_root').id,
            'interval_number': 1, 'interval_type': 'days',
            'nextcall': fields.Datetime.now() + timedelta(days=1),
        }
        values.update(vals)
        return self.env['ir.cron'].create(values)

    def test_own_cron_is_reported_when_late_and_failing(self):
        """FR-OPA-0022, FR-OPA-0023: the tool's own scheduled action is reported like any other."""
        own = self.env.ref('perf_audit.ir_cron_execute_audit').sudo()  # test setup edits the shipped action
        own.nextcall = fields.Datetime.now() - timedelta(days=2)
        own.failure_count = 5
        run = self.start_run()
        kinds = run.finding_ids.filtered(lambda f: f.cron_id == own).mapped('kind')
        self.assertCountEqual(kinds, ['failure', 'late'])

    def test_shared_model_finding_has_suggestion(self):
        """FR-OPA-0025: two actions on one model give one finding carrying a proposed fix."""
        first, second = self._cron('b7 one'), self._cron('b7 two')
        found = self.findings(self.start_run(), 'B7', kind='shared').filtered(
            lambda f: f.model_id.model == 'res.partner')
        self.assertEqual(len(found), 1)
        self.assertTrue(found.code_suggestion)
        self.assertEqual({item['cron_id'] for item in found.references['crons']}, {first.id, second.id})

    def test_weekly_interval_in_minutes(self):
        """FR-OPA-0024: a weekly interval is compared in minutes (10080)."""
        cron = self._cron('b7 weekly', interval_number=1, interval_type='weeks')
        progress = self.env['ir.cron.progress'].create({'cron_id': cron.id})
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE ir_cron_progress SET create_date = now() - interval '2 days', write_date = now() "
            "WHERE id = %s", [progress.id])
        self.env.invalidate_all()
        run = self.start_run()
        self.assertFalse(self.findings(run, 'B7', kind='runtime').filtered(lambda f: f.cron_id == cron))
        self.env.cr.execute(
            "UPDATE ir_cron_progress SET create_date = now() - interval '8 days' WHERE id = %s", [progress.id])
        self.env.invalidate_all()
        run = self.start_run()
        found = self.findings(run, 'B7', kind='runtime').filtered(lambda f: f.cron_id == cron)
        self.assertEqual(found.threshold_value, 10080)

    def test_identity_key_per_cron(self):
        """FR-OPA-0022: the identity key of a finding names its scheduled action."""
        cron = self._cron('b7 key', failure_count=3, first_failure_date=fields.Datetime.now())
        found = self.findings(self.start_run(), 'B7', kind='failure').filtered(lambda f: f.cron_id == cron)
        self.assertEqual(found.identity_key, f'B7:failure:{cron.id}')
