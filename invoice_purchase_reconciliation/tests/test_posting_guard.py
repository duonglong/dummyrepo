from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import IprCommon


@tagged('post_install', '-at_install')
class TestPostingGuard(IprCommon):
    """Posting and payment guards of T-007.

    Requirements: FR-IPR-0002, FR-IPR-0016, FR-IPR-0017
    """

    def _held_bill(self, ref):
        order = self.make_order(10, 10.0, received=10)
        bill = self.make_bill_from_order(order, 10, 12.0, ref=ref)
        bill.with_user(self.accountant).action_ipr_check()
        return bill

    def test_credit_note_never_held(self):
        """FR-IPR-0016: a credit note is never held, whatever its status."""
        refund = self.env['account.move'].with_company(self.company_a).create({
            'move_type': 'in_refund',
            'partner_id': self.supplier_acme.id,
            'invoice_date': '2026-01-01',
            'invoice_line_ids': [(0, 0, {'product_id': self.product.id, 'quantity': 1, 'price_unit': 5.0})],
        })
        self.assertFalse(refund._ipr_held())
        refund.with_user(self.accountant).action_post()
        self.assertEqual(refund.state, 'posted')

    def test_pay_from_bill_names_held_bill(self):
        """FR-IPR-0017: the bill's own payment action refuses and names the held bill."""
        bill = self._held_bill('GUARD-PAY')
        with self.assertRaises(UserError) as caught:
            bill.with_user(self.accountant).action_force_register_payment()
        self.assertIn('GUARD-PAY', str(caught.exception))

    def test_held_post_message_names_bill(self):
        """FR-IPR-0016: the hard guard names the held bill."""
        bill = self._held_bill('GUARD-POST')
        with self.assertRaises(UserError) as caught:
            bill._post()
        self.assertIn('GUARD-POST', str(caught.exception))
