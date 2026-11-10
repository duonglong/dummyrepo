from datetime import date, datetime

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
