import time
from datetime import date, datetime

from odoo.tests import TransactionCase, freeze_time

MODULE = 'scheduled_record_reminder_mvp'


class TestDueWindowAcceptance(TransactionCase):
    """Acceptance tests of T-003: due moment, window, retries, switch on/off.

    Requirements: BR-SRR-0002, BR-SRR-0003, BR-SRR-0004, FR-SRR-0003,
    FR-SRR-0004, FR-SRR-0005, FR-SRR-0013, FR-SRR-0016, FR-SRR-0017.

    The scheduled job is called through the design's entry point
    ``rule._cron_send_reminders()``; the due moment through
    ``rule._compute_due_moment(value)`` (UTC datetime). No form is involved.
    """

    def setUp(self):
        super().setUp()
        self.Rule = self.env['record.reminder.rule']
        self.Execution = self.env['record.reminder.execution']
        self.partner_model = self.env['ir.model']._get('res.partner')
        self.todo = self.env.ref('mail.mail_activity_data_todo')
        self.ann = self.env['res.users'].create({
            'name': 'Ann', 'login': 'srr_due_ann',
            'email': 'srr_due_ann@example.com',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })

    def _field(self, name):
        return self.env['ir.model.fields']._get('res.partner', name)

    def _rule(self, **kw):
        vals = {
            'name': 'SRR due rule',
            'model_id': self.partner_model.id,
            'domain': "[('name', 'like', 'SRR-')]",
            'date_field_id': self._field('create_date').id,
            'offset': 0,
            'offset_unit': 'days',
            'offset_direction': 'before',
            'tz': 'UTC',
            'channel': 'activity',
            'activity_type_id': self.todo.id,
            'activity_summary': 'Call before renewal',
            'recipient_mode': 'users',
            'user_ids': [(6, 0, [self.ann.id])],
        }
        vals.update(kw)
        return self.Rule.create(vals)

    def _set_created(self, partner, moment):
        """Set the record's date (create_date, a date-and-time field) by SQL; None empties it."""
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE res_partner SET create_date = %s WHERE id = %s", [moment, partner.id])
        partner.invalidate_recordset(['create_date'])

    def _partner(self, name, day, **kw):
        """A partner whose date is the given day (midnight) or datetime."""
        vals = {'name': 'SRR-' + name}
        vals.update(kw)
        partner = self.env['res.partner'].create(vals)
        if isinstance(day, date) and not isinstance(day, datetime):
            day = datetime(day.year, day.month, day.day)
        self._set_created(partner, day)
        return partner

    def _date_rule(self, **kw):
        """An email rule on currency rates, whose date field `name` is a date without time."""
        if not getattr(self, 'currency', None):
            self.currency = self.env['res.currency'].create(
                {'name': 'XQX', 'symbol': 'X', 'active': True})
        rate_model = self.env['ir.model']._get('res.currency.rate')
        template = self.env['mail.template'].create({
            'name': 'SRR rate tpl', 'model_id': rate_model.id,
            'subject': 'Rate {{ object.name }}', 'body_html': '<p>x</p>',
            'auto_delete': False,
        })
        vals = {
            'name': 'SRR date rule', 'model_id': rate_model.id,
            'domain': "[('currency_id', '=', %d)]" % self.currency.id,
            'date_field_id': self.env['ir.model.fields']._get('res.currency.rate', 'name').id,
            'channel': 'email', 'mail_template_id': template.id,
            'activity_type_id': False,
        }
        vals.update(kw)
        return self._rule(**vals)

    def _run(self, at):
        with freeze_time(at):
            self.Rule._cron_send_reminders()
            self.env.flush_all()

    def _activities(self, partner):
        return self.env['mail.activity'].search([
            ('res_model', '=', 'res.partner'), ('res_id', '=', partner.id)])

    def _lines(self, rule, state=None):
        self.env.flush_all()
        domain = [('rule_id', '=', rule.id)]
        if state:
            domain.append(('state', '=', state))
        return self.Execution.search(domain)

    # --- FR-SRR-0003 ------------------------------------------------------
    def test_only_records_matching_the_conditions_are_reminded(self):
        """Requirements: FR-SRR-0003. Scenario SC-SRR-0003-1."""
        with freeze_time('2026-03-01 08:00:00'):
            rule = self._rule(domain="[('name', 'like', 'SRR-'), ('color', '=', 3)]")
        match = self._partner('company', date(2026, 3, 10), color=3)
        other = self._partner('person', date(2026, 3, 10), color=0)
        self._run('2026-03-10 09:00:00')
        self.assertEqual(len(self._activities(match)), 1)
        self.assertFalse(self._activities(other))
        self.assertEqual(self._lines(rule).mapped('res_id'), [match.id])

    def test_archived_records_get_no_reminder(self):
        """Requirements: FR-SRR-0003. Scenario SC-SRR-0003-2."""
        with freeze_time('2026-03-01 08:00:00'):
            rule = self._rule()
        active = self._partner('active', date(2026, 3, 10))
        archived = self._partner('archived', date(2026, 3, 10), active=False)
        self._run('2026-03-10 09:00:00')
        self.assertEqual(len(self._activities(active)), 1)
        self.assertFalse(self._activities(archived))
        self.assertEqual(self._lines(rule).mapped('res_id'), [active.id])

    # --- BR-SRR-0002 / FR-SRR-0004 ---------------------------------------
    def test_due_moment_of_a_date_is_midnight_in_the_rule_time_zone(self):
        """Requirements: FR-SRR-0004, BR-SRR-0002. Scenario SC-SRR-0004-1.

        3 days before 20 March, Europe/Brussels (UTC+1 then): 17 March 00:00
        Brussels is 16 March 23:00 UTC.
        """
        rule = self._date_rule(offset=3, tz='Europe/Brussels')
        self.assertEqual(rule._compute_due_moment(date(2026, 3, 20)),
                         datetime(2026, 3, 16, 23, 0))

    def test_due_moment_with_offset_zero_hours_and_after(self):
        """Requirements: BR-SRR-0002. Offset 0, hours and after."""
        zero = self._date_rule(offset=0)
        self.assertEqual(zero._compute_due_moment(date(2026, 3, 20)),
                         datetime(2026, 3, 20, 0, 0))
        hours = self._date_rule(offset=36, offset_unit='hours')
        self.assertEqual(hours._compute_due_moment(date(2026, 3, 20)),
                         datetime(2026, 3, 18, 12, 0))
        after = self._date_rule(offset=2, offset_direction='after')
        self.assertEqual(after._compute_due_moment(date(2026, 3, 20)),
                         datetime(2026, 3, 22, 0, 0))

    def test_due_moment_of_a_datetime_is_taken_as_it_is(self):
        """Requirements: FR-SRR-0004, BR-SRR-0002. Scenario SC-SRR-0004-2.

        2 hours after 10 March 14:00 is 16:00, whatever the rule's zone.
        """
        rule = self._rule(
            date_field_id=self._field('create_date').id, offset=2,
            offset_unit='hours', offset_direction='after', tz='Europe/Brussels')
        self.assertEqual(rule._compute_due_moment(datetime(2026, 3, 10, 14, 0)),
                         datetime(2026, 3, 10, 16, 0))

    def test_reminder_goes_out_exactly_at_the_due_moment(self):
        """Requirements: FR-SRR-0004, BR-SRR-0003. Boundary of the window.

        Due 16 March 23:00 UTC: one second before nothing is sent, at the
        due moment the reminder is sent.
        """
        with freeze_time('2026-03-01 08:00:00'):
            rule = self._date_rule(offset=3, tz='Europe/Brussels')
        rate = self.env['res.currency.rate'].create({
            'name': date(2026, 3, 20), 'currency_id': self.currency.id, 'rate': 1.0})
        mails = self.env['mail.mail']
        self._run('2026-03-16 22:59:59')
        self.assertFalse(mails.search([('model', '=', 'res.currency.rate'), ('res_id', '=', rate.id)]))
        self.assertFalse(self._lines(rule))
        self._run('2026-03-16 23:00:00')
        self.assertTrue(mails.search([('model', '=', 'res.currency.rate'), ('res_id', '=', rate.id)]))
        self.assertEqual(len(self._lines(rule, 'sent')), 1)

    def test_record_without_date_gets_no_reminder_and_no_line(self):
        """Requirements: FR-SRR-0004. Scenario SC-SRR-0004-3."""
        with freeze_time('2026-03-01 08:00:00'):
            rule = self._rule()
        partner = self._partner('nodate', None)
        self._run('2026-03-10 09:00:00')
        self.assertFalse(self._activities(partner))
        self.assertFalse(self._lines(rule))

    # --- FR-SRR-0005 / BR-SRR-0003 ---------------------------------------
    def test_only_records_whose_moment_has_come_are_reminded(self):
        """Requirements: FR-SRR-0005, BR-SRR-0003. Scenario SC-SRR-0005-1."""
        with freeze_time('2026-03-08 10:00:00'):
            rule = self._rule(offset=1)
        record_a = self._partner('A', date(2026, 3, 10))
        record_b = self._partner('B', date(2026, 3, 19))
        self._run('2026-03-09 10:00:00')
        self.assertEqual(len(self._activities(record_a)), 1)
        self.assertFalse(self._activities(record_b))
        self.assertEqual(self._lines(rule).mapped('res_id'), [record_a.id])

    def test_moments_before_the_switch_on_are_not_sent(self):
        """Requirements: BR-SRR-0003. Scenario SC-SRR-0005-2."""
        with freeze_time('2026-03-09 10:00:00'):
            rule = self._rule()
        old = self._partner('old', date(2026, 2, 10))
        self._run('2026-03-09 11:00:00')
        self.assertFalse(self._activities(old))
        self.assertFalse(self._lines(rule))

    def test_moment_equal_to_the_switch_on_is_sent(self):
        """Requirements: BR-SRR-0003. Due exactly at the switch-on moment."""
        with freeze_time('2026-03-10 00:00:00'):
            rule = self._rule()
        partner = self._partner('same', date(2026, 3, 10))
        self._run('2026-03-10 00:30:00')
        self.assertEqual(len(self._activities(partner)), 1)
        self.assertEqual(len(self._lines(rule, 'sent')), 1)

    def test_reminder_missed_while_the_job_was_stopped_is_sent_later(self):
        """Requirements: BR-SRR-0003. After the switch-on, a late run still sends."""
        with freeze_time('2026-03-01 08:00:00'):
            rule = self._rule()
        partner = self._partner('late', date(2026, 3, 10))
        self._run('2026-03-14 09:00:00')
        self.assertEqual(len(self._activities(partner)), 1)
        self.assertEqual(len(self._lines(rule, 'sent')), 1)

    def test_a_rerun_sends_nothing_more(self):
        """Requirements: FR-SRR-0005, FR-SRR-0010. The job runs every hour."""
        with freeze_time('2026-03-01 08:00:00'):
            rule = self._rule()
        partner = self._partner('hourly', date(2026, 3, 10))
        self._run('2026-03-10 09:00:00')
        self._run('2026-03-10 10:00:00')
        self.assertEqual(len(self._activities(partner)), 1)
        self.assertEqual(len(self._lines(rule)), 1)

    # --- FR-SRR-0013 / BR-SRR-0004 ---------------------------------------
    def _no_recipient_rule(self):
        return self._rule(
            recipient_mode='field', recipient_field_id=self._field('user_id').id,
            user_ids=[(5, 0, 0)])

    def test_failed_reminder_is_tried_again_and_then_sent(self):
        """Requirements: FR-SRR-0013. Scenario SC-SRR-0013-1.

        The failure here is a missing recipient, fixed before the next run.
        """
        with freeze_time('2026-03-01 08:00:00'):
            rule = self._no_recipient_rule()
        partner = self._partner('retry', date(2026, 3, 10))
        self._run('2026-03-10 08:00:00')
        self.assertEqual(len(self._lines(rule, 'failed')), 1)
        partner.user_id = self.ann
        self._run('2026-03-10 09:00:00')
        self.assertEqual(len(self._lines(rule, 'failed')), 1)
        self.assertEqual(len(self._lines(rule, 'sent')), 1)
        self.assertEqual(len(self._activities(partner)), 1)

    def test_failed_three_times_is_not_tried_again(self):
        """Requirements: FR-SRR-0013, BR-SRR-0004. Scenario SC-SRR-0013-2.

        Boundary: tries 1, 2 and 3 are made, the fourth and fifth are not.
        """
        with freeze_time('2026-03-01 08:00:00'):
            rule = self._no_recipient_rule()
        partner = self._partner('hopeless', date(2026, 3, 10))
        for hour in (8, 9, 10):
            self._run('2026-03-10 %02d:00:00' % hour)
        self.assertEqual(len(self._lines(rule, 'failed')), 3)
        self._run('2026-03-10 11:00:00')
        self._run('2026-03-10 12:00:00')
        self.assertEqual(len(self._lines(rule)), 3)
        self.assertFalse(self._lines(rule, 'sent'))
        self.assertFalse(self._activities(partner))

    # --- FR-SRR-0016 ------------------------------------------------------
    def test_reminder_due_while_the_rule_was_off_is_not_sent_afterwards(self):
        """Requirements: FR-SRR-0016, BR-SRR-0003. Scenario SC-SRR-0016-1."""
        with freeze_time('2026-03-01 08:00:00'):
            rule = self._rule()
        with freeze_time('2026-03-05 08:00:00'):
            rule.active = False
        partner = self._partner('paused', date(2026, 3, 10))
        with freeze_time('2026-03-12 08:00:00'):
            rule.active = True
        self.assertEqual(rule.activated_at, datetime(2026, 3, 12, 8, 0))
        self._run('2026-03-12 09:00:00')
        self.assertFalse(self._activities(partner))
        self.assertFalse(self._lines(rule))

    def test_switched_off_rule_sends_nothing(self):
        """Requirements: FR-SRR-0016. Scenario SC-SRR-0016-2."""
        with freeze_time('2026-03-01 08:00:00'):
            rule = self._rule()
        rule.active = False
        partner = self._partner('off', date(2026, 3, 10))
        self._run('2026-03-10 09:00:00')
        self.assertFalse(self._activities(partner))
        self.assertFalse(self.Execution.search([('rule_id', '=', rule.id)]))

    # --- FR-SRR-0017 ------------------------------------------------------
    def test_changed_offset_keeps_history_and_does_not_resend(self):
        """Requirements: FR-SRR-0017. Scenario SC-SRR-0017-1."""
        with freeze_time('2026-03-01 08:00:00'):
            rule = self._rule(offset=3)
        partner = self._partner('offset', date(2026, 3, 20))
        self._run('2026-03-18 09:00:00')
        first_line = self._lines(rule, 'sent')
        self.assertEqual(len(first_line), 1)
        rule.offset = 1
        self._run('2026-03-19 09:00:00')
        self.assertEqual(len(self._activities(partner)), 1)
        self.assertEqual(self._lines(rule), first_line)

    def test_changed_date_field_is_applied_from_the_next_run(self):
        """Requirements: FR-SRR-0017. The change applies, the history stays."""
        with freeze_time('2026-03-01 08:00:00'):
            rule = self._rule()
        partner = self._partner('later', date(2026, 3, 10), user_id=self.ann.id)
        self._run('2026-03-10 09:00:00')
        first_line = self._lines(rule, 'sent')
        rule.offset = 2
        self._set_created(partner, datetime(2026, 3, 30))
        self._run('2026-03-28 09:00:00')
        self.assertEqual(len(self._activities(partner)), 2)
        self.assertEqual(len(self._lines(rule, 'sent')), 2)
        self.assertIn(first_line, self._lines(rule))


class TestScheduledActionAcceptance(TransactionCase):
    """Requirements: FR-SRR-0005. The scheduled action runs every hour."""

    def test_hourly_scheduled_action_calls_the_engine(self):
        """Requirements: FR-SRR-0005. Scenario SC-SRR-0005-1 (job definition)."""
        cron = self.env['ir.cron'].search([
            ('model_id.model', '=', 'record.reminder.rule'),
            ('code', 'ilike', '_cron_send_reminders'),
        ])
        self.assertEqual(len(cron), 1)
        self.assertTrue(cron.active)
        self.assertEqual(cron.interval_number, 1)
        self.assertEqual(cron.interval_type, 'hours')


class TestLargeBatchAcceptance(TransactionCase):
    """Requirements: NFR-SRR-0001. One run, 100,000 records, 1,000 reminders."""

    def test_large_batch_runs_within_five_minutes(self):
        """Requirements: NFR-SRR-0001.

        100,000 partners are inserted by SQL, 1,000 of them due; one run
        sends the 1,000 reminders in under 300 seconds.
        """
        ann = self.env['res.users'].create({
            'name': 'Ann', 'login': 'srr_perf_ann',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        rule = self.env['record.reminder.rule'].create({
            'name': 'SRR perf',
            'model_id': self.env['ir.model']._get('res.partner').id,
            'domain': "[('name', 'like', 'SRR-PERF-')]",
            'date_field_id': self.env['ir.model.fields']._get('res.partner', 'create_date').id,
            'tz': 'UTC',
            'channel': 'activity',
            'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
            'recipient_mode': 'users',
            'user_ids': [(6, 0, [ann.id])],
        })
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE record_reminder_rule SET activated_at = now() at time zone 'utc' - interval '30 days' WHERE id = %s",
            [rule.id])
        self.env.cr.execute("""
            INSERT INTO res_partner (name, complete_name, active, type,
                                     create_date, write_date, create_uid, write_uid)
            SELECT 'SRR-PERF-' || g, 'SRR-PERF-' || g, TRUE, 'contact',
                   CASE WHEN g <= 1000 THEN now() at time zone 'utc' - interval '1 day'
                        ELSE now() at time zone 'utc' + interval '400 days' END,
                   now() at time zone 'utc', 1, 1
              FROM generate_series(1, 100000) g
        """)
        self.env.invalidate_all()
        start = time.perf_counter()
        self.env['record.reminder.rule']._cron_send_reminders()
        self.env.flush_all()
        elapsed = time.perf_counter() - start
        self.assertEqual(self.env['record.reminder.execution'].search_count(
            [('rule_id', '=', rule.id), ('state', '=', 'sent')]), 1000)
        self.assertLess(elapsed, 300)
