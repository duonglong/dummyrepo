from odoo.exceptions import UserError
from odoo.tests import TransactionCase


class TestHistoryViews(TransactionCase):
    """Requirements: FR-SRR-0015. Opening the record of a history line."""

    def _line(self, partner_id):
        rule = self.env['record.reminder.rule'].create({
            'name': 'R',
            'model_id': self.env['ir.model']._get('res.partner').id,
            'date_field_id': self.env['ir.model.fields']._get('res.partner', 'create_date').id,
            'tz': 'UTC', 'channel': 'activity',
            'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
            'recipient_mode': 'users', 'user_ids': [(6, 0, [self.env.ref('base.user_admin').id])],
        })
        return self.env['record.reminder.execution'].create({
            'rule_id': rule.id, 'res_model': 'res.partner', 'res_id': partner_id,
            'reminder_key': 'k', 'channel': 'activity', 'state': 'sent'})

    def test_open_record_returns_the_record_form(self):
        partner = self.env['res.partner'].create({'name': 'Target'})
        action = self._line(partner.id).action_open_record()
        self.assertEqual((action['res_model'], action['res_id']), ('res.partner', partner.id))

    def test_open_record_of_a_deleted_record_is_refused(self):
        partner = self.env['res.partner'].create({'name': 'Gone'})
        line = self._line(partner.id)
        partner.unlink()
        with self.assertRaises(UserError):
            line.action_open_record()

    def test_smart_buttons_open_the_rule_history_by_state(self):
        line = self._line(self.env.user.partner_id.id)
        action = line.rule_id.action_view_sent()
        self.assertEqual(self.env['record.reminder.execution'].search(action['domain']), line)
        failed = line.rule_id.action_view_failed()
        self.assertFalse(self.env['record.reminder.execution'].search(failed['domain']))
