import json

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import IprCommon


@tagged('post_install', '-at_install')
class TestCheckAcceptance(IprCommon):
    """Acceptance tests of T-004: the three-way check of price and quantity.

    Requirements: FR-IPR-0001, FR-IPR-0003, FR-IPR-0004, FR-IPR-0005, FR-IPR-0006,
    FR-IPR-0007, FR-IPR-0010, FR-IPR-0020, BR-IPR-0001, BR-IPR-0004.
    The check is run by its method (action_ipr_check); the Run check button of the
    form is left to the build.
    """

    def _tolerance(self, **vals):
        vals.setdefault('company_id', self.company_a.id)
        return self.env['ipr.tolerance'].sudo().create(vals)

    def _check(self, bill):
        bill.with_user(self.accountant).action_ipr_check()
        self.env.flush_all()
        self.env.invalidate_all()
        return bill

    def _order_and_bill(self, bill_qty, bill_price, qty=100.0, price=10.0, received=100.0):
        order = self.make_order(qty, price, received=received)
        return order, self.make_bill_from_order(order, bill_qty, bill_price)

    def _exceptions(self, bill, kind):
        return bill.ipr_exception_ids.filtered(lambda e: e.kind == kind)

    # SC-IPR-0001-1
    def test_matched(self):
        """SC-IPR-0001-1: order 100 x 10.00 all received, bill 100 x 10.00: row shown, status Matched."""
        _order, bill = self._order_and_bill(100, 10.0)
        self._check(bill)
        self.assertEqual(len(bill.ipr_check_line_ids), 1)
        line = bill.ipr_check_line_ids
        self.assertEqual(line.ordered_qty, 100)
        self.assertEqual(line.received_qty, 100)
        self.assertEqual(line.billed_qty, 100)
        self.assertAlmostEqual(line.ordered_price, 10.0)
        self.assertAlmostEqual(line.billed_price, 10.0)
        self.assertEqual(line.result, 'matched')
        self.assertEqual(bill.ipr_state, 'matched')
        self.assertFalse(bill.ipr_exception_ids)

    def test_rerun_replaces_comparison(self):
        """FR-IPR-0001: running the check again replaces the comparison, one history entry per run."""
        _order, bill = self._order_and_bill(100, 10.0)
        self._check(bill)
        self._check(bill)
        self.assertEqual(len(bill.ipr_check_line_ids), 1)
        self.assertEqual(len(bill.ipr_history_ids.filtered(lambda h: h.event == 'check')), 2)

    # SC-IPR-0001-2
    def test_second_partial_bill(self):
        """SC-IPR-0001-2: 100 received, posted bill of 60, draft bill of 40: billed 100, no quantity exception."""
        order = self.make_order(100, 10.0, received=100)
        first = self.make_bill_from_order(order, 60, 10.0, ref='FIRST')
        first.action_post()
        self.env.flush_all()
        second = self.make_bill_from_order(order, 40, 10.0, ref='SECOND')
        self._check(second)
        line = second.ipr_check_line_ids
        self.assertEqual(line.billed_qty, 100)
        self.assertEqual(line.received_qty, 100)
        self.assertFalse(self._exceptions(second, 'quantity'))

    # SC-IPR-0003-1..3
    def test_price_within_5_percent(self):
        """SC-IPR-0003-1 / BR-IPR-0001: 5 percent on 10.00, billed 10.50 (exactly at the limit): no exception."""
        self._tolerance(price_tolerance_type='percent', price_tolerance_percent=5.0)
        _order, bill = self._order_and_bill(100, 10.50)
        self._check(bill)
        self.assertFalse(self._exceptions(bill, 'price'))
        self.assertEqual(bill.ipr_state, 'matched')

    def test_price_over(self):
        """SC-IPR-0003-2: 5 percent on 10.00, billed 10.51: price exception ordered 10.00, billed 10.51, difference 0.51."""
        self._tolerance(price_tolerance_type='percent', price_tolerance_percent=5.0)
        _order, bill = self._order_and_bill(100, 10.51)
        self._check(bill)
        exc = self._exceptions(bill, 'price')
        self.assertEqual(len(exc), 1)
        line = exc.check_line_id
        self.assertEqual(line, bill.ipr_check_line_ids)
        self.assertAlmostEqual(line.ordered_price, 10.0)
        self.assertAlmostEqual(line.billed_price, 10.51)
        self.assertAlmostEqual(line.price_difference, 0.51)
        self.assertTrue(line.price_exception)
        self.assertEqual(line.result, 'exception')
        self.assertEqual(bill.ipr_state, 'waiting')

    def test_price_below_fixed(self):
        """SC-IPR-0003-3: fixed 0.20 on 10.00, billed 9.70: price exception with difference -0.30."""
        self._tolerance(price_tolerance_type='fixed', price_tolerance_amount=0.20)
        _order, bill = self._order_and_bill(100, 9.70)
        self._check(bill)
        exc = self._exceptions(bill, 'price')
        self.assertEqual(len(exc), 1)
        self.assertAlmostEqual(exc.check_line_id.price_difference, -0.30)

    def test_price_fixed_at_limit(self):
        """BR-IPR-0001: fixed 0.20 on 10.00, billed 10.20 (exactly at the limit): no exception."""
        self._tolerance(price_tolerance_type='fixed', price_tolerance_amount=0.20)
        _order, bill = self._order_and_bill(100, 10.20)
        self._check(bill)
        self.assertFalse(self._exceptions(bill, 'price'))

    def test_price_default_tolerance_zero(self):
        """FR-IPR-0007: a company with no tolerances uses zero, so 10.01 against 10.00 is an exception."""
        _order, bill = self._order_and_bill(100, 10.01)
        self._check(bill)
        self.assertEqual(len(self._exceptions(bill, 'price')), 1)
        self.assertEqual(bill.ipr_state, 'waiting')

    # SC-IPR-0004-1..3
    def test_qty_within_fixed(self):
        """SC-IPR-0004-1: fixed 2 units, 100 received, billed 102: no quantity exception."""
        self._tolerance(qty_tolerance_type='fixed', qty_tolerance_value=2.0)
        _order, bill = self._order_and_bill(102, 10.0)
        self._check(bill)
        self.assertFalse(self._exceptions(bill, 'quantity'))

    def test_qty_over_fixed(self):
        """BR-IPR-0001: fixed 2 units, 100 received, billed 103: quantity exception."""
        self._tolerance(qty_tolerance_type='fixed', qty_tolerance_value=2.0)
        _order, bill = self._order_and_bill(103, 10.0)
        self._check(bill)
        self.assertEqual(len(self._exceptions(bill, 'quantity')), 1)

    def test_qty_percent_boundary(self):
        """BR-IPR-0001: 10 percent of the received quantity: 110 of 100 within, 111 over."""
        self._tolerance(qty_tolerance_type='percent', qty_tolerance_value=10.0)
        _order, within = self._order_and_bill(110, 10.0)
        self._check(within)
        self.assertFalse(self._exceptions(within, 'quantity'))
        _order, over = self._order_and_bill(111, 10.0)
        self._check(over)
        self.assertEqual(len(self._exceptions(over, 'quantity')), 1)

    def test_qty_over_second_bill(self):
        """SC-IPR-0004-2: fixed 2, 100 received, posted 60, second bill 50: received 100, billed 110, difference 10."""
        self._tolerance(qty_tolerance_type='fixed', qty_tolerance_value=2.0)
        order = self.make_order(100, 10.0, received=100)
        first = self.make_bill_from_order(order, 60, 10.0, ref='FIRST')
        first.action_post()
        self.env.flush_all()
        second = self.make_bill_from_order(order, 50, 10.0, ref='SECOND')
        self._check(second)
        exc = self._exceptions(second, 'quantity')
        self.assertEqual(len(exc), 1)
        line = exc.check_line_id
        self.assertEqual(line.received_qty, 100)
        self.assertEqual(line.billed_qty, 110)
        self.assertEqual(line.qty_difference, 10)
        self.assertTrue(line.qty_exception)
        self.assertEqual(second.ipr_state, 'waiting')

    def test_qty_under_received(self):
        """SC-IPR-0004-3: quantity tolerance 0, 80 received, billed 50: no exception (partial billing)."""
        self._tolerance(qty_tolerance_type='fixed', qty_tolerance_value=0.0)
        order = self.make_order(100, 10.0, received=80)
        bill = self.make_bill_from_order(order, 50, 10.0)
        self._check(bill)
        self.assertFalse(self._exceptions(bill, 'quantity'))
        self.assertEqual(bill.ipr_state, 'matched')

    def test_qty_nothing_received(self):
        """BR-IPR-0001: percent tolerance on 0 received allows 0, so billing 1 is an exception."""
        self._tolerance(qty_tolerance_type='percent', qty_tolerance_value=10.0)
        order = self.make_order(100, 10.0, received=0)
        bill = self.make_bill_from_order(order, 1, 10.0)
        self._check(bill)
        self.assertEqual(len(self._exceptions(bill, 'quantity')), 1)

    # SC-IPR-0005-1
    def test_not_on_order_line(self):
        """SC-IPR-0005-1: a freight line of 25.00 without an order is shown Not on an order; status Matched."""
        order = self.make_order(100, 10.0, received=100)
        line = order.order_line
        bill = self.make_bill(order.partner_id, [
            {'product_id': line.product_id.id, 'quantity': 100, 'price_unit': 10.0,
             'purchase_line_id': line.id},
            {'name': 'Freight', 'quantity': 1, 'price_unit': 25.0},
        ])
        self._check(bill)
        rows = bill.ipr_check_line_ids
        self.assertEqual(len(rows), 2)
        freight = rows.filtered(lambda r: not r.purchase_line_id)
        self.assertEqual(len(freight), 1)
        self.assertEqual(freight.result, 'not_on_order')
        self.assertFalse(freight.price_exception or freight.qty_exception)
        self.assertEqual(bill.ipr_state, 'matched')
        self.assertFalse(bill.ipr_exception_ids)

    # SC-IPR-0006-1
    def test_foreign_currency(self):
        """SC-IPR-0006-1: order line 11.00 USD, rate 1.10 USD per EUR, bill 10.00 EUR, tolerance 0: ordered price 10.00 EUR, no exception."""
        eur = self.env.ref('base.EUR')
        usd = self.env.ref('base.USD')
        (eur | usd).active = True
        self.env['res.currency.rate'].search([
            ('currency_id', 'in', (eur | usd).ids),
            ('company_id', 'in', (self.company_a | self.env['res.company']).ids)]).unlink()
        for currency, rate in ((eur, 1.0), (usd, 1.1)):
            self.env['res.currency.rate'].create({
                'currency_id': currency.id, 'company_id': self.company_a.id,
                'name': '2026-01-01', 'rate': rate})
        order = self.env['purchase.order'].with_company(self.company_a).create({
            'partner_id': self.supplier_acme.id,
            'company_id': self.company_a.id,
            'currency_id': usd.id,
            'order_line': [Command.create({
                'product_id': self.product.id, 'product_qty': 10, 'price_unit': 11.0,
                'date_planned': fields.Datetime.now()})],
        })
        order.button_confirm()
        picking = order.picking_ids
        picking.move_ids.quantity = 10
        picking.move_ids.picked = True
        picking.with_context(skip_backorder=True, skip_sms=True).button_validate()
        line = order.order_line
        bill = self.make_bill(self.supplier_acme, [{
            'product_id': line.product_id.id, 'quantity': 10, 'price_unit': 10.0,
            'purchase_line_id': line.id}])
        bill.currency_id = eur
        self._check(bill)
        row = bill.ipr_check_line_ids
        self.assertEqual(row.currency_id, eur)
        self.assertAlmostEqual(row.ordered_price, 10.0, places=2)
        self.assertFalse(self._exceptions(bill, 'price'))

    # SC-IPR-0007-1
    def test_tolerance_in_force(self):
        """SC-IPR-0007-1: an accounting administrator sets 3 percent and fixed 1 unit; the next check uses them."""
        self.env['ipr.tolerance'].with_user(self.account_admin).create({
            'company_id': self.company_a.id,
            'price_tolerance_type': 'percent', 'price_tolerance_percent': 3.0,
            'qty_tolerance_type': 'fixed', 'qty_tolerance_value': 1.0,
        })
        _order, within = self._order_and_bill(101, 10.30)
        self._check(within)
        self.assertEqual(within.ipr_state, 'matched')
        _order, price_over = self._order_and_bill(100, 10.31)
        self._check(price_over)
        self.assertEqual(len(self._exceptions(price_over, 'price')), 1)
        _order, qty_over = self._order_and_bill(102, 10.0)
        self._check(qty_over)
        self.assertEqual(len(self._exceptions(qty_over, 'quantity')), 1)

    # SC-IPR-0020-1
    def test_history_entry(self):
        """SC-IPR-0020-1: tolerance 5 percent, one price exception: one Check entry with user, tolerance and exception."""
        self._tolerance(price_tolerance_type='percent', price_tolerance_percent=5.0)
        _order, bill = self._order_and_bill(100, 10.60)
        self._check(bill)
        entries = bill.ipr_history_ids
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries.event, 'check')
        self.assertEqual(entries.user_id, self.accountant)
        self.assertTrue(entries.date)
        self.assertEqual(entries.state_after, 'waiting')
        self.assertEqual(entries.price_tolerance_type, 'percent')
        self.assertAlmostEqual(entries.price_tolerance_percent, 5.0)
        self.assertEqual(entries.exception_count, 1)
        self.assertIn('price', json.dumps(entries.exceptions_data).lower())

    def test_check_sets_checked_by(self):
        """FR-IPR-0010: the check stamps who checked and when; Waiting also stamps the waiting time."""
        _order, bill = self._order_and_bill(100, 11.0)
        self._check(bill)
        self.assertEqual(bill.ipr_checked_by_id, self.accountant)
        self.assertTrue(bill.ipr_checked_date)
        self.assertTrue(bill.ipr_waiting_since)

    # status protection and scope
    def test_status_write_refused(self):
        """A user cannot write the reconciliation status by hand."""
        _order, bill = self._order_and_bill(100, 10.0)
        with self.assertRaises(UserError):
            bill.with_user(self.accountant).write({'ipr_state': 'approved'})
        self.assertEqual(bill.ipr_state, 'not_checked')

    def test_only_draft_checked(self):
        """FR-IPR-0001: only a draft bill can be checked."""
        _order, bill = self._order_and_bill(100, 10.0)
        bill.action_post()
        with self.assertRaisesRegex(UserError, 'Only draft bills can be checked'):
            bill.with_user(self.accountant).action_ipr_check()

    def test_credit_note_not_checked(self):
        """BR-IPR-0004: a vendor credit note is out of scope and stays Not checked."""
        refund = self.env['account.move'].with_company(self.company_a).create({
            'move_type': 'in_refund', 'partner_id': self.supplier_acme.id,
            'invoice_date': fields.Date.today(), 'company_id': self.company_a.id,
            'invoice_line_ids': [Command.create({'name': 'Return', 'quantity': 1, 'price_unit': 5.0})],
        })
        _order, bill = self._order_and_bill(100, 10.0)
        self.assertFalse(refund.ipr_in_scope)
        self.assertTrue(bill.ipr_in_scope)
        self.assertEqual(refund.ipr_state, 'not_checked')


@tagged('post_install', '-at_install')
class TestReconciliationServiceAcceptance(IprCommon):
    """Acceptance tests of T-004 for the pure tolerance rule.

    Requirements: BR-IPR-0001, FR-IPR-0010.
    """

    def _service(self):
        from odoo.addons.invoice_purchase_reconciliation.services import reconciliation
        return reconciliation

    def test_within_tolerance_percent(self):
        """BR-IPR-0001: percent of the reference value; equality is accepted."""
        within = self._service().within_tolerance
        self.assertTrue(within(0.50, 10.0, 'percent', 5.0))
        self.assertFalse(within(0.51, 10.0, 'percent', 5.0))
        self.assertTrue(within(-0.50, 10.0, 'percent', 5.0))
        self.assertFalse(within(-0.51, 10.0, 'percent', 5.0))

    def test_within_tolerance_fixed(self):
        """BR-IPR-0001: fixed amount compared with the absolute difference."""
        within = self._service().within_tolerance
        self.assertTrue(within(2, 100, 'fixed', 2))
        self.assertFalse(within(3, 100, 'fixed', 2))
        self.assertTrue(within(-0.20, 10.0, 'fixed', 0.20))
        self.assertFalse(within(-0.30, 10.0, 'fixed', 0.20))

    def test_within_tolerance_zero(self):
        """BR-IPR-0001: a zero tolerance only accepts no difference."""
        within = self._service().within_tolerance
        self.assertTrue(within(0, 10.0, 'percent', 0))
        self.assertFalse(within(0.01, 10.0, 'percent', 0))
        self.assertFalse(within(1, 0, 'percent', 10))

    def test_verdict_no_exception_is_matched(self):
        """FR-IPR-0010: no exception and no duplicate gives matched."""
        self.assertEqual(self._service().verdict([], []), 'matched')
