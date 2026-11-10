from datetime import date, datetime

from psycopg2 import IntegrityError

from odoo import fields
from odoo.tests import TransactionCase, freeze_time
from odoo.tools import mute_logger

MODULE = 'scheduled_record_reminder_mvp'


class TestSendReminderAcceptance(TransactionCase):
    """Acceptance tests of T-002: sending one reminder and the history.

    Requirements: BR-SRR-0001, FR-SRR-0006, FR-SRR-0007, FR-SRR-0008,
    FR-SRR-0009, FR-SRR-0010, FR-SRR-0011, FR-SRR-0012.

    The engine is called through the design's public method
    ``rule._send_one(record, reminder_key, reminder_date)``; no form or
    dialog is involved, the Run now button belongs to a later task.
    """

    def setUp(self):
        super().setUp()
        self.Rule = self.env['record.reminder.rule']
        self.Execution = self.env['record.reminder.execution']
        self.partner_model = self.env['ir.model']._get('res.partner')
        self.todo = self.env.ref('mail.mail_activity_data_todo')
        group = self.env.ref('base.group_user')
        self.ann = self._user('Ann', 'srr_ann', group)
        self.bob = self._user('Bob', 'srr_bob', group)
        self.carl = self._user('Carl', 'srr_carl', group)
        self.template = self.env['mail.template'].create({
            'name': 'SRR Contract ends soon',
            'model_id': self.partner_model.id,
            'subject': 'Contract ends {{ object.name }}',
            'body_html': '<p>Ends soon</p>',
            'auto_delete': False,
        })

    def _user(self, name, login, group):
        return self.env['res.users'].create({
            'name': name, 'login': login, 'group_ids': [(6, 0, [group.id])],
        })

    def _field(self, name, model='res.partner'):
        return self.env['ir.model.fields']._get(model, name)

    def _rule(self, **kw):
        vals = {
            'name': 'SRR test rule',
            'model_id': self.partner_model.id,
            'domain': "[('name', 'like', 'SRR-')]",
            'date_field_id': self._field('create_date').id,
            'offset': 0,
            'tz': 'UTC',
            'channel': 'activity',
            'activity_type_id': self.todo.id,
            'activity_summary': 'Call before renewal',
            'recipient_mode': 'users',
            'user_ids': [(6, 0, [self.ann.id])],
        }
        vals.update(kw)
        return self.Rule.create(vals)

    def _email_rule(self, **kw):
        vals = {'channel': 'email', 'mail_template_id': self.template.id,
                'activity_type_id': False}
        vals.update(kw)
        return self._rule(**vals)

    def _set_created(self, partner, moment):
        """Set the record's date (create_date, a date-and-time field) by SQL."""
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE res_partner SET create_date = %s WHERE id = %s", [moment, partner.id])
        partner.invalidate_recordset(['create_date'])

    def _partner(self, name, moment=datetime(2026, 3, 20, 9, 0), **kw):
        vals = {'name': 'SRR-' + name}
        vals.update(kw)
        partner = self.env['res.partner'].create(vals)
        self._set_created(partner, moment)
        return partner

    def _send(self, rule, partner, day=None):
        """Send through the engine: key = the date field's value as text."""
        value = partner.create_date
        key = fields.Datetime.to_string(value)
        return rule._send_one(partner, key, day or value.date())

    def _lines(self, rule, state=None):
        self.env.flush_all()
        domain = [('rule_id', '=', rule.id)]
        if state:
            domain.append(('state', '=', state))
        return self.Execution.search(domain)

    def _activities(self, partner):
        return self.env['mail.activity'].search([
            ('res_model', '=', 'res.partner'), ('res_id', '=', partner.id)])

    def _mails(self, partner):
        return self.env['mail.mail'].search([
            ('model', '=', 'res.partner'), ('res_id', '=', partner.id)])

    # --- FR-SRR-0006 ------------------------------------------------------
    def test_recipient_read_from_field_at_sending_time(self):
        """Requirements: FR-SRR-0006. Scenario SC-SRR-0006-1.

        The salesperson was changed from Ann to Bob before the send: Bob
        gets the activity and Ann does not.
        """
        rule = self._rule(
            recipient_mode='field', recipient_field_id=self._field('user_id').id,
            user_ids=[(5, 0, 0)])
        partner = self._partner('order', user_id=self.ann.id)
        partner.user_id = self.bob
        self._send(rule, partner)
        activities = self._activities(partner)
        self.assertEqual(activities.user_id, self.bob)
        self.assertEqual(len(activities), 1)
        self.assertEqual(self._lines(rule).state, 'sent')

    def test_no_recipient_is_a_failure_with_the_reason(self):
        """Requirements: FR-SRR-0006, FR-SRR-0012. Scenario SC-SRR-0006-2."""
        rule = self._rule(
            recipient_mode='field', recipient_field_id=self._field('user_id').id,
            user_ids=[(5, 0, 0)])
        partner = self._partner('order')
        self._send(rule, partner)
        self.assertFalse(self._activities(partner))
        line = self._lines(rule)
        self.assertEqual(len(line), 1)
        self.assertEqual(line.state, 'failed')
        self.assertIn('recipient', (line.error_message or '').lower())

    def test_email_recipient_taken_from_contact_field(self):
        """Requirements: FR-SRR-0006. For an email the field may hold contacts."""
        contact = self.env['res.partner'].create({
            'name': 'SRR-contact', 'email': 'contact@example.com'})
        rule = self._email_rule(
            recipient_mode='field', recipient_field_id=self._field('parent_id').id,
            user_ids=[(5, 0, 0)])
        partner = self._partner('child', parent_id=contact.id)
        self._send(rule, partner)
        self.assertEqual(self._lines(rule).state, 'sent')
        self.assertEqual(self._mails(partner).recipient_ids, contact)

    # --- FR-SRR-0007 ------------------------------------------------------
    def test_archived_fixed_user_is_left_out(self):
        """Requirements: FR-SRR-0007. Scenario SC-SRR-0007-1."""
        ann_partner = self.ann.partner_id
        carl_partner = self.carl.partner_id
        self.carl.active = False
        rule = self._email_rule(user_ids=[(6, 0, [self.ann.id, self.carl.id])])
        partner = self._partner('contract')
        self._send(rule, partner)
        recipients = self._mails(partner).recipient_ids
        self.assertIn(ann_partner, recipients)
        self.assertNotIn(carl_partner, recipients)
        self.assertEqual(self._lines(rule).partner_ids, ann_partner)

    # --- FR-SRR-0008 ------------------------------------------------------
    def test_email_is_rendered_from_the_template_for_the_record(self):
        """Requirements: FR-SRR-0008. Scenario SC-SRR-0008-1."""
        rule = self._email_rule()
        partner = self._partner('contract')
        self._send(rule, partner)
        mails = self._mails(partner)
        self.assertTrue(mails)
        self.assertIn('Contract ends SRR-contract', mails.mapped('subject'))
        self.assertIn(self.ann.partner_id, mails.recipient_ids)
        self.assertEqual(self._lines(rule).state, 'sent')

    # --- FR-SRR-0009 ------------------------------------------------------
    def test_activity_created_for_each_recipient_due_on_the_date(self):
        """Requirements: FR-SRR-0009. Scenario SC-SRR-0009-1."""
        rule = self._rule(user_ids=[(6, 0, [self.ann.id, self.bob.id])])
        partner = self._partner('renewal', datetime(2026, 3, 20, 9, 0))
        self._send(rule, partner)
        activities = self._activities(partner)
        self.assertEqual(len(activities), 2)
        self.assertEqual(activities.user_id, self.ann | self.bob)
        self.assertEqual(activities.activity_type_id, self.todo)
        self.assertEqual(set(activities.mapped('summary')), {'Call before renewal'})
        self.assertEqual(set(activities.mapped('date_deadline')), {date(2026, 3, 20)})

    def test_activity_deadline_is_the_day_in_the_rule_time_zone(self):
        """Requirements: FR-SRR-0009. A date-and-time reminder date gives a deadline
        on that day in the rule's time zone: 20 March 23:30 UTC is 21 March in Brussels.
        """
        rule = self._rule(tz='Europe/Brussels')
        partner = self._partner('late', datetime(2026, 3, 20, 23, 30))
        self._send(rule, partner, day=date(2026, 3, 21))
        self.assertEqual(self._activities(partner).date_deadline, date(2026, 3, 21))

    # --- FR-SRR-0010 / BR-SRR-0001 ---------------------------------------
    def test_same_record_rule_and_date_is_sent_once(self):
        """Requirements: FR-SRR-0010, BR-SRR-0001. Scenario SC-SRR-0010-1."""
        rule = self._rule()
        partner = self._partner('once')
        self._send(rule, partner)
        self._send(rule, partner)
        self.assertEqual(len(self._lines(rule, 'sent')), 1)
        self.assertEqual(len(self._activities(partner)), 1)

    def test_changed_date_is_a_new_reminder(self):
        """Requirements: FR-SRR-0010, BR-SRR-0001. Scenario SC-SRR-0010-2."""
        rule = self._rule()
        partner = self._partner('moved', datetime(2026, 3, 20, 9, 0))
        self._send(rule, partner)
        self._set_created(partner, datetime(2026, 4, 20, 9, 0))
        self._send(rule, partner)
        sent = self._lines(rule, 'sent')
        self.assertEqual(len(sent), 2)
        self.assertEqual(set(sent.mapped('reminder_date')),
                         {date(2026, 3, 20), date(2026, 4, 20)})
        self.assertEqual(len(self._activities(partner)), 2)

    def test_two_rules_on_one_record_each_send_their_own(self):
        """Requirements: BR-SRR-0001. Two rules on the same record both send."""
        rule1 = self._rule(name='SRR one')
        rule2 = self._rule(name='SRR two')
        partner = self._partner('twice')
        self._send(rule1, partner)
        self._send(rule2, partner)
        self.assertEqual(len(self._lines(rule1, 'sent')), 1)
        self.assertEqual(len(self._lines(rule2, 'sent')), 1)

    def test_database_refuses_a_second_sent_line_for_the_same_key(self):
        """Requirements: FR-SRR-0010. Scenario SC-SRR-0010-3.

        Overlapping runs are stopped by the database: a second sent line of
        the same rule, record and reminder date is refused, while failed
        lines of the same key may repeat (one per try).
        """
        rule = self._rule()
        partner = self._partner('race')
        vals = {
            'rule_id': rule.id, 'res_model': 'res.partner', 'res_id': partner.id,
            'reminder_key': '2026-03-20 09:00:00', 'channel': 'activity',
        }
        self.Execution.create(dict(vals, state='sent'))
        self.Execution.create(dict(vals, state='failed', error_message='x'))
        self.Execution.create(dict(vals, state='failed', error_message='y'))
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'):
            with self.cr.savepoint():
                self.Execution.create(dict(vals, state='sent'))
                self.env.flush_all()

    # --- FR-SRR-0011 ------------------------------------------------------
    def test_sent_reminder_is_recorded_in_the_history(self):
        """Requirements: FR-SRR-0011. Scenario SC-SRR-0011-1."""
        rule = self._email_rule()
        partner = self._partner('S00042')
        with freeze_time('2026-03-17 08:30:00'):
            self._send(rule, partner)
        line = self._lines(rule)
        self.assertEqual(len(line), 1)
        self.assertEqual(line.state, 'sent')
        self.assertEqual(line.rule_id, rule)
        self.assertEqual(line.res_model, 'res.partner')
        self.assertEqual(line.res_id, partner.id)
        self.assertEqual(line.record_name, 'SRR-S00042')
        self.assertEqual(line.reminder_date, date(2026, 3, 20))
        self.assertEqual(line.channel, 'email')
        self.assertEqual(line.partner_ids, self.ann.partner_id)
        self.assertEqual(line.executed_at, fields.Datetime.to_datetime('2026-03-17 08:30:00'))
        self.assertFalse(line.error_message)

    def test_history_line_keeps_the_record_name_after_deletion(self):
        """Requirements: FR-SRR-0011. The name is copied at sending time."""
        rule = self._rule()
        partner = self._partner('gone')
        self._send(rule, partner)
        partner.unlink()
        self.assertEqual(self._lines(rule).record_name, 'SRR-gone')

    # --- FR-SRR-0012 ------------------------------------------------------
    def test_failure_is_recorded_and_the_others_go_on(self):
        """Requirements: FR-SRR-0012. Scenario SC-SRR-0012-1.

        Rendering the template divides by the partner's colour: it fails for
        the second record only.
        """
        self.template.subject = '{{ 1 / object.color }}'
        rule = self._email_rule()
        first = self._partner('first', color=1)
        second = self._partner('second', color=0)
        third = self._partner('third', color=1)
        for partner in (first, second, third):
            self._send(rule, partner)
        self.assertTrue(self._mails(first))
        self.assertTrue(self._mails(third))
        self.assertFalse(self._mails(second))
        failed = self._lines(rule, 'failed')
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed.res_id, second.id)
        self.assertTrue(failed.error_message)
        self.assertEqual(failed.channel, 'email')
        self.assertEqual(failed.reminder_date, date(2026, 3, 20))
        self.assertEqual(set(self._lines(rule, 'sent').mapped('res_id')),
                         {first.id, third.id})

    def test_failed_reminder_is_tried_again_and_recorded_per_try(self):
        """Requirements: FR-SRR-0012, FR-SRR-0010. A failure is not a send."""
        rule = self._rule(
            recipient_mode='field', recipient_field_id=self._field('user_id').id,
            user_ids=[(5, 0, 0)])
        partner = self._partner('retry')
        self._send(rule, partner)
        partner.user_id = self.ann
        self._send(rule, partner)
        self.assertEqual(len(self._lines(rule, 'failed')), 1)
        self.assertEqual(len(self._lines(rule, 'sent')), 1)
        self.assertEqual(len(self._activities(partner)), 1)
