"""Regression tests of the bug fixes of record_change_tracker."""
import statistics
import time

from odoo.tests.common import TransactionCase, new_test_user
from odoo.tools import format_amount


class TestBugfixes(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login='rct_bugfix_user',
            groups='base.group_user,sales_team.group_sale_manager,purchase.group_purchase_manager')
        cls.partner = cls.env['res.partner'].create({'name': 'Bugfix partner'})
        cls.product = cls.env['product.product'].create({'name': 'Bugfix product', 'list_price': 10.0, 'standard_price': 10.0})

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


    def test_bug_b_0097_total_recorded_when_line_changes_outside_order_write(self):
        """Regression test for B-0097: the order total is recorded when a line changes without an order write.

        Requirements: FR-RCT-001, FR-RCT-009
        """
        for model in ('sale.order', 'purchase.order'):
            order = self.env[model].with_user(self.user).create({'partner_id': self.partner.id})
            order._update_order_line_info(self.product, 2, self.product.uom_id, 'order_line')
            self.assertEqual(
                len(self._entries(model, order, 'amount_total')), 1,
                "%s: one Total entry after adding a product through the catalog" % model)
            order._update_order_line_info(self.product, 5, self.product.uom_id, 'order_line')
            self.assertEqual(
                len(self._entries(model, order, 'amount_total')), 2,
                "%s: a second Total entry after changing the catalog quantity" % model)
            order._update_order_line_info(self.product, 0, self.product.uom_id, 'order_line')
            self.assertEqual(
                len(self._entries(model, order, 'amount_total')), 3,
                "%s: a third Total entry after removing the line through the catalog" % model)

    def test_bug_b_0098_creating_order_with_lines_records_nothing(self):
        """Regression test for B-0098: creating an order with lines leaves no entry.

        Requirements: FR-RCT-001, FR-RCT-009
        """
        line = {'product_id': self.product.id, 'product_qty': 1, 'price_unit': 10.0}
        for model, line_vals in (
                ('sale.order', {'product_id': self.product.id, 'product_uom_qty': 1}),
                ('purchase.order', line)):
            order = self.env[model].with_user(self.user).create({
                'partner_id': self.partner.id, 'order_line': [(0, 0, line_vals)]})
            self.assertFalse(
                self.env['record.change.entry'].search([
                    ('res_model', '=', model), ('res_id', '=', order.id)]),
                "%s: creation records no entry" % model)
    def _order_and_line_vals(self, model):
        order = self.env[model].with_user(self.user).create({'partner_id': self.partner.id})
        if model == 'sale.order':
            vals = {'order_id': order.id, 'product_id': self.product.id, 'product_uom_qty': 1}
        else:
            vals = {'order_id': order.id, 'product_id': self.product.id, 'product_qty': 1, 'price_unit': 10.0}
        return order, vals

    def test_bug_b_0099_batch_line_create_records_one_total_entry(self):
        """Regression test for B-0099 (R4-01): two lines of one order created in one call give one Total entry.

        Requirements: FR-RCT-001
        """
        for model in ('sale.order', 'purchase.order'):
            order, vals = self._order_and_line_vals(model)
            line_model = self.env[model + '.line'].with_user(self.user)
            line_model.create([dict(vals), dict(vals)])
            self.assertEqual(
                len(self._entries(model, order, 'amount_total')), 1,
                "%s: one Total entry for one change" % model)

    def test_bug_b_0099_moving_line_records_destination_total(self):
        """Regression test for B-0099 (R4-02): a line moved to another order records both orders' totals.

        Requirements: FR-RCT-001
        """
        for model in ('sale.order', 'purchase.order'):
            source, vals = self._order_and_line_vals(model)
            destination = self.env[model].with_user(self.user).create({'partner_id': self.partner.id})
            line = self.env[model + '.line'].with_user(self.user).create(vals)
            line.write({'order_id': destination.id})
            self.assertEqual(len(self._entries(model, source, 'amount_total')), 2, "%s: source" % model)
            self.assertEqual(len(self._entries(model, destination, 'amount_total')), 1, "%s: destination" % model)


class TestPerformance(TransactionCase):

    RUNS = 15

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(
            cls.env, login='rct_perf_user', groups='base.group_user,sales_team.group_sale_manager')
        cls.rtype = cls.env['record.change.model'].search([('model_id.model', '=', 'sale.order')])
        cls.partner = cls.env['res.partner'].create({'name': 'Perf partner'})
        cls.order = cls.env['sale.order'].with_user(cls.user).create({'partner_id': cls.partner.id})

    def _median(self, action):
        times = []
        for _i in range(self.RUNS):
            start = time.perf_counter()
            action()
            self.env.flush_all()
            times.append(time.perf_counter() - start)
        return statistics.median(times)

    def _added_time(self, make_action):
        """Median time of the action with the record type followed minus not followed."""
        self.rtype.is_followed = False
        self.env.flush_all()
        off = self._median(make_action())
        self.rtype.is_followed = True
        self.env.flush_all()
        on = self._median(make_action())
        return on - off

    def test_nfr_perf_002_open_adds_under_200ms(self):
        """NFR-PERF-002: opening a followed record adds at most 200 ms.

        Requirements: NFR-PERF-002
        """
        order = self.order.with_user(self.user)
        spec = {'name': {}, 'change_entry_ids': {'fields': {'field_name': {}, 'new_value': {}}}}
        added = self._added_time(lambda: (lambda: order.web_read(spec)))
        self.assertLess(added, 0.2, "opening added %.3f s" % added)

    def test_nfr_perf_002_save_adds_under_200ms(self):
        """NFR-PERF-002: saving a followed record adds at most 200 ms.

        Requirements: NFR-PERF-002
        """
        order = self.order.with_user(self.user)
        counter = iter(range(10 ** 6))
        added = self._added_time(lambda: (lambda: order.write({'note': 'n%d' % next(counter)})))
        self.assertLess(added, 0.2, "saving added %.3f s" % added)
