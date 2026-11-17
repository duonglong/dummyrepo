from odoo import fields

from .acceptance_m3_helpers import M3Case


class TestHealthAutomation(M3Case):
    """Regression tests for B-0213: the health page shows the automations and their failed runs.

    The figures are read through the call the health page makes as an administrator; the page
    itself is client side and is not reached here.

    Requirements: FR-DGQ-0079, FR-DGQ-0069
    """

    def setUp(self):
        super().setUp()
        self.automation = self.make_automation(name='Nightly contacts')
        self.automation.sudo().write({
            'last_run': fields.Datetime.subtract(fields.Datetime.now(), hours=3),
            'next_run': fields.Datetime.add(fields.Datetime.now(), hours=21)})

    def _health(self):
        return self.env['data.quality.dashboard'].with_user(self.administrator).get_health()

    def _failed_run(self, message, days_ago=0):
        run = self.env['data.quality.automation.run'].sudo().create({'automation_id': self.automation.id})
        run._dgq_fail(message, notify=False)
        end = fields.Datetime.subtract(fields.Datetime.now(), days=days_ago)
        run.write({'date_end': end})
        return run

    def test_b_0213_each_active_automation_is_listed_with_its_last_and_next_run(self):
        """B-0213: the page names the automation with the times of its last and next run."""
        self.make_automation(name='Archived one').sudo().write({'active': False})
        listed = {row['name']: row for row in self._health().get('automations', [])}
        self.assertEqual(list(listed), ['Nightly contacts'])
        self.assertEqual(listed['Nightly contacts']['last_run'], fields.Datetime.to_string(self.automation.last_run))
        self.assertEqual(listed['Nightly contacts']['next_run'], fields.Datetime.to_string(self.automation.next_run))

    def test_b_0213_a_failed_automation_run_of_the_last_7_days_is_listed_with_its_reason(self):
        """B-0213: a failed run shows with its reason and the record to open; a run of 8 days ago does not."""
        recent = self._failed_run('The scan failed after 3 retries')
        old = self._failed_run('Too old to show', days_ago=8)
        failed = {(row['model'], row['id']): row for row in self._health()['failed_runs']}
        self.assertEqual(failed[('data.quality.automation.run', recent.id)]['error'], 'The scan failed after 3 retries')
        self.assertNotIn(('data.quality.automation.run', old.id), failed)
