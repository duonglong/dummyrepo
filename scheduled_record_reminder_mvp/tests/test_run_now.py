from odoo.exceptions import UserError
from odoo.tests import TransactionCase


class TestRunNow(TransactionCase):
    """Requirements: FR-SRR-0014. Run now refuses a switched-off rule."""

    def test_run_now_refuses_a_switched_off_rule(self):
        """The Run now button has no field or dialog, so the method is called directly."""
        rule = self.env['record.reminder.rule'].create({
            'name': 'Off rule',
            'model_id': self.env['ir.model']._get('res.partner').id,
            'date_field_id': self.env['ir.model.fields']._get('res.partner', 'create_date').id,
            'tz': 'UTC', 'channel': 'activity',
            'activity_type_id': self.env.ref('mail.mail_activity_data_todo').id,
            'recipient_mode': 'users', 'user_ids': [(6, 0, [self.env.ref('base.user_admin').id])],
            'active': False,
        })
        with self.assertRaises(UserError):
            rule.action_run_now()
