from datetime import datetime

from unittest.mock import patch

from odoo import Command
from odoo.addons.scheduled_record_reminder_mvp.models.record_reminder_rule import RUN_NOW_LIMIT
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, freeze_time


class TestRunNow(TransactionCase):
    """Requirements: FR-SRR-0014. Run now refuses a switched-off rule."""

    def test_run_now_refuses_a_switched_off_rule(self):
        """The Run now button has no field or dialog, so the method is called directly."""
        rule = self.env['record.reminder.rule'].create({
            'name': 'Off rule',
            'model_id': self.env['ir.model']._get('res.partner').id,
            'date_field_id': self.env['ir.model.fields']._get('res.partner', 'create_date').id,
            'tz': 'UTC', 'channel': 'activity',
            'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
            'recipient_mode': 'users', 'user_ids': [(6, 0, [self.env.ref('base.user_admin').id])],
            'active': False,
        })
        with self.assertRaises(UserError):
            rule.action_run_now()


class TestRunNowBatch(TransactionCase):
    """Run now sends a small batch in the request and hands the rest to the scheduled run.

    Requirements: FR-SRR-0010, FR-SRR-0014.
    """

    def setUp(self):
        super().setUp()
        self.cron = self.env.ref('scheduled_record_reminder_mvp.ir_cron_record_reminder_send')
        with freeze_time('2026-03-01 08:00:00'):
            self.rule = self.env['record.reminder.rule'].create({
                'name': 'SRR batch rule',
                'model_id': self.env['ir.model']._get('res.partner').id,
                'domain': "[('name', 'like', 'SRR-batch')]",
                'date_field_id': self.env['ir.model.fields']._get('res.partner', 'create_date').id,
                'tz': 'UTC', 'channel': 'activity',
                'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
                'recipient_mode': 'users',
                'user_ids': [Command.set([self.env.ref('base.user_admin').id])],
            })

    def _due_partners(self, count):
        partners = self.env['res.partner'].create([{'name': f'SRR-batch {n}'} for n in range(count)])
        self.env.flush_all()
        self.env.cr.execute("UPDATE res_partner SET create_date = %s WHERE id IN %s",
                            [datetime(2026, 3, 10), tuple(partners.ids)])
        partners.invalidate_recordset(['create_date'])

    def _run_now(self):
        with freeze_time('2026-03-10 08:00:00'):
            return self.rule.action_run_now()['params']['message']

    def _triggers(self):
        return self.env['ir.cron.trigger'].search_count([('cron_id', '=', self.cron.id)])

    def test_b_0172_run_now_sends_a_small_batch_and_hands_the_rest_to_the_scheduled_run(self):
        """Regression test for B-0172 (R3-2): Run now must not send every due email in one request transaction.

        Requirements: FR-SRR-0010, FR-SRR-0014
        """
        self._due_partners(RUN_NOW_LIMIT + 5)
        triggers_before = self._triggers()
        message = self._run_now()
        sent = self.env['record.reminder.execution'].search_count(
            [('rule_id', '=', self.rule.id), ('state', '=', 'sent')])
        self.assertEqual(sent, RUN_NOW_LIMIT)
        self.assertIn(f"{RUN_NOW_LIMIT} sent", message)
        self.assertIn("5 more", message)
        self.assertEqual(self._triggers(), triggers_before + 1)

    def test_run_now_with_few_reminders_sends_all_and_starts_no_extra_run(self):
        """Requirements: FR-SRR-0014. At the limit nothing is left over."""
        self._due_partners(RUN_NOW_LIMIT)
        triggers_before = self._triggers()
        message = self._run_now()
        self.assertIn(f"{RUN_NOW_LIMIT} sent", message)
        self.assertNotIn("more", message)
        self.assertEqual(self._triggers(), triggers_before)


class TestRunNowExhaustedReminders(TestRunNowBatch):
    """Run now tells the truth about what the scheduled run takes and walks forward through failures.

    Requirements: FR-SRR-0013, FR-SRR-0014, BR-SRR-0004.
    """

    def _fail_times(self, partners, times):
        executions = self.env['record.reminder.execution']
        for partner in partners:
            key = self.rule._reminder_key(partner.create_date)
            for attempt in range(1, times + 1):
                executions.create({
                    'rule_id': self.rule.id, 'res_model': 'res.partner', 'res_id': partner.id,
                    'reminder_key': key, 'reminder_date': partner.create_date.date(), 'channel': 'activity',
                    'state': 'failed', 'error_message': 'down', 'attempt': attempt,
                    'executed_at': datetime(2026, 3, 10, 6, attempt),
                })

    def _partners(self, count):
        self._due_partners(count)
        return self.env['res.partner'].search([('name', 'like', 'SRR-batch')], order='id')

    def test_b_0173_left_over_that_failed_three_times_is_not_promised_to_the_scheduled_run(self):
        """Regression test for B-0173 (R4-1): the cron never takes a reminder that failed 3 times.

        Requirements: FR-SRR-0014, BR-SRR-0004
        """
        partners = self._partners(RUN_NOW_LIMIT + 5)
        self._fail_times(partners, 3)
        triggers_before = self._triggers()
        message = self._run_now()
        self.assertNotIn("scheduled run sends", message)
        self.assertIn("5 already failed 3 times", message)
        self.assertEqual(self._triggers(), triggers_before)

    def test_b_0173_left_over_is_split_between_the_scheduled_run_and_run_now(self):
        """Never-failed reminders go first; the message names who takes what is left.

        Requirements: FR-SRR-0014, BR-SRR-0004
        """
        partners = self._partners(RUN_NOW_LIMIT + 5)
        self._fail_times(partners[:3], 3)
        triggers_before = self._triggers()
        message = self._run_now()
        self.assertIn("2 more go out with the scheduled run", message)
        self.assertIn("3 already failed 3 times", message)
        self.assertEqual(self._triggers(), triggers_before + 1)

    def test_b_0173_repeated_run_now_walks_forward_through_failing_reminders(self):
        """The second Run now reaches the reminders the first one did not try.

        Requirements: FR-SRR-0014, BR-SRR-0004
        """
        partners = self._partners(RUN_NOW_LIMIT + 5)
        self._fail_times(partners, 3)
        with patch.object(type(self.rule), '_deliver', side_effect=UserError("still down")):
            self._run_now()
            self._run_now()
        untried = [
            partner.id for partner in partners
            if self.env['record.reminder.execution'].search_count(
                [('rule_id', '=', self.rule.id), ('res_id', '=', partner.id), ('state', '=', 'failed')]) == 3]
        self.assertEqual(untried, [])
