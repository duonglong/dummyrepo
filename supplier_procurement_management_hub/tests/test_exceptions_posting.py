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

    def test_a_rejected_exception_is_resolved_when_the_bill_is_corrected(self):
        """Requirements: FR-SPM-0077. A corrected bill releases a rejection."""
        self.exc.with_user(self.user_fin).action_spm_reject('Not agreed')
        self.bill.invoice_line_ids.price_unit = 11.5
        self.env.flush_all()
        self.assertEqual(self.exc.state, 'resolved')
        self.bill.with_user(self.user_acc).action_post()
        self.assertEqual(self.bill.state, 'posted')

    def test_a_rejected_exception_can_be_resolved_by_finance(self):
        """Requirements: FR-SPM-0077. Resolve also applies to a rejection."""
        self.exc.with_user(self.user_fin).action_spm_reject('Not agreed')
        self.exc.with_user(self.user_fin).action_spm_resolve()
        self.assertEqual(self.exc.state, 'resolved')

    def test_the_approval_setting_is_read_when_posting(self):
        """Requirements: FR-SPM-0077. Turning approval on after matching holds the bill."""
        route = self.env['procurement.exception.route'].search([('kind', '=', 'price')])
        route.requires_approval = False
        self.exc.sudo().requires_approval = False
        route.requires_approval = True
        with self.assertRaises(UserError):
            self.bill.with_user(self.user_acc).action_post()

    def test_a_buyer_is_refused_every_decision(self):
        """Requirements: FR-SPM-0077. Buyers cannot accept, reject, resolve or review."""
        exc = self.exc.with_user(self.user_ann)
        for call in (lambda: exc.action_spm_accept('x'), lambda: exc.action_spm_reject('x'),
                     exc.action_spm_resolve, exc.action_spm_review):
            with self.assertRaises(AccessError):
                call()
        self.assertEqual(self.exc.state, 'open')

    def test_a_manager_cannot_clear_a_duplicate(self):
        """Requirements: FR-SPM-0075. Clearing is reserved to the Finance reviewer."""
        self._plain_bill(self.acme, 1150.0, ref='INV-M').action_post()
        recon = self._recon(self._plain_bill(self.acme, 1150.0, ref='INV-M'))
        with self.assertRaises(AccessError):
            recon.with_user(self.user_pm).action_spm_mark_not_duplicate('mine')

    def test_a_user_cannot_create_a_cleared_reconciliation(self):
        """Requirements: FR-SPM-0075. Create is guarded like write."""
        bill = self._plain_bill(self.acme, 10.0, ref='NEW-1')
        self.env['procurement.reconciliation'].sudo().search([('move_id', '=', bill.id)]).unlink()
        with self.assertRaises(AccessError):
            self.env['procurement.reconciliation'].with_user(self.user_fin).create(
                {'move_id': bill.id, 'duplicate_cleared': True})

    def test_a_cleared_bill_is_flagged_again_for_a_new_duplicate(self):
        """Requirements: FR-SPM-0075. The clearing covers the bills the reviewer saw."""
        self._plain_bill(self.acme, 1150.0, ref='INV-A').action_post()
        dup = self._plain_bill(self.acme, 1150.0, ref='INV-A')
        self._recon(dup).with_user(self.user_fin).action_spm_mark_not_duplicate('Other order')
        second = self._plain_bill(self.acme, 20.0, ref='INV-B')
        second.action_post()
        dup.write({'ref': 'INV-B'})
        self.env.flush_all()
        self.assertEqual(self._recon(dup).duplicate_move_ids, second)
        self.assertEqual(self._recon(dup).exception_ids.filtered(lambda e: e.state == 'open').kind, 'duplicate')


@tagged('post_install', '-at_install')
class TestExceptionRefresh(SpmMatchingCommon):
    """Requirements: FR-SPM-0076, FR-SPM-0077"""

    def setUp(self):
        super().setUp()
        self._clear_config()
        self._policy('two_way')
        self._tolerance('quantity')
        self._default_routes(approval_kinds=('quantity',))

    def test_an_unchanged_refresh_keeps_an_accepted_quantity_exception(self):
        """Requirements: FR-SPM-0077. 3 units over at 1.10 is not exact in binary."""
        bill = self._bill(self._order(qty=10, price=1.10), 13, 1.10)
        exc = self._recon(bill).exception_ids.filtered(lambda e: e.kind == 'quantity')
        exc.with_user(self.user_fin).action_spm_accept('Agreed')
        bill.action_spm_run_matching()
        self.assertEqual(exc.state, 'accepted')
        self.assertEqual(exc.decision_comment, 'Agreed')

    def test_a_matching_error_does_not_duplicate_exceptions(self):
        """Requirements: FR-SPM-0076. Lines dropped by an error keep their exceptions."""
        bill = self._bill(self._order(qty=10, price=1.10), 13, 1.10)
        recon = self._recon(bill)
        before = recon.exception_ids
        recon.sudo().line_ids.unlink()
        bill.action_spm_run_matching()
        self.assertEqual(recon.exception_ids, before)
