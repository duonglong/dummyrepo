from datetime import date, datetime

from odoo.tests import TransactionCase, freeze_time


class TestDueWindowEdges(TransactionCase):
    """Own tests of the due window helpers.

    Requirements: FR-SRR-0004, BR-SRR-0002, BR-SRR-0003, BR-SRR-0004, FR-SRR-0013.
    """

    def setUp(self):
        super().setUp()
        self.todo = self.env.ref('mail.mail_activity_data_todo')
        self.ann = self.env['res.users'].create({
            'name': 'Ann', 'login': 'srr_edge_ann',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })

    def _rule(self, **kw):
        vals = {
            'name': 'SRR edge rule',
            'model_id': self.env['ir.model']._get('res.partner').id,
            'domain': "[('name', 'like', 'SRR-EDGE')]",
            'date_field_id': self.env['ir.model.fields']._get('res.partner', 'create_date').id,
            'tz': 'UTC', 'channel': 'activity', 'activity_type_id': self.todo.id,
            'recipient_mode': 'users', 'user_ids': [(6, 0, [self.ann.id])],
        }
        vals.update(kw)
        return self.env['record.reminder.rule'].create(vals)

    def _partner(self, moment):
        partner = self.env['res.partner'].create({'name': 'SRR-EDGE'})
        self.env.flush_all()
        self.env.cr.execute("UPDATE res_partner SET create_date = %s WHERE id = %s", [moment, partner.id])
        partner.invalidate_recordset(['create_date'])
        return partner

    def test_due_moment_of_a_date_follows_the_daylight_saving_change(self):
        """Requirements: BR-SRR-0002. 1 day before 30 March 2026 (Brussels goes to summer time that night)."""
        rule = self._rule(offset=1, tz='Europe/Brussels')
        self.assertEqual(rule._compute_due_moment(date(2026, 3, 30)), datetime(2026, 3, 28, 23, 0))

    def test_due_moment_is_found_east_and_west_of_utc(self):
        """Requirements: FR-SRR-0004. The collection does not lose a date-only record in a far time zone."""
        field = self.env['ir.model.fields']._get('res.currency.rate', 'name')
        currency = self.env['res.currency'].create({'name': 'XQY', 'symbol': 'Y', 'active': True})
        template = self.env['mail.template'].create({
            'name': 'SRR edge tpl', 'model_id': self.env['ir.model']._get('res.currency.rate').id,
            'subject': 'x', 'body_html': '<p>x</p>'})
        for zone in ('Pacific/Kiritimati', 'Pacific/Pago_Pago'):
            with freeze_time('2026-03-01 08:00:00'):
                rule = self._rule(
                    model_id=field.model_id.id, date_field_id=field.id, tz=zone, channel='email',
                    mail_template_id=template.id, activity_type_id=False,
                    domain="[('currency_id', '=', %d)]" % currency.id)
            rate = self.env['res.currency.rate'].create(
                {'name': date(2026, 3, 20), 'currency_id': currency.id, 'rate': 1.0})
            due = rule._compute_due_moment(date(2026, 3, 20))
            self.assertEqual(rule._collect_due(due)[0][0], rate)
            rate.unlink()

    def test_exhausted_reminders_are_collected_only_when_asked(self):
        """Requirements: BR-SRR-0004. Run now (a manager) still tries a reminder that failed 3 times."""
        with freeze_time('2026-03-01 08:00:00'):
            rule = self._rule(recipient_mode='field',
                              recipient_field_id=self.env['ir.model.fields']._get('res.partner', 'user_id').id,
                              user_ids=[(5, 0, 0)])
        partner = self._partner(datetime(2026, 3, 10))
        now = datetime(2026, 3, 10, 9, 0)
        for _ in range(2):
            rule._send_one(partner, '2026-03-10 00:00:00', date(2026, 3, 10))
        self.assertEqual(len(rule._collect_due(now)), 1)
        rule._send_one(partner, '2026-03-10 00:00:00', date(2026, 3, 10))
        self.assertFalse(rule._collect_due(now))
        self.assertEqual(len(rule._collect_due(now, include_exhausted=True)), 1)

    def test_record_with_an_empty_date_is_left_out(self):
        """Requirements: FR-SRR-0004. Own version of SC-SRR-0004-3 with the date emptied by SQL NULL."""
        with freeze_time('2026-03-01 08:00:00'):
            rule = self._rule()
        self._partner(None)
        self.assertFalse(rule._collect_due(datetime(2026, 3, 10, 9, 0)))

    def test_bug_b_0168_date_field_reminder_date_is_the_record_date(self):
        """Regression test for B-0168: 3 days before a date of 20 March, the history says 20 March.

        Runs the scheduled job on a date field; the due moment (17 March) is not the reminder date.
        An email rule is used: currency rates hold no activities.

        Requirements: BR-SRR-0001, FR-SRR-0011
        """
        field = self.env['ir.model.fields']._get('res.currency.rate', 'name')
        currency = self.env['res.currency'].create({'name': 'XQZ', 'symbol': 'Z', 'active': True})
        template = self.env['mail.template'].create({
            'name': 'SRR b0168 tpl', 'model_id': field.model_id.id, 'subject': 'x', 'body_html': '<p>x</p>'})
        with freeze_time('2026-03-01 08:00:00'):
            rule = self._rule(
                model_id=field.model_id.id, date_field_id=field.id, tz='Europe/Brussels', offset=3,
                channel='email', mail_template_id=template.id, activity_type_id=False,
                domain="[('currency_id', '=', %d)]" % currency.id)
        self.env['res.currency.rate'].create({'name': date(2026, 3, 20), 'currency_id': currency.id, 'rate': 1.0})
        with freeze_time('2026-03-18 09:00:00'):
            self.env['record.reminder.rule']._cron_send_reminders()
        self.assertEqual(rule.execution_ids.mapped('reminder_date'), [date(2026, 3, 20)])

    def test_bug_b_0168_datetime_field_reminder_date_is_the_day_of_the_value(self):
        """Regression test for B-0168: 1 day before a value of 10 March 14:00, the reminder date is 10 March.

        Requirements: BR-SRR-0001, FR-SRR-0009, FR-SRR-0011
        """
        with freeze_time('2026-03-01 08:00:00'):
            rule = self._rule(offset=1, tz='Europe/Brussels')
        partner = self._partner(datetime(2026, 3, 10, 14, 0))
        due_reminders = rule._collect_due(datetime(2026, 3, 10, 15, 0))
        self.assertEqual(due_reminders, [(partner, '2026-03-10 14:00:00', date(2026, 3, 10))])

    def test_bug_b_0168_run_now_uses_the_record_date(self):
        """Regression test for B-0168: Run now sends the record date, also as the activity deadline.

        Requirements: BR-SRR-0001, FR-SRR-0014
        """
        with freeze_time('2026-03-01 08:00:00'):
            rule = self._rule(offset=1, tz='UTC')
        partner = self._partner(datetime(2026, 3, 10, 14, 0))
        with freeze_time('2026-03-11 15:00:00'):
            rule.with_user(self.env.ref('base.user_admin')).action_run_now()
        self.assertEqual(rule.execution_ids.reminder_date, date(2026, 3, 10))
        activities = self.env['mail.activity'].search([('res_model', '=', 'res.partner'), ('res_id', '=', partner.id)])
        self.assertEqual(set(activities.mapped('date_deadline')), {date(2026, 3, 10)})
