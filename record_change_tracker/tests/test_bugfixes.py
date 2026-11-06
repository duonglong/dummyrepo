"""Regression tests of the bug fixes of record_change_tracker."""
from odoo.tests.common import TransactionCase, new_test_user


class TestBugfixes(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login='rct_bugfix_user',
            groups='base.group_user,sales_team.group_sale_manager,purchase.group_purchase_manager')
        cls.partner = cls.env['res.partner'].create({'name': 'Bugfix partner'})
        cls.product = cls.env['product.product'].create({'name': 'Bugfix product', 'list_price': 10.0})

    def _entries(self, model, record, field_name):
        return self.env['record.change.entry'].search([
            ('res_model', '=', model), ('res_id', '=', record.id), ('field_name', '=', field_name)])

    def test_bug_b_0094_stored_computed_total_recorded(self):
        """Regression test for B-0094: a stored computed followed field (amount_total) is recorded.

        Requirements: FR-RCT-001, FR-RCT-009
        """
        order = self.env['sale.order'].with_user(self.user).create({'partner_id': self.partner.id})
        order.write({'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 2})]})
        entries = self._entries('sale.order', order, 'amount_total')
        self.assertEqual(len(entries), 1, "one Total entry after adding a line")

    def test_bug_b_0095_datetime_shown_in_user_time_zone(self):
        """Regression test for B-0095: date-times are stored in the saving user's time zone.

        Requirements: FR-RCT-003, FR-RCT-009
        """
        self.user.tz = 'Europe/Brussels'
        order = self.env['purchase.order'].with_user(self.user).create({'partner_id': self.partner.id})
        order.write({'date_planned': '2026-07-05 08:00:00'})
        entry = self._entries('purchase.order', order, 'date_planned')
        self.assertEqual(len(entry), 1)
        self.assertIn('10:00', entry.new_value)
        self.assertNotIn('08:00', entry.new_value)
