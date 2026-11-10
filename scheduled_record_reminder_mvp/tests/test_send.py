from datetime import date, datetime
from unittest.mock import patch

from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import TransactionCase


class TestRuleWithHistory(TransactionCase):
    """A rule that already sent or tried a reminder cannot be deleted."""

    def setUp(self):
        super().setUp()
        partner_model = self.env['ir.model']._get('res.partner')
        self.rule = self.env['record.reminder.rule'].create({
            'name': 'SRR delete test',
            'model_id': partner_model.id,
            'date_field_id': self.env['ir.model.fields']._get('res.partner', 'create_date').id,
            'channel': 'activity',
            'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
            'recipient_mode': 'users',
            'user_ids': [(6, 0, [self.env.ref('base.user_admin').id])],
        })

    def test_rule_without_history_can_be_deleted(self):
        """Requirements: FR-SRR-0017. A rule with no history is deleted."""
        self.rule.unlink()
        self.assertFalse(self.rule.exists())

    def test_rule_with_history_cannot_be_deleted(self):
        """Requirements: FR-SRR-0017. The history is kept: switch the rule off instead."""
        partner = self.env['res.partner'].create({'name': 'SRR-keep'})
        self.rule._send_one(partner, '2026-03-20 09:00:00', datetime(2026, 3, 20).date())
        with self.assertRaises(UserError):
            self.rule.unlink()

    def test_removing_the_watched_app_removes_the_rule_with_its_history(self):
        """Requirements: FR-SRR-0017. Removing the watched app deletes the rule in the database; its history goes too.

        The record type's removal reaches the rule through the database's cascade, not through the rule's delete.
        """
        partner = self.env['res.partner'].create({'name': 'SRR-uninstall'})
        line = self.rule._send_one(partner, '2026-03-20 09:00:00', date(2026, 3, 20))
        self.env.flush_all()
        self.env.cr.execute("DELETE FROM record_reminder_rule WHERE id = %s", [self.rule.id])
        self.env.invalidate_all()
        self.assertFalse(line.exists())


class TestSendOneEdgeCases(TransactionCase):
    """Sending one reminder when the record or the recipients are unusual."""

    def setUp(self):
        super().setUp()
        self.ann = self.env['res.users'].create({'name': 'SRR Ann', 'login': 'srr_edge_ann'})
        self.rule = self.env['record.reminder.rule'].create({
            'name': 'SRR edge test',
            'model_id': self.env['ir.model']._get('res.partner').id,
            'date_field_id': self.env['ir.model.fields']._get('res.partner', 'create_date').id,
            'channel': 'activity',
            'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
            'recipient_mode': 'users',
            'user_ids': [(6, 0, [self.ann.id])],
        })

    def test_deleted_record_becomes_a_failed_line(self):
        """Requirements: FR-SRR-0012. A record deleted before sending is a failure, not a stop."""
        partner = self.env['res.partner'].create({'name': 'SRR-deleted'})
        partner.unlink()
        line = self.rule._send_one(partner, '2026-03-20 09:00:00', date(2026, 3, 20))
        self.assertEqual(line.state, 'failed')
        self.assertEqual(line.error_message, "The record no longer exists.")
        self.assertEqual(line.attempt, 1)

    def test_activity_goes_to_the_listed_users_only(self):
        """Requirements: FR-SRR-0007, FR-SRR-0009. A user sharing a listed user's contact gets nothing."""
        other = self.env['res.users'].create({
            'name': 'SRR Other', 'login': 'srr_edge_other', 'partner_id': self.ann.partner_id.id})
        partner = self.env['res.partner'].create({'name': 'SRR-exact'})
        self.rule._send_one(partner, '2026-03-20 09:00:00', date(2026, 3, 20))
        activities = self.env['mail.activity'].search([
            ('res_model', '=', 'res.partner'), ('res_id', '=', partner.id)])
        self.assertEqual(activities.user_id, self.ann)
        self.assertNotIn(other, activities.user_id)

    def test_failed_tries_are_counted(self):
        """Requirements: FR-SRR-0012. Each failed try of a key gets the next attempt number."""
        self.ann.active = False
        partner = self.env['res.partner'].create({'name': 'SRR-retry'})
        first = self.rule._send_one(partner, '2026-03-20 09:00:00', date(2026, 3, 20))
        second = self.rule._send_one(partner, '2026-03-20 09:00:00', date(2026, 3, 20))
        self.assertEqual((first.attempt, second.attempt), (1, 2))

    def test_bug_b_0169_a_record_whose_name_cannot_be_read_is_a_failed_line(self):
        """Regression test for B-0169: one record with a broken display name fails alone, the run goes on.

        Requirements: FR-SRR-0012, FR-SRR-0010
        """
        broken, good = self.env['res.partner'].create([{'name': 'SRR-broken'}, {'name': 'SRR-good'}])
        broken, good = broken.with_prefetch(broken.ids), good.with_prefetch(good.ids)  # no shared compute batch
        compute = self.env.registry['res.partner']._compute_display_name

        def failing_for_broken(partners):
            if broken in partners:
                raise ValueError("display name unreadable")
            compute(partners)

        self.env.invalidate_all()
        with patch.object(self.env.registry['res.partner'], '_compute_display_name', failing_for_broken):
            lines = [self.rule._send_one(partner, '2026-03-20 09:00:00', date(2026, 3, 20))
                     for partner in (broken, good)]
        self.assertEqual([line.state for line in lines], ['failed', 'sent'])
        self.assertIn("display name unreadable", lines[0].error_message)
        self.assertEqual(lines[0].record_name, 'res.partner,%s' % broken.id)


class TestEmailToUnreachableRecipients(TransactionCase):
    """An email reminder whose recipients have no email address is a failure, never a send.

    Native mail only marks such an email as an exception and raises nothing, so
    the rule must check the addresses itself, for each way it finds recipients.
    """

    def setUp(self):
        super().setUp()
        self.no_mail_user = self.env['res.users'].create({'name': 'SRR Nomail', 'login': 'srr_nomail'})
        self.no_mail_user.partner_id.email = False
        self.template = self.env['mail.template'].create({
            'name': 'SRR unreachable', 'model_id': self.env['ir.model']._get('res.partner').id,
            'subject': 'Reminder', 'body_html': '<p>Reminder</p>',
        })
        self.rule = self.env['record.reminder.rule'].create({
            'name': 'SRR unreachable test',
            'model_id': self.env['ir.model']._get('res.partner').id,
            'date_field_id': self.env['ir.model.fields']._get('res.partner', 'create_date').id,
            'channel': 'email',
            'mail_template_id': self.template.id,
            'recipient_mode': 'users',
            'user_ids': [Command.set(self.no_mail_user.ids)],
        })

    def _send(self, record):
        return self.rule._send_one(record, '2026-03-20 09:00:00', date(2026, 3, 20))

    def assertFailedForNoAddress(self, line, contact):
        self.assertEqual(line.state, 'failed')
        self.assertIn("no email address", line.error_message)
        self.assertIn(contact.name, line.error_message)

    def test_bug_b_0170_fixed_user_without_email_is_a_failed_line(self):
        """Regression test for B-0170: fixed users whose contacts have no email address.

        Requirements: FR-SRR-0007, FR-SRR-0008, FR-SRR-0012
        """
        line = self._send(self.env['res.partner'].create({'name': 'SRR-fixed'}))
        self.assertFailedForNoAddress(line, self.no_mail_user.partner_id)

    def test_bug_b_0170_user_field_without_email_is_a_failed_line(self):
        """Regression test for B-0170: a user field holding a user whose contact has no email address.

        Requirements: FR-SRR-0006, FR-SRR-0008, FR-SRR-0012
        """
        self.rule.write({
            'recipient_mode': 'field',
            'recipient_field_id': self.env['ir.model.fields']._get('res.partner', 'user_id').id,
        })
        record = self.env['res.partner'].create({'name': 'SRR-userfield', 'user_id': self.no_mail_user.id})
        self.assertFailedForNoAddress(self._send(record), self.no_mail_user.partner_id)

    def test_bug_b_0170_contact_field_without_email_is_a_failed_line(self):
        """Regression test for B-0170: a contact field holding a contact with no email address.

        Requirements: FR-SRR-0006, FR-SRR-0008, FR-SRR-0012
        """
        self.rule.write({
            'recipient_mode': 'field',
            'recipient_field_id': self.env['ir.model.fields']._get('res.partner', 'parent_id').id,
        })
        contact = self.env['res.partner'].create({'name': 'SRR Company without mail'})
        record = self.env['res.partner'].create({'name': 'SRR-contactfield', 'parent_id': contact.id})
        self.assertFailedForNoAddress(self._send(record), contact)

    def test_bug_b_0170_unreachable_reminder_is_tried_again(self):
        """Regression test for B-0170: the failure blocks no later try; once an address is set it is sent.

        Requirements: FR-SRR-0013, BR-SRR-0004
        """
        record = self.env['res.partner'].create({'name': 'SRR-retry-mail'})
        first = self._send(record)
        self.no_mail_user.partner_id.email = 'nomail@example.com'
        second = self._send(record)
        self.assertEqual((first.state, second.state), ('failed', 'sent'))
        self.assertEqual(second.attempt, 2)


class TestRemovedRecipientField(TransactionCase):
    """A rule in field mode whose recipient field was removed fails for the reason, for each channel."""

    def setUp(self):
        super().setUp()
        partner_model = self.env['ir.model']._get('res.partner')
        self.reviewer_field = self.env['ir.model.fields'].create({
            'name': 'x_srr_reviewer_id', 'field_description': 'SRR reviewer', 'model_id': partner_model.id,
            'ttype': 'many2one', 'relation': 'res.users', 'state': 'manual',
        })
        self.template = self.env['mail.template'].create({
            'name': 'SRR removed field', 'model_id': partner_model.id,
            'subject': 'Reminder', 'body_html': '<p>Reminder</p>',
        })
        self.customer = self.env['res.partner'].create({'name': 'SRR Customer X', 'email': 'srr.customer@example.com'})

    def _rule_without_recipient_field(self, **channel_values):
        rule = self.env['record.reminder.rule'].create(dict({
            'name': 'SRR removed field test',
            'model_id': self.env['ir.model']._get('res.partner').id,
            'date_field_id': self.env['ir.model.fields']._get('res.partner', 'create_date').id,
            'recipient_mode': 'field',
            'recipient_field_id': self.reviewer_field.id,
        }, **channel_values))
        self.reviewer_field.unlink()
        return rule

    def _send(self, rule):
        return rule._send_one(self.customer, '2026-03-20 09:00:00', date(2026, 3, 20))

    def assertFailedForRemovedField(self, line):
        self.assertEqual(line.state, 'failed')
        self.assertIn("recipient field no longer exists", line.error_message)
        self.assertFalse(line.partner_ids)

    def test_bug_b_0175_activity_rule_fails_instead_of_recording_a_send(self):
        """Regression test for B-0175: the watched record is not its own recipient; no activity means a failed line.

        Requirements: FR-SRR-0006, FR-SRR-0009, FR-SRR-0012
        """
        rule = self._rule_without_recipient_field(
            channel='activity', activity_type_id=self.env.ref('mail.mail_activity_data_todo').id)
        self.assertFailedForRemovedField(self._send(rule))
        self.assertFalse(self.env['mail.activity'].search([
            ('res_model', '=', 'res.partner'), ('res_id', '=', self.customer.id)]))

    def test_bug_b_0175_email_rule_does_not_mail_the_watched_record(self):
        """Regression test for B-0175: the internal reminder is not emailed to the customer.

        Requirements: FR-SRR-0006, FR-SRR-0008, FR-SRR-0012
        """
        rule = self._rule_without_recipient_field(channel='email', mail_template_id=self.template.id)
        self.assertFailedForRemovedField(self._send(rule))
        self.assertFalse(self.env['mail.mail'].search([('recipient_ids', 'in', self.customer.ids)]))
