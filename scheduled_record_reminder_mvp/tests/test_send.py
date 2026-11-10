from datetime import datetime

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
        """Requirements: FR-SRR-0011. A rule with no history is deleted."""
        self.rule.unlink()
        self.assertFalse(self.rule.exists())

    def test_rule_with_history_cannot_be_deleted(self):
        """Requirements: FR-SRR-0011. The history is kept: switch the rule off instead."""
        partner = self.env['res.partner'].create({'name': 'SRR-keep'})
        self.rule._send_one(partner, '2026-03-20 09:00:00', datetime(2026, 3, 20).date())
        with self.assertRaises(UserError):
            self.rule.unlink()
