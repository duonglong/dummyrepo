from datetime import date, datetime

from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, freeze_time

MODULE = 'scheduled_record_reminder_mvp'


class TestRunNowAcceptance(TransactionCase):
    """Acceptance tests of T-004: Run now, and what it leaves in the history.

    Requirements: FR-SRR-0011, FR-SRR-0012, FR-SRR-0014, FR-SRR-0018.

    The Run now button is the design's ``rule.action_run_now()`` (the
    button of the rule form, calling it takes no field of a form: the button
    itself has no dialog). What the form step leaves to the build: the button
    on the form is checked only by its presence in the form arch. The method
    returns the client action that shows the message "N sent and M failed".
    """

    def setUp(self):
        super().setUp()
        self.Rule = self.env['record.reminder.rule']
        self.Execution = self.env['record.reminder.execution']
        self.partner_model = self.env['ir.model']._get('res.partner')
        self.todo = self.env.ref('mail.mail_activity_data_todo')
        self.ann = self.env['res.users'].create({
            'name': 'Ann', 'login': 'srr_rn_ann',
            'email': 'srr_rn_ann@example.com',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        self.template = self.env['mail.template'].create({
            'name': 'SRR run now template',
            'model_id': self.partner_model.id,
            'subject': 'Contract ends {{ object.name }}',
            'body_html': '<p>Ends soon</p>',
            'auto_delete': False,
        })

    def _field(self, name):
        return self.env['ir.model.fields']._get('res.partner', name)

    def _rule(self, **kw):
        vals = {
            'name': 'SRR run now rule',
            'model_id': self.partner_model.id,
            'domain': "[('name', 'like', 'SRR-')]",
            'date_field_id': self._field('create_date').id,
            'offset': 0, 'offset_unit': 'days', 'offset_direction': 'before',
            'tz': 'UTC',
            'channel': 'activity',
            'activity_type_id': self.todo.id,
            'activity_summary': 'Call before renewal',
            'recipient_mode': 'users',
            'user_ids': [(6, 0, [self.ann.id])],
        }
        vals.update(kw)
        return self.Rule.create(vals)

    def _no_recipient_rule(self):
        return self._rule(
            recipient_mode='field', recipient_field_id=self._field('user_id').id,
            user_ids=[(5, 0, 0)])

    def _partner(self, name, moment=datetime(2026, 3, 10, 0, 0), **kw):
        vals = {'name': 'SRR-' + name}
        vals.update(kw)
        partner = self.env['res.partner'].create(vals)
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE res_partner SET create_date = %s WHERE id = %s", [moment, partner.id])
        partner.invalidate_recordset(['create_date'])
        return partner

    def _cron(self, at):
        with freeze_time(at):
            self.Rule._cron_send_reminders()
            self.env.flush_all()

    def _run_now(self, rule, at, user=None):
        with freeze_time(at):
            action = (rule.with_user(user) if user else rule).action_run_now()
            self.env.flush_all()
        return action

    def _message(self, action):
        self.assertEqual(action.get('type'), 'ir.actions.client')
        self.assertEqual(action.get('tag'), 'display_notification')
        return str(action['params']['message'])

    def _lines(self, rule, state=None):
        self.env.flush_all()
        domain = [('rule_id', '=', rule.id)]
        if state:
            domain.append(('state', '=', state))
        return self.Execution.search(domain)

    # --- FR-SRR-0014 ------------------------------------------------------
    def test_run_now_button_is_on_the_rule_form(self):
        """Requirements: FR-SRR-0014. The form shows the Run now button."""
        arch = self.Rule.get_view(view_type='form')['arch']
        self.assertIn('name="action_run_now"', arch)

    def test_run_now_sends_both_reminders_including_the_exhausted_one(self):
        """Requirements: FR-SRR-0014, BR-SRR-0004. Scenario SC-SRR-0014-1."""
        with freeze_time('2026-03-01 08:00:00'):
            rule = self._no_recipient_rule()
        failing = self._partner('failing')
        for hour in (8, 9, 10):
            self._cron('2026-03-10 %02d:00:00' % hour)
        self.assertEqual(len(self._lines(rule, 'failed')), 3)
        fresh = self._partner('fresh')
        failing.user_id = self.ann
        fresh.user_id = self.ann
        action = self._run_now(rule, '2026-03-10 12:00:00')
        message = self._message(action)
        self.assertIn('2 sent', message)
        self.assertIn('0 failed', message)
        self.assertEqual(set(self._lines(rule, 'sent').mapped('res_id')),
                         {failing.id, fresh.id})
        for partner in (failing, fresh):
            self.assertEqual(self.env['mail.activity'].search_count([
                ('res_model', '=', 'res.partner'), ('res_id', '=', partner.id)]), 1)

    def test_run_now_reports_a_failure_and_tries_an_exhausted_reminder_again(self):
        """Requirements: FR-SRR-0014. The cause is not fixed: the 4th try fails."""
        with freeze_time('2026-03-01 08:00:00'):
            rule = self._no_recipient_rule()
        self._partner('hopeless')
        for hour in (8, 9, 10):
            self._cron('2026-03-10 %02d:00:00' % hour)
        self._cron('2026-03-10 11:00:00')
        self.assertEqual(len(self._lines(rule)), 3)
        message = self._message(self._run_now(rule, '2026-03-10 12:00:00'))
        self.assertIn('0 sent', message)
        self.assertIn('1 failed', message)
        self.assertEqual(len(self._lines(rule, 'failed')), 4)

    def test_run_now_with_nothing_due_sends_nothing(self):
        """Requirements: FR-SRR-0014. Scenario SC-SRR-0014-2."""
        with freeze_time('2026-03-01 08:00:00'):
            rule = self._rule()
        partner = self._partner('later', moment=datetime(2026, 4, 20, 0, 0))
        message = self._message(self._run_now(rule, '2026-03-10 12:00:00'))
        self.assertIn('0 sent', message)
        self.assertIn('0 failed', message)
        self.assertFalse(self._lines(rule))
        self.assertFalse(self.env['mail.activity'].search([
            ('res_model', '=', 'res.partner'), ('res_id', '=', partner.id)]))

    def test_run_now_does_not_send_what_was_already_sent(self):
        """Requirements: FR-SRR-0014. The duplicate rule still applies."""
        with freeze_time('2026-03-01 08:00:00'):
            rule = self._rule()
        self._partner('once')
        first = self._message(self._run_now(rule, '2026-03-10 12:00:00'))
        self.assertIn('1 sent', first)
        second = self._message(self._run_now(rule, '2026-03-10 13:00:00'))
        self.assertIn('0 sent', second)
        self.assertIn('0 failed', second)
        self.assertEqual(len(self._lines(rule, 'sent')), 1)

    # --- FR-SRR-0011 ------------------------------------------------------
    def test_sent_reminder_is_recorded_in_the_history(self):
        """Requirements: FR-SRR-0011. Scenario SC-SRR-0011-1 (run through Run now)."""
        with freeze_time('2026-03-01 08:00:00'):
            rule = self._rule(
                channel='email', mail_template_id=self.template.id,
                activity_type_id=False)
        partner = self._partner('S00042')
        self._run_now(rule, '2026-03-10 12:00:00')
        line = self._lines(rule)
        self.assertEqual(len(line), 1)
        self.assertEqual(line.state, 'sent')
        self.assertEqual(line.rule_id, rule)
        self.assertEqual(line.res_model, 'res.partner')
        self.assertEqual(line.res_id, partner.id)
        self.assertEqual(line.record_name, 'SRR-S00042')
        self.assertEqual(line.reminder_date, date(2026, 3, 10))
        self.assertEqual(line.channel, 'email')
        self.assertEqual(line.partner_ids, self.ann.partner_id)
        self.assertEqual(line.executed_at, datetime(2026, 3, 10, 12, 0))

    # --- FR-SRR-0012 ------------------------------------------------------
    def test_run_now_records_the_failure_and_goes_on(self):
        """Requirements: FR-SRR-0012, FR-SRR-0014. Scenario SC-SRR-0012-1 (run through Run now).

        Rendering divides by zero for the second record only (a template is
        rendered against one record when saved, so the expression holds for
        any other record).
        """
        self.template.subject = "{{ 1 / (0 if object.name == 'SRR-second' else 1) }}"
        with freeze_time('2026-03-01 08:00:00'):
            rule = self._rule(
                channel='email', mail_template_id=self.template.id,
                activity_type_id=False)
        first = self._partner('first')
        second = self._partner('second', moment=datetime(2026, 3, 10, 0, 1))
        third = self._partner('third', moment=datetime(2026, 3, 10, 0, 2))
        message = self._message(self._run_now(rule, '2026-03-10 12:00:00'))
        self.assertIn('2 sent', message)
        self.assertIn('1 failed', message)
        self.assertEqual(set(self._lines(rule, 'sent').mapped('res_id')),
                         {first.id, third.id})
        failed = self._lines(rule, 'failed')
        self.assertEqual(failed.res_id, second.id)
        self.assertTrue(failed.error_message)

    # --- FR-SRR-0018 ------------------------------------------------------
    def test_administrator_not_made_manager_can_create_and_run_a_rule(self):
        """Requirements: FR-SRR-0018. Scenario SC-SRR-0018-2."""
        admin = self.env['res.users'].create({
            'name': 'Plain admin', 'login': 'srr_rn_admin',
            'group_ids': [(6, 0, [self.env.ref('base.group_system').id])],
        })
        rule = self.Rule.with_user(admin).create({
            'name': 'Admin rule',
            'model_id': self.partner_model.id,
            'date_field_id': self._field('create_date').id,
            'tz': 'UTC', 'channel': 'activity',
            'activity_type_id': self.todo.id,
            'recipient_mode': 'users', 'user_ids': [(6, 0, [self.ann.id])],
        })
        message = self._message(self._run_now(rule, '2026-03-10 12:00:00', user=admin))
        self.assertIn('0 sent', message)
