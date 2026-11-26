from odoo.tests import tagged

from .common import IprCommon


@tagged('post_install', '-at_install')
class TestSmoke(IprCommon):
    """Requirements: NFR-IPR-0003, NFR-IPR-0004"""

    def test_module_installed(self):
        module = self.env['ir.module.module'].search(
            [('name', '=', 'invoice_purchase_reconciliation')])
        self.assertEqual(module.state, 'installed')

    def test_fixture_bill(self):
        order = self.make_order(100, 10.00, received=100)
        bill = self.make_bill_from_order(order, 100, 10.00)
        self.assertEqual(bill.state, 'draft')
        self.assertEqual(bill.move_type, 'in_invoice')
        self.assertEqual(bill.invoice_line_ids.purchase_line_id, order.order_line)
        self.assertEqual(order.order_line.qty_received, 100)

    def test_two_companies(self):
        self.assertNotEqual(self.company_a, self.company_b)
        self.assertEqual(self.accountant.company_ids, self.company_a)
