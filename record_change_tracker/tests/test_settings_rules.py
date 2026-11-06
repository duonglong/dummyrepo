"""Own tests of T-001: mixin fields refused, sensitive wins over followed."""
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger


class TestSettingsRules(TransactionCase):

    def _setting(self, model, fname, **vals):
        rtype = self.env['record.change.model'].search([('model_id.model', '=', model)])
        field = self.env['ir.model.fields']._get(model, fname)
        return self.env['record.change.field'].create(
            dict(tracked_model_id=rtype.id, field_id=field.id, **vals))

    def test_mixin_fields_refused(self):
        """Requirements: FR-RCT-010, BR-RCT-003. Stored mixin fields are technical."""
        for model, fname in (('res.partner', 'message_bounce'),
                             ('sale.order', 'access_token')):
            with self.subTest(field=fname), mute_logger('odoo.sql_db'), self.assertRaises(ValidationError):
                self._setting(model, fname)

    def test_sensitive_wins_over_followed(self):
        """Requirements: BR-RCT-004, FR-RCT-011."""
        line = self._setting('res.partner', 'comment', is_followed=True, is_sensitive=True)
        self.assertFalse(line.is_tracked)
        line.is_sensitive = False
        self.assertTrue(line.is_tracked)
