"""Own tests of T-002: sensitive fields leave the chatter, rich text and blank handling."""
from odoo.tests.common import TransactionCase


class TestEntriesRules(TransactionCase):

    def _line(self, model, fname, **vals):
        rtype = self.env['record.change.model'].search([('model_id.model', '=', model)])
        ir_field = self.env['ir.model.fields']._get(model, fname)
        line = rtype.field_ids.filtered(lambda l: l.field_id == ir_field)
        if line:
            line.write(vals)
        else:
            self.env['record.change.field'].create(
                dict(vals, tracked_model_id=rtype.id, field_id=ir_field.id))
        self.env.flush_all()

    def test_sensitive_field_leaves_chatter_tracking(self):
        """Requirements: FR-RCT-012. A sensitive field is dropped from the native tracked set."""
        orders = self.env['sale.order']
        self.assertIn('state', orders._track_get_fields())
        self._line('sale.order', 'state', is_followed=True, is_sensitive=True)
        self.assertNotIn('state', orders._track_get_fields())

    def test_rich_text_recorded(self):
        """Requirements: FR-RCT-001. A followed rich-text field is recorded."""
        self._line('res.partner', 'comment', is_followed=True)
        partner = self.env['res.partner'].create({'name': 'P'})
        partner.write({'comment': '<p>hi</p>'})
        entry = self.env['record.change.entry'].search([
            ('res_model', '=', 'res.partner'), ('res_id', '=', partner.id),
            ('field_name', '=', 'comment')])
        self.assertEqual(len(entry), 1)
        self.assertFalse(entry.old_value)
