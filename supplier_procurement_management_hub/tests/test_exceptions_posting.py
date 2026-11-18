"""Own tests of T-026 (exception life cycle, direct-write guards, dialogs).

Requirements: FR-SPM-0075, FR-SPM-0076, FR-SPM-0077, FR-SPM-0078
"""
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, tagged

from .matching_common import SpmMatchingCommon


@tagged('post_install', '-at_install')
class TestExceptionLifecycle(SpmMatchingCommon):
    """Requirements: FR-SPM-0076, FR-SPM-0077"""

    def setUp(self):
        super().setUp()
        self._clear_config()
        self._policy('three_way')
        self._tolerance('price', percent=2.0, absolute=5.0)
        self._default_routes(approval_kinds=('price',))
        self.bill = self._bill(self._order(qty=100, receive=100), 100, 12.5)
        self.exc = self._recon(self.bill).exception_ids

    def test_correcting_the_bill_resolves_an_open_exception(self):
        """Requirements: FR-SPM-0076. A corrected price closes the exception."""
        self.bill.invoice_line_ids.price_unit = 11.5
        self.env.flush_all()
        self.assertEqual(self.exc.state, 'resolved')
        self.assertEqual(self.bill.with_user(self.user_acc).spm_open_exception_count, 0)

    def test_changing_an_accepted_bill_reopens_the_exception(self):
        """Requirements: FR-SPM-0077. An acceptance holds for the evidence it saw."""
        self.exc.with_user(self.user_fin).action_spm_accept('Agreed')
        self.bill.invoice_line_ids.price_unit = 13.5
        self.env.flush_all()
        self.assertEqual(self.exc.state, 'open')
        with self.assertRaises(UserError):
            self.bill.with_user(self.user_acc).action_post()

    def test_a_user_cannot_write_the_decision_directly(self):
        """Requirements: FR-SPM-0077. Users hold read access only."""
        with self.assertRaises(AccessError):
            self.exc.with_user(self.user_fin).write({'state': 'accepted'})
        recon = self._recon(self.bill)
        with self.assertRaises(AccessError):
            recon.with_user(self.user_fin).write({'duplicate_cleared': True})

    def test_a_rejected_exception_keeps_the_hold(self):
        """Requirements: FR-SPM-0077. Rejected is not a release."""
        self.exc.with_user(self.user_fin).action_spm_reject('Not agreed')
        with self.assertRaises(UserError):
            self.bill.with_user(self.user_acc).action_post()

    def test_only_a_finance_reviewer_clears_a_duplicate(self):
        """Requirements: FR-SPM-0075."""
        self._plain_bill(self.acme, 1150.0, ref='INV-Z').action_post()
        dup = self._plain_bill(self.acme, 1150.0, ref='INV-Z')
        recon = self._recon(dup)
        with self.assertRaises(AccessError):
            recon.with_user(self.user_acc).action_spm_mark_not_duplicate('mine')
        recon.with_user(self.user_fin).action_spm_mark_not_duplicate('Different order')
        self.assertEqual(recon.exception_ids.state, 'resolved')
        dup.write({'ref': 'INV-Z'})
        self.assertFalse(dup.spm_possible_duplicate_ids)

    def test_the_comment_dialog_accepts_an_exception(self):
        """Requirements: FR-SPM-0077. The dialog is filled and saved as a person does;
        its Confirm button stays for the screen review."""
        action = self.exc.with_user(self.user_fin).action_spm_open_accept_wizard()
        wizard = Form(self.env['procurement.reason.wizard'].with_user(self.user_fin).with_context(
            **action['context']))
        wizard.reason = 'Agreed surcharge'
        record = wizard.save()
        record.action_confirm()
        self.assertEqual(self.exc.state, 'accepted')
