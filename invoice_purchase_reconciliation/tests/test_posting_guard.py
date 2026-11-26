from datetime import timedelta
from unittest.mock import patch

from odoo import fields
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

    def _unchecked_bill(self, ref):
        order = self.make_order(10, 10.0, received=10)
        return self.make_bill_from_order(order, 10, 12.0, ref=ref)

    def test_list_post_keeps_new_check(self):
        """FR-IPR-0002: the list dialog holds a bill it just checked and keeps the check.

        The dialog's button is not pressed: the test calls validate_move, as the button does.
        """
        bill = self._unchecked_bill('LIST-NEW')
        wizard = self.env['validate.account.move'].with_user(self.accountant).with_context(
            active_model='account.move', active_ids=bill.ids).create({})
        action = wizard.validate_move()
        self.assertEqual(action['tag'], 'display_notification')
        self.assertIn('Price', action['params']['message'])
        self.assertEqual((bill.state, bill.ipr_state), ('draft', 'waiting'))
        self.assertEqual(len(bill.ipr_exception_ids), 1)

    def test_form_post_names_open_exceptions(self):
        """FR-IPR-0016: posting an already Waiting bill from its form names the price exception."""
        bill = self._held_bill('FORM-WAIT')
        action = bill.with_user(self.accountant).action_post()
        self.assertIn('Price', action['params']['message'])
        action = self._unchecked_bill('FORM-NEW').with_user(self.accountant).action_post()
        self.assertIn('Price', action['params']['message'])

    def test_autopost_job_checks_and_keeps_result(self):
        """FR-IPR-0002: the job checks a due Not checked bill and the check survives Odoo's rollback."""
        bill = self._unchecked_bill('JOB-NEW')
        bill.write({'auto_post': 'at_date', 'date': fields.Date.today() - timedelta(days=1)})
        cron = type(self.env['ir.cron'])
        self.env.flush_all()
        # the job's commit becomes a savepoint, its rollback goes back to the last one
        savepoints = []

        def commit(*args, **kwargs):
            self.env.flush_all()
            savepoints.append(self.env.cr.savepoint(flush=False))

        def rollback(*args, **kwargs):
            self.env.invalidate_all()
            savepoints[-1].rollback()

        with patch.object(cron, '_commit_progress', side_effect=commit), \
                patch.object(cron, '_rollback_progress', side_effect=rollback):
            self.env['account.move']._autopost_draft_entries()
        self.assertEqual(bill.state, 'draft')
        self.assertEqual(bill.ipr_state, 'waiting')
        self.assertEqual(len(bill.ipr_exception_ids), 1)
        self.assertEqual(bill.auto_post, 'no')
        self.assertIn('not approved', ' '.join(bill.message_ids.mapped('body')))

    def test_credit_note_never_held(self):
        """FR-IPR-0016: a credit note is never held, whatever its status."""
        refund = self.env['account.move'].with_company(self.company_a).create({
            'move_type': 'in_refund',
            'partner_id': self.supplier_acme.id,
            'invoice_date': '2026-01-01',
            'invoice_line_ids': [(0, 0, {'product_id': self.product.id, 'quantity': 1, 'price_unit': 5.0})],
        })
        refund.sudo().write({'ipr_state': 'waiting'})
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
