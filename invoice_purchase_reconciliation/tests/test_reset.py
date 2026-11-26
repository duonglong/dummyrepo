from odoo import Command, fields
from odoo.tests import tagged

from .common import IprCommon


@tagged('post_install', '-at_install')
class TestReset(IprCommon):
    """Own tests of the reset of a checked draft bill.

    Requirements: FR-IPR-0018, FR-IPR-0020
    """

    def _checked_bill(self, ref, price=12.0):
        order = self.make_order(10, 10.0, received=10)
        bill = self.make_bill_from_order(order, 10, price, ref=ref)
        bill.with_user(self.accountant).action_ipr_check()
        return bill

    def _resets(self, bill):
        return bill.sudo().ipr_history_ids.filtered(lambda h: h.event == 'reset')

    def _product_line(self, bill):
        return bill.invoice_line_ids.filtered(lambda line: line.display_type == 'product')

    def test_price_change_resets(self):
        """SC-IPR-0018-1: a price change sends a Waiting bill back and clears the results."""
        bill = self._checked_bill('T8-PRICE')
        self.assertEqual(bill.ipr_state, 'waiting')
        self.assertTrue(bill.sudo().ipr_exception_ids)
        self._product_line(bill).write({'price_unit': 15.0})
        self.assertEqual(bill.ipr_state, 'not_checked')
        self.assertFalse(bill.ipr_waiting_since)
        self.assertFalse(bill.sudo().ipr_exception_ids)
        self.assertFalse(bill.sudo().ipr_check_line_ids)
        self.assertEqual(len(self._resets(bill)), 1)

    def test_due_date_keeps_status(self):
        """SC-IPR-0018-2: an unwatched field keeps the status and adds no entry."""
        bill = self._checked_bill('T8-DUE')
        bill.write({'invoice_date_due': fields.Date.add(fields.Date.today(), days=30)})
        self.assertEqual(bill.ipr_state, 'waiting')
        self.assertFalse(self._resets(bill))

    def test_same_value_write_keeps_status(self):
        """FR-IPR-0018: a write of the values the bill already has keeps the status."""
        bill = self._checked_bill('T8-SAME')
        line = self._product_line(bill)
        line.write({'price_unit': line.price_unit, 'quantity': line.quantity})
        bill.write({'ref': bill.ref})
        self.assertEqual(bill.ipr_state, 'waiting')
        self.assertFalse(self._resets(bill))

    def test_unchecked_bill_gets_no_entry(self):
        """FR-IPR-0020: a change of a bill never checked adds no reset entry."""
        order = self.make_order(10, 10.0, received=10)
        bill = self.make_bill_from_order(order, 10, 12.0, ref='T8-FRESH')
        bill.write({'ref': 'T8-FRESH-2'})
        self.assertFalse(self._resets(bill))

    def test_deleting_the_bill_adds_no_reset_entry(self):
        """FR-IPR-0018: deleting a checked draft bill removes its lines without a reset entry."""
        bill = self._checked_bill('T8-DELETE')
        bill.unlink()
        history = self.env['ipr.history'].sudo().search([('bill_reference', '=', 'T8-DELETE')])
        self.assertFalse(history.filtered(lambda h: h.event == 'reset'))
        self.assertTrue(history.filtered(lambda h: h.event == 'delete'))

    def test_caller_context_cannot_skip_the_reset(self):
        """FR-IPR-0018: a client-supplied context key never turns the reset off."""
        for key in ('ipr_skip_reset', 'ipr_deleting_move_ids'):
            bill = self._checked_bill('T8-CTX-%s' % key)
            self._product_line(bill).with_context(**{key: True}).write({'price_unit': 15.0})
            self.assertEqual(bill.ipr_state, 'not_checked')

    def test_failed_deletion_leaves_no_marker(self):
        """FR-IPR-0018: after a refused deletion the bill still resets on a change."""
        bill = self._checked_bill('T8-FAILDEL')
        with self.assertRaises(Exception), self.cr.savepoint():
            bill.with_user(self.env.ref('base.public_user')).unlink()
        self._product_line(bill).write({'price_unit': 15.0})
        self.assertEqual(bill.ipr_state, 'not_checked')

    def test_section_line_added_keeps_status(self):
        """FR-IPR-0018: a section line is not a product line."""
        bill = self._checked_bill('T8-SECTION')
        bill.write({'invoice_line_ids': [Command.create({
            'display_type': 'line_section', 'name': 'Delivery', 'quantity': 0})]})
        self.assertEqual(bill.ipr_state, 'waiting')
