from lxml import etree

from odoo.exceptions import UserError
from odoo.tests import Form, tagged

from .common import IprCommon


@tagged('post_install', '-at_install')
class TestApproval(IprCommon):
    """Own tests of T-006: the decision dialog, the server guards and the Bills to Approve screens.

    Requirements: BR-IPR-0003, FR-IPR-0011, FR-IPR-0012, FR-IPR-0013, FR-IPR-0014, FR-IPR-0015,
    FR-IPR-0023
    """

    def _waiting_bill(self, ref='DLG-1'):
        order = self.make_order(10, 10.0, received=10)
        bill = self.make_bill_from_order(order, 10, 12.0, ref=ref)
        bill.with_user(self.accountant).action_ipr_check()
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(bill.ipr_state, 'waiting')
        return bill

    def _dialog(self, user, bill, decision, reason):
        """Open the dialog as the form's button does (action context), fill it and save.

        Form presses no button: the Approve and Reject buttons and the confirm button stay for
        the screen review.
        """
        view = f'invoice_purchase_reconciliation.ipr_reject_wizard_view_form_{decision}'
        model = self.env['ipr.reject.wizard'].with_user(user).with_context(
            default_move_id=bill.id, default_decision=decision)
        with Form(model, view=view) as form:
            if reason:
                form.reason = reason
            return form.save()

    def test_dialog_rejects_with_reason(self):
        """FR-IPR-0013: the dialog's confirm rejects the bill with the typed reason."""
        bill = self._waiting_bill()
        wizard = self._dialog(self.purchase_manager, bill, 'reject', 'Ask Acme for a credit note')
        wizard.action_confirm()
        self.env.invalidate_all()
        self.assertEqual(bill.ipr_state, 'rejected')
        self.assertEqual(bill.ipr_decision_comment, 'Ask Acme for a credit note')

    def test_dialog_approves_without_comment(self):
        """FR-IPR-0012: the approve dialog takes an optional comment."""
        bill = self._waiting_bill(ref='DLG-2')
        self._dialog(self.account_admin, bill, 'approve', None).action_confirm()
        self.env.invalidate_all()
        self.assertEqual(bill.ipr_state, 'approved')
        self.assertFalse(bill.ipr_decision_comment)

    def test_dialog_blank_reason_refused(self):
        """FR-IPR-0013: a blank reason typed in the dialog is refused by the server too."""
        bill = self._waiting_bill(ref='DLG-3')
        wizard = self.env['ipr.reject.wizard'].with_user(self.account_admin).create({
            'move_id': bill.id, 'decision': 'reject', 'reason': '   '})
        with self.assertRaises(UserError):
            wizard.action_confirm()
        self.env.invalidate_all()
        self.assertEqual(bill.ipr_state, 'waiting')

    def test_dialog_checks_the_caller(self):
        """FR-IPR-0014: a user without manager rights cannot decide through the dialog."""
        bill = self._waiting_bill(ref='DLG-4')
        wizard = self.env['ipr.reject.wizard'].with_user(self.purchase_manager).create({
            'move_id': bill.id, 'decision': 'approve'})
        with self.assertRaises(UserError):
            wizard.with_user(self.purchase_user).action_confirm()
        self.env.invalidate_all()
        self.assertEqual(bill.ipr_state, 'waiting')

    def test_approver_cannot_write_status(self):
        """BR-IPR-0003: an approver cannot write the status through their own rights."""
        bill = self._waiting_bill(ref='DLG-5')
        with self.assertRaises(UserError):
            bill.with_user(self.purchase_manager).write({'ipr_state': 'approved'})

    def test_matched_bill_refused(self):
        """FR-IPR-0015: a bill that never waited is refused with a plain message."""
        order = self.make_order(10, 10.0, received=10)
        bill = self.make_bill_from_order(order, 10, 10.0, ref='DLG-6')
        bill.with_user(self.accountant).action_ipr_check()
        self.assertEqual(bill.ipr_state, 'matched')
        with self.assertRaises(UserError) as caught:
            bill.with_user(self.purchase_manager).action_ipr_approve()
        self.assertIn('not waiting', str(caught.exception))

    def test_to_approve_screens(self):
        """UX-IPR-002: the row opens the form on the Reconciliation tab, the list is filtered."""
        action = self.env.ref('invoice_purchase_reconciliation.ipr_bill_to_approve_action')
        self.assertIn('search_default_ipr_waiting', action.context)
        form_view = action.view_ids.filtered(lambda v: v.view_mode == 'form').view_id
        arch = etree.fromstring(
            self.env['account.move'].with_user(self.purchase_manager)
            .get_views([(form_view.id, 'form')])['views']['form']['arch'])
        pages = arch.xpath("//page[@name='ipr_reconciliation']")
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].get('autofocus'), 'autofocus')
        self.assertTrue(arch.xpath("//button[@string='Approve']"))

    def test_dialog_buttons(self):
        """UX-IPR-001: the form's Approve and Reject buttons open the dialog with the bill and decision."""
        arch = etree.fromstring(
            self.env['account.move'].with_user(self.purchase_manager)
            .get_views([(False, 'form')])['views']['form']['arch'])
        for string, decision in (('Approve', 'approve'), ('Reject', 'reject')):
            button = arch.xpath(f"//button[@string='{string}']")[0]
            self.assertEqual(button.get('type'), 'action')
            self.assertIn(f"'default_decision': '{decision}'", button.get('context'))
