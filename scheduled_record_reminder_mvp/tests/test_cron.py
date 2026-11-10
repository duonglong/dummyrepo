import logging
from datetime import datetime
from unittest.mock import patch

from odoo import SUPERUSER_ID, api
from odoo.tests import TransactionCase, freeze_time

RULE_MODULE = 'odoo.addons.scheduled_record_reminder_mvp.models.record_reminder_rule'


class TestScheduledRun(TransactionCase):
    """Own tests of the scheduled run.

    Requirements: FR-SRR-0005, NFR-SRR-0001.
    """

    def setUp(self):
        super().setUp()
        self.todo = self.env.ref('mail.mail_activity_data_todo')
        self.ann = self.env['res.users'].create({
            'name': 'Ann', 'login': 'srr_cron_ann',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        self.Rule = self.env['record.reminder.rule']

    def _rule(self, name, tag):
        with freeze_time('2026-03-01 08:00:00'):
            return self.Rule.create({
                'name': name,
                'model_id': self.env['ir.model']._get('res.partner').id,
                'domain': "[('name', 'like', '%s')]" % tag,
                'date_field_id': self.env['ir.model.fields']._get('res.partner', 'create_date').id,
                'tz': 'UTC', 'channel': 'activity', 'activity_type_id': self.todo.id,
                'recipient_mode': 'users', 'user_ids': [(6, 0, [self.ann.id])],
            })

    def _partners(self, tag, count):
        partners = self.env['res.partner'].create([{'name': '%s %d' % (tag, i)} for i in range(count)])
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE res_partner SET create_date = %s WHERE id IN %s", [datetime(2026, 3, 10), tuple(partners.ids)])
        partners.invalidate_recordset(['create_date'])
        return partners

    def test_progress_is_committed_every_chunk(self):
        """Requirements: NFR-SRR-0001. 5 reminders with chunks of 2 commit twice, then the run reports nothing left."""
        rule = self._rule('chunks', 'SRR-CHUNK')
        self._partners('SRR-CHUNK', 5)
        with patch(RULE_MODULE + '.COMMIT_EVERY', 2), patch('odoo.modules.module.current_test', None), \
                patch.object(self.env.registry['ir.cron'], '_commit_progress', return_value=100.0) as commit, \
                freeze_time('2026-03-10 09:00:00'):
            self.Rule._cron_send_reminders()
        self.assertEqual([call.kwargs['remaining'] for call in commit.call_args_list], [3, 1, 0])
        self.assertEqual(len(rule.execution_ids), 5)

    def test_run_stops_when_the_time_of_the_job_is_used_up(self):
        """Requirements: NFR-SRR-0001. The next run goes on with what is left."""
        rule = self._rule('time', 'SRR-TIME')
        self._partners('SRR-TIME', 5)
        with patch(RULE_MODULE + '.COMMIT_EVERY', 2), patch('odoo.modules.module.current_test', None), \
                patch.object(self.env.registry['ir.cron'], '_commit_progress', return_value=0.0), \
                freeze_time('2026-03-10 09:00:00'):
            self.Rule._cron_send_reminders()
            self.assertEqual(len(rule.execution_ids), 2)
            self.Rule._cron_send_reminders()
            self.assertEqual(len(rule.execution_ids), 4)

    def test_a_rule_another_run_holds_is_left_alone(self):
        """Requirements: FR-SRR-0005. Another connection holds the rule's row: this run sends nothing for it.

        The rule and its due record are committed from a separate connection
        (the test transaction is invisible to others), a second connection
        locks the rule's row as a running job does, and the run is made from a
        third one. Everything committed is removed afterwards.
        """
        with freeze_time('2026-03-01 08:00:00'), self.registry.cursor() as setup_cr:
            env = api.Environment(setup_cr, SUPERUSER_ID, {})
            partner = env['res.partner'].create({'name': 'SRR-HELD 1'})
            rule = env['record.reminder.rule'].create({
                'name': 'held',
                'model_id': env['ir.model']._get('res.partner').id,
                'domain': "[('name', 'like', 'SRR-HELD')]",
                'date_field_id': env['ir.model.fields']._get('res.partner', 'create_date').id,
                'tz': 'UTC', 'channel': 'activity', 'activity_type_id': self.todo.id,
                'recipient_mode': 'users', 'user_ids': [(6, 0, [env.ref('base.user_admin').id])],
            })
            env.flush_all()
            setup_cr.execute("UPDATE res_partner SET create_date = %s WHERE id = %s", [datetime(2026, 3, 10), partner.id])
        self.addCleanup(self._remove_committed, rule.id, partner.id)

        holder_cr = self.registry.cursor()
        run_cr = self.registry.cursor()
        try:
            holder_cr.execute("SELECT id FROM record_reminder_rule WHERE id = %s FOR UPDATE", [rule.id])
            run_cr.execute("SET LOCAL lock_timeout = '5s'")  # without the guard the run would wait on the held row
            run_env = api.Environment(run_cr, SUPERUSER_ID, {})
            with freeze_time('2026-03-10 09:00:00'):
                run_env['record.reminder.rule']._cron_send_reminders()
            self.assertFalse(run_env['record.reminder.rule'].browse(rule.id).execution_ids)
        finally:
            run_cr.rollback()
            run_cr.close()
            holder_cr.rollback()
            holder_cr.close()

    def _remove_committed(self, rule_id: int, partner_id: int) -> None:
        with self.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            env['record.reminder.rule'].browse(rule_id).unlink()
            env['res.partner'].browse(partner_id).unlink()

    def test_a_rule_that_cannot_be_read_does_not_stop_the_others(self):
        """Requirements: FR-SRR-0005. The failure is logged as an error and the next rule is processed."""
        broken = self._rule('broken', 'SRR-BROKEN')
        good = self._rule('good', 'SRR-GOOD')
        self._partners('SRR-GOOD', 1)
        broken.env.cr.execute("UPDATE record_reminder_rule SET domain = %s WHERE id = %s", ["[('nofield', '=', 1)]", broken.id])
        broken.invalidate_recordset(['domain'])
        with freeze_time('2026-03-10 09:00:00'), self.assertLogs(RULE_MODULE, logging.ERROR):
            self.Rule._cron_send_reminders()
        self.assertEqual(len(good.execution_ids), 1)
