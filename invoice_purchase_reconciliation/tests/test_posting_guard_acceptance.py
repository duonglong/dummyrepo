from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import IprCommon


@tagged('post_install', '-at_install')
class TestPostingGuardAcceptance(IprCommon):
    """Acceptance tests of T-007: check before posting, posting guard and payment guard.

    Requirements: FR-IPR-0002, FR-IPR-0012, FR-IPR-0016, FR-IPR-0017.
    Posting is driven by action_post, the validate dialog's validate_move and the payment
    register's _create_payments; the buttons and dialogs that call them are left to Odoo.
    """

    def _set_creator(self, bill, user):
        self.env.flush_all()
        self.env.cr.execute("UPDATE account_move SET create_uid = %s WHERE id = %s", (user.id, bill.id))
        bill.invalidate_recordset(['create_uid'])

    def _bill(self, bill_price, ref, creator=None):
        order = self.make_order(10, 10.0, received=10)
        bill = self.make_bill_from_order(order, 10, bill_price, ref=ref)
        self._set_creator(bill, creator or self.accountant)
        return bill

    def _refresh(self):
        self.env.flush_all()
        self.env.invalidate_all()

    def _held_bill(self, ref, decision=None):
        """Draft bill billing 12.00 against 10.00, checked: waiting, or rejected when decision='reject'."""
        bill = self._bill(12.0, ref)
        bill.with_user(self.accountant).action_ipr_check()
        if decision == 'reject':
            bill.with_user(self.purchase_manager).action_ipr_reject('Ask for a credit note')
        elif decision == 'approve':
            bill.with_user(self.purchase_manager).action_ipr_approve('OK')
        self._refresh()
        return bill

    def _posted_bill(self, ref, state=None):
        """Posted bill matching its order; `state` is then forced (superuser) to stand for a held bill."""
        bill = self._bill(10.0, ref)
        bill.with_user(self.accountant).action_post()
        self._refresh()
        self.assertEqual(bill.state, 'posted')
        if state:
            bill.write({'ipr_state': state})
            self._refresh()
        return bill

    # SC-IPR-0002-1
    def test_post_matched(self):
        """SC-IPR-0002-1: a Not checked bill that matches is checked on posting and posted."""
        bill = self._bill(10.0, 'POST-OK')
        self.assertEqual(bill.ipr_state, 'not_checked')
        bill.with_user(self.accountant).action_post()
        self._refresh()
        self.assertEqual(bill.state, 'posted')
        self.assertEqual(bill.ipr_state, 'matched')
        self.assertTrue(bill.sudo().ipr_history_ids.filtered(lambda h: h.event == 'check'))

    # SC-IPR-0002-2
    def test_post_held_keeps_results(self):
        """SC-IPR-0002-2: 12.00 against 10.00, tolerance 0: stays draft, Waiting, one price exception kept."""
        bill = self._bill(12.0, 'POST-HELD')
        result = bill.with_user(self.accountant).action_post()
        self._refresh()
        self.assertEqual(bill.state, 'draft')
        self.assertEqual(bill.ipr_state, 'waiting')
        self.assertEqual(len(bill.ipr_exception_ids), 1)
        self.assertEqual(bill.ipr_exception_ids.kind, 'price')
        self.assertEqual(len(bill.ipr_check_line_ids), 1)
        self.assertTrue(bill.ipr_waiting_since)
        self.assertIsInstance(result, dict)
        self.assertEqual(result.get('tag'), 'display_notification')

    # SC-IPR-0012-1
    def test_post_after_approval(self):
        """SC-IPR-0012-1: after a manager approved, the Accountant can post the bill."""
        bill = self._held_bill('POST-APPROVED', decision='approve')
        self.assertEqual(bill.ipr_state, 'approved')
        bill.with_user(self.accountant).action_post()
        self._refresh()
        self.assertEqual(bill.state, 'posted')
        self.assertEqual(bill.ipr_state, 'approved')

    # SC-IPR-0016-1
    def test_post_waiting_refused(self):
        """SC-IPR-0016-1: posting a Waiting bill is refused naming the price exception; it stays draft."""
        bill = self._held_bill('POST-WAIT')
        with self.assertRaises(UserError) as caught:
            bill.with_user(self.accountant)._post()
        self.assertIn('price', str(caught.exception).lower())
        try:
            bill.with_user(self.accountant).action_post()
        except UserError:
            pass
        self._refresh()
        self.assertEqual(bill.state, 'draft')
        self.assertEqual(bill.ipr_state, 'waiting')

    # SC-IPR-0016-2
    def test_list_post_all_or_nothing(self):
        """SC-IPR-0016-2: a Rejected and a Matched bill posted together via the list dialog: neither is posted."""
        rejected = self._held_bill('LIST-REJ', decision='reject')
        matched = self._bill(10.0, 'LIST-OK')
        matched.with_user(self.accountant).action_ipr_check()
        self._refresh()
        self.assertEqual(rejected.ipr_state, 'rejected')
        self.assertEqual(matched.ipr_state, 'matched')
        selection = rejected | matched
        wizard = self.env['validate.account.move'].with_user(self.accountant).with_context(
            active_model='account.move', active_ids=selection.ids).create({})
        with self.assertRaises(UserError) as caught:
            wizard.validate_move()
        message = str(caught.exception)
        self.assertTrue(rejected.ref in message or rejected.display_name in message, message)
        self._refresh()
        self.assertEqual(rejected.state, 'draft')
        self.assertEqual(matched.state, 'draft')

    def test_list_action_post_all_or_nothing(self):
        """FR-IPR-0016: action_post on a selection holding a Waiting bill posts none of them."""
        waiting = self._held_bill('SEL-WAIT')
        matched = self._bill(10.0, 'SEL-OK')
        matched.with_user(self.accountant).action_ipr_check()
        (waiting | matched).with_user(self.accountant).action_post()
        self._refresh()
        self.assertEqual(waiting.state, 'draft')
        self.assertEqual(matched.state, 'draft')

    def test_direct_post_guard(self):
        """FR-IPR-0016: _post() on a Waiting or a Rejected bill raises, whichever route calls it."""
        for decision in (None, 'reject'):
            bill = self._held_bill(f'DIRECT-{decision}', decision=decision)
            with self.assertRaises(UserError):
                bill._post()
            self._refresh()
            self.assertEqual(bill.state, 'draft')

    def test_direct_post_checks_not_checked(self):
        """FR-IPR-0002: _post() on a Not checked bill with an exception raises and does not post."""
        bill = self._bill(12.0, 'DIRECT-NC')
        with self.assertRaises(UserError):
            bill._post()
        self.assertEqual(bill.state, 'draft')

    def test_autopost_job(self):
        """FR-IPR-0016: the auto-post job leaves a held bill in draft."""
        bill = self._bill(12.0, 'AUTO-HELD')
        bill.write({'auto_post': 'at_date', 'date': fields.Date.today() - timedelta(days=1)})
        bill.with_user(self.accountant).action_ipr_check()
        self._refresh()
        self.assertEqual(bill.ipr_state, 'waiting')
        # the job commits its progress, which a test cursor forbids
        cron = type(self.env['ir.cron'])
        with patch.object(cron, '_commit_progress'), patch.object(cron, '_rollback_progress'):
            self.env['account.move']._autopost_draft_entries()
        self._refresh()
        self.assertEqual(bill.state, 'draft')
        self.assertEqual(bill.ipr_state, 'waiting')

    # SC-IPR-0017-1
    def test_pay_waiting_refused(self):
        """SC-IPR-0017-1: payment of a posted Waiting bill is refused naming it, from the bill and the dialog."""
        bill = self._posted_bill('PAY-WAIT', state='waiting')
        with self.assertRaises(UserError) as caught:
            bill.with_user(self.accountant).action_force_register_payment()
        self.assertIn(bill.name, str(caught.exception))
        register = self.env['account.payment.register'].with_user(self.accountant).with_context(
            active_model='account.move', active_ids=bill.ids).create({})
        payments_before = self.env['account.payment'].search_count([])
        with self.assertRaises(UserError) as caught:
            register._create_payments()
        self.assertIn(bill.name, str(caught.exception))
        self.assertEqual(self.env['account.payment'].search_count([]), payments_before)

    def test_pay_rejected_refused(self):
        """FR-IPR-0017: a Rejected posted bill cannot be paid either."""
        bill = self._posted_bill('PAY-REJ', state='rejected')
        register = self.env['account.payment.register'].with_user(self.accountant).with_context(
            active_model='account.move', active_ids=bill.ids).create({})
        with self.assertRaises(UserError):
            register._create_payments()

    def test_pay_selection(self):
        """FR-IPR-0017: one blocked bill among three refuses the whole selection and names the blocked bill."""
        ok_1 = self._posted_bill('SEL-PAY-1')
        blocked = self._posted_bill('SEL-PAY-2', state='waiting')
        ok_2 = self._posted_bill('SEL-PAY-3')
        selection = ok_1 | blocked | ok_2
        register = self.env['account.payment.register'].with_user(self.accountant).with_context(
            active_model='account.move', active_ids=selection.ids).create({})
        payments_before = self.env['account.payment'].search_count([])
        with self.assertRaises(UserError) as caught:
            register._create_payments()
        message = str(caught.exception)
        self.assertIn(blocked.name, message)
        self.assertNotIn(ok_1.name, message)
        self.assertNotIn(ok_2.name, message)
        self.assertEqual(self.env['account.payment'].search_count([]), payments_before)

    # SC-IPR-0017-2
    def test_pay_approved(self):
        """SC-IPR-0017-2: an Approved posted bill is paid normally."""
        bill = self._held_bill('PAY-APPROVED', decision='approve')
        bill.with_user(self.accountant).action_post()
        self._refresh()
        self.assertEqual(bill.state, 'posted')
        register = self.env['account.payment.register'].with_user(self.accountant).with_context(
            active_model='account.move', active_ids=bill.ids).create({})
        payments = register._create_payments()
        self.assertTrue(payments)
        self._refresh()
        self.assertIn(bill.payment_state, ('paid', 'in_payment'))
