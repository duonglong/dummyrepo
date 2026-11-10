import time
from datetime import datetime
from unittest.mock import patch

from odoo.tests import TransactionCase, freeze_time

RULE_MODULE = 'odoo.addons.scheduled_record_reminder_mvp.models.record_reminder_rule'


class TestRunReportsWorkLeft(TransactionCase):
    """The scheduled run tells the native scheduler how many reminders are left.

    The scheduler runs a job again at once only when the job reported work
    left; with nothing reported it waits for the next interval (one hour).
    The run is called as the scheduler calls it: with a progress record of the
    shipped scheduled action and the end of the job's time in the context.

    Requirements: NFR-SRR-0001, FR-SRR-0005.
    """

    def setUp(self):
        super().setUp()
        ann = self.env['res.users'].create({
            'name': 'Ann', 'login': 'srr_perf_ann',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        with freeze_time('2026-03-01 08:00:00'):
            self.rule = self.env['record.reminder.rule'].create({
                'name': 'progress',
                'model_id': self.env['ir.model']._get('res.partner').id,
                'domain': "[('name', 'like', 'SRR-PROGRESS')]",
                'date_field_id': self.env['ir.model.fields']._get('res.partner', 'create_date').id,
                'tz': 'UTC', 'channel': 'activity',
                'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
                'recipient_mode': 'users', 'user_ids': [(6, 0, [ann.id])],
            })
        partners = self.env['res.partner'].create([{'name': 'SRR-PROGRESS %d' % i} for i in range(5)])
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE res_partner SET create_date = %s WHERE id IN %s", [datetime(2026, 3, 10), tuple(partners.ids)])
        partners.invalidate_recordset(['create_date'])
        self.cron = self.env.ref('scheduled_record_reminder_mvp.ir_cron_record_reminder_send')

    def _run_as_scheduler(self, seconds_left: float, at='2026-03-10 09:00:00'):
        """Run the job once, as the scheduler does, with ``seconds_left`` of its time; return its progress."""
        # sudo: progress records belong to the scheduler, as when it runs the job
        progress = self.env['ir.cron.progress'].sudo().create({'cron_id': self.cron.id, 'done': 0, 'remaining': 0})
        # the scheduler commits each chunk; a test transaction may not be committed
        with patch(RULE_MODULE + '.COMMIT_EVERY', 2), patch('odoo.modules.module.current_test', None), \
                patch.object(self.env.cr, 'commit'), freeze_time(at):
            context = {
                'cron_id': self.cron.id,
                'ir_cron_progress_id': progress.id,
                'cron_end_time': time.monotonic() + seconds_left,  # the frozen clock is the one the job reads
            }
            self.env['record.reminder.rule'].with_context(context)._cron_send_reminders()
        return progress

    def test_a_run_whose_time_is_used_up_reports_the_reminders_left(self):
        """Requirements: NFR-SRR-0001. 5 due, chunks of 2, no time left: 2 sent, 3 reported left."""
        progress = self._run_as_scheduler(seconds_left=-1)
        self.assertEqual(len(self.rule.execution_ids), 2)
        self.assertEqual(progress.done, 2)
        self.assertEqual(progress.remaining, 3)

    def test_a_complete_run_reports_nothing_left(self):
        """Requirements: NFR-SRR-0001. 5 due, enough time: all sent, nothing reported left."""
        progress = self._run_as_scheduler(seconds_left=600)
        self.assertEqual(len(self.rule.execution_ids), 5)
        self.assertEqual(progress.remaining, 0)

    def test_a_failure_is_not_tried_again_by_the_next_pass_of_the_same_job(self):
        """Requirements: BR-SRR-0004, FR-SRR-0013. The scheduler calls the job again at once while work is left.

        A reminder that failed in the first pass keeps one failed line after
        the second pass of the same job; the next job, an hour later (its own
        time budget, as the scheduler gives each job), tries it again.
        """
        failing = patch.object(self.env.registry['record.reminder.rule'], '_deliver', side_effect=ValueError('down'))
        with failing:
            self._run_as_scheduler(seconds_left=-1)
            self.assertEqual(len(self.rule.execution_ids), 2)
            self._run_as_scheduler(seconds_left=-1)
        self.assertEqual(self.rule.execution_ids.mapped('attempt'), [1, 1, 1, 1])
        with failing:
            self._run_as_scheduler(seconds_left=-1, at='2026-03-10 10:00:00')
        self.assertIn(2, self.rule.execution_ids.mapped('attempt'))

    def test_the_first_run_tries_the_failures_of_a_manual_run(self):
        """Requirements: BR-SRR-0004. A failure from before the job started (Run now) is tried by the job."""
        failing = patch.object(self.env.registry['record.reminder.rule'], '_deliver', side_effect=ValueError('down'))
        with failing, freeze_time('2026-03-10 08:30:00'):
            self.rule._send_due(datetime(2026, 3, 10, 8, 30))
        self.assertEqual(set(self.rule.execution_ids.mapped('attempt')), {1})
        self._run_as_scheduler(seconds_left=600)
        self.assertEqual(len(self.rule.execution_ids.filtered(lambda line: line.state == 'sent')), 5)
