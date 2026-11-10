from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import TransactionCase
from odoo.tools import mute_logger


class TestRemovalKeepsHistory(TransactionCase):
    """Removing a custom date field or record type is refused while a rule using it has history.

    Requirements: FR-SRR-0017, FR-SRR-0010, FR-SRR-0011.
    """

    def setUp(self):
        super().setUp()
        self.item_model = self.env['ir.model'].create({
            'name': 'SRR Item', 'model': 'x_srr_item', 'is_mail_activity': True,
            'field_id': [
                Command.create({'name': 'x_name', 'ttype': 'char', 'field_description': 'Name'}),
                Command.create({'name': 'x_due', 'ttype': 'date', 'field_description': 'Due'}),
            ],
        })
        self.due_field = self.env['ir.model.fields']._get('x_srr_item', 'x_due')
        self.rule = self.env['record.reminder.rule'].create({
            'name': 'SRR custom model rule',
            'model_id': self.item_model.id,
            'date_field_id': self.due_field.id,
            'channel': 'activity',
            'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
            'recipient_mode': 'users',
            'user_ids': [Command.set([self.env.ref('base.user_admin').id])],
        })

    def _add_history(self):
        return self.env['record.reminder.execution'].create({
            'rule_id': self.rule.id, 'res_model': 'x_srr_item', 'res_id': 1,
            'reminder_key': '2026-03-20', 'channel': 'activity', 'state': 'sent',
        })

    def test_b_0172_deleting_the_date_field_of_a_rule_with_history_is_refused(self):
        """Regression test for B-0172 (R3-1): the field's removal must not cascade to the rule and its history.

        Requirements: FR-SRR-0017
        """
        line = self._add_history()
        with self.assertRaises(UserError), mute_logger('odoo.sql_db'):
            self.due_field.unlink()
        self.assertTrue(self.rule.exists())
        self.assertTrue(line.exists())

    def test_b_0172_deleting_the_record_type_of_a_rule_with_history_is_refused(self):
        """Regression test for B-0172 (R3-1): the record type's removal must not cascade to the rule and its history.

        Requirements: FR-SRR-0017
        """
        line = self._add_history()
        with self.assertRaises(UserError), mute_logger('odoo.sql_db'):
            self.item_model.unlink()
        self.assertTrue(self.rule.exists())
        self.assertTrue(line.exists())

    def test_deleting_the_date_field_of_a_rule_without_history_is_allowed(self):
        """Requirements: FR-SRR-0017. Nothing to keep: the rule goes with its field."""
        self.due_field.unlink()
        self.assertFalse(self.rule.exists())

    def test_uninstalling_removes_the_rule_with_its_history(self):
        """Requirements: FR-SRR-0017. Removing the watched app still deletes the rule with its history."""
        line = self._add_history()
        self.patch(self.registry, 'uninstalling_modules', {'some_watched_app'})
        self.item_model.unlink()
        self.assertFalse(self.rule.exists())
        self.assertFalse(line.exists())
