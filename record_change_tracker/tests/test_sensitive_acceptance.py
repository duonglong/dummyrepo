"""Acceptance tests of T-006 (rework of T-002): sensitive fields are not recorded.

Written from the spec only. Requirements: FR-RCT-012.
"""
from odoo.tests.common import TransactionCase, new_test_user


class TestSensitiveAcceptance(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = ('base.group_user,base.group_partner_manager,'
                  'sales_team.group_sale_manager,purchase.group_purchase_manager')
        cls.user = new_test_user(cls.env, login='rct_sens_user', name='Sens User', groups=groups)

    def _mark(self, model, fname, followed=True, sensitive=False):
        rtype = self.env['record.change.model'].sudo().search([('model_id.model', '=', model)])
        ir_field = self.env['ir.model.fields']._get(model, fname)
        line = rtype.field_ids.filtered(lambda l: l.field_id == ir_field)
        vals = {'is_followed': followed, 'is_sensitive': sensitive}
        if line:
            line.sudo().write(vals)
        else:
            self.env['record.change.field'].sudo().create(
                dict(vals, tracked_model_id=rtype.id, field_id=ir_field.id))
        self.env.flush_all()

    def _entries(self, record, fname):
        self.env.flush_all()
        return self.env['record.change.entry'].sudo().search([
            ('res_model', '=', record._name), ('res_id', '=', record.id), ('field_name', '=', fname)])

    def _log_text(self, record):
        """Everything the message log keeps of the record (Odoo 20 keeps tracked values in the body)."""
        self.env.flush_all()
        self.env.cr.precommit.run()  # the native tracking is written in a precommit hook
        self.env.flush_all()
        messages = self.env['mail.message'].sudo().search([
            ('model', '=', record._name), ('res_id', '=', record.id)])
        parts = [str(b) for b in messages.mapped('body') if b]
        tracking = self.env.get('mail.tracking.value')
        if tracking is not None:
            for val in tracking.sudo().search([('mail_message_id', 'in', messages.ids)]):
                parts += [str(v) for v in (val.old_value_char, val.new_value_char) if v]
        return ' '.join(parts)

    def test_001_email_sensitive_leaves_no_message_log_value_and_no_entry(self):
        """Requirements: FR-RCT-012. Scenario SC-RCT-001."""
        self._mark('res.partner', 'email', followed=True, sensitive=True)
        partner = self.env['res.partner'].create({'name': 'Azure', 'email': 'old.secret@example.com'})
        partner.with_user(self.user).write({'email': 'new.secret@example.com'})
        text = self._log_text(partner)
        self.assertNotIn('old.secret@example.com', text)
        self.assertNotIn('new.secret@example.com', text)
        self.assertFalse(self._entries(partner, 'email'))

    def test_012_message_log_keeps_no_value_of_sensitive_native_field(self):
        """Requirements: FR-RCT-012. A natively logged field (order state), followed and sensitive, leaves
        no old or new value in the message log, and no entry."""
        self._mark('sale.order', 'state', followed=True, sensitive=True)
        customer = self.env['res.partner'].create({'name': 'Customer'})
        order = self.env['sale.order'].create({'partner_id': customer.id})
        order.with_user(self.user).write({'state': 'sent'})
        self.assertNotIn('Quotation Sent', self._log_text(order))
        self.assertFalse(self._entries(order, 'state'))

    def test_012_1_sensitive_followed_vat_leaves_no_entry(self):
        """Requirements: FR-RCT-012. Scenario SC-RCT-012-1."""
        self._mark('res.partner', 'vat', followed=True, sensitive=True)
        partner = self.env['res.partner'].create({'name': 'Azure'})
        partner.with_user(self.user).write({'vat': 'BE0123456749'})
        self.assertFalse(self._entries(partner, 'vat'))

    def test_012_2_unmarked_vat_is_recorded_again(self):
        """Requirements: FR-RCT-012. Scenario SC-RCT-012-2."""
        self._mark('res.partner', 'vat', followed=True, sensitive=True)
        self._mark('res.partner', 'vat', followed=True, sensitive=False)
        partner = self.env['res.partner'].create({'name': 'Azure'})
        partner.with_user(self.user).write({'vat': 'BE0123456749'})
        entries = self._entries(partner, 'vat')
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries.new_value, 'BE0123456749')
