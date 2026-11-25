"""Acceptance tests of T-026 (duplicates, exceptions, approval, posting guard).

Requirements: BR-SPM-0015, BR-SPM-0016, FR-SPM-0073, FR-SPM-0074, FR-SPM-0075,
FR-SPM-0076, FR-SPM-0077, FR-SPM-0078

Also here, because they need the exception model T-026 builds: the exception
halves of SC-SPM-0073-1/2/3 and SC-SPM-0074-1/2.

The design names no method for the exception decisions, so the tests use
these (recorded in the module notes): ``action_spm_accept(comment)``,
``action_spm_resolve()`` on ``procurement.reconciliation.exception`` and
``action_spm_mark_not_duplicate(comment)`` on ``procurement.reconciliation``.
The reason dialog that wraps them (``procurement.reason.wizard``) is a form
step the tests leave to the build. Notification of the owner is accepted as a
follower, an activity or a notification of the owner.
"""
from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .matching_common import SpmMatchingCommon


@tagged('post_install', '-at_install')
class TestExceptionsAcceptance(SpmMatchingCommon):
    """Requirements: FR-SPM-0073, FR-SPM-0074, FR-SPM-0076"""

    def setUp(self):
        super().setUp()
        self._clear_config()
        self._policy('three_way')
        self._tolerance('price', percent=2.0, absolute=5.0)
        self._tolerance('quantity')
        self._default_routes()

    def _kinds(self, bill):
        return sorted(self._recon(bill).exception_ids.mapped('kind'))

    def test_sc_0073_1_quantity_and_price_exceptions_are_created(self):
        """Requirements: FR-SPM-0073. Scenario SC-SPM-0073-1."""
        bill = self._bill(self._order(receive=90), 100, 11.8)
        self.assertEqual(self._kinds(bill), ['price', 'quantity'])

    def test_sc_0073_2_currency_exception_is_created(self):
        """Requirements: FR-SPM-0073, FR-SPM-0074. Scenario SC-SPM-0073-2."""
        bill = self._bill(self._order(currency=self.eur, receive=100), 100, 11.5,
                          currency=self.usd)
        self.assertIn('currency', self._kinds(bill))

    def test_sc_0073_3_no_exception_when_billing_less_than_received(self):
        """Requirements: FR-SPM-0073. Scenario SC-SPM-0073-3."""
        bill = self._bill(self._order(receive=90), 60, 11.5)
        self.assertEqual(self._kinds(bill), [])

    def test_sc_0074_1_no_exception_within_tolerance(self):
        """Requirements: FR-SPM-0074. Scenario SC-SPM-0074-1."""
        bill = self._bill(self._order(qty=10, receive=10), 10, 11.7)
        self.assertEqual(self._kinds(bill), [])

    def test_sc_0074_2_price_exception_above_tolerance(self):
        """Requirements: FR-SPM-0074. Scenario SC-SPM-0074-2."""
        bill = self._bill(self._order(qty=100, receive=100), 100, 12.5)
        self.assertEqual(self._kinds(bill), ['price'])

    def test_sc_0076_1_exception_carries_evidence_owner_and_status(self):
        """Requirements: FR-SPM-0076. Scenario SC-SPM-0076-1."""
        order = self._order(qty=100, receive=100)
        bill = self._bill(order, 100, 12.5)
        exc = self._recon(bill).exception_ids
        self.assertEqual(exc.kind, 'price')
        self.assertEqual(exc.state, 'open')
        self.assertEqual(exc.owner_id, self.user_ann)
        self.assertIn('11.5', exc.expected_value)
        self.assertIn('12.5', exc.found_value)
        self.assertAlmostEqual(exc.amount_at_stake, 100.0)
        self.assertEqual(exc.move_id, bill)
        self.assertEqual(exc.purchase_line_id, order.order_line)
        self.assertTrue(exc.stock_move_ids)
        self.assertEqual(exc.stock_move_ids.picking_id, order.picking_ids)

    def test_sc_0076_1_the_owner_is_notified(self):
        """Requirements: FR-SPM-0076. Scenario SC-SPM-0076-1: Ann is notified."""
        bill = self._bill(self._order(qty=100, receive=100), 100, 12.5)
        exc = self._recon(bill).exception_ids
        partner = self.user_ann.partner_id
        notified = (
            partner in exc.message_partner_ids
            or self.user_ann in exc.activity_ids.user_id
            or bool(self.env['mail.notification'].sudo().search_count([
                ('res_partner_id', '=', partner.id),
                ('mail_message_id.model', '=', exc._name),
                ('mail_message_id.res_id', '=', exc.id)])))
        self.assertTrue(notified)

    def test_sc_0076_1_a_finance_route_names_its_owner(self):
        """Requirements: FR-SPM-0076. Duplicates go to the route's named owner."""
        self.env['procurement.exception.route'].search(
            [('company_id', '=', self.company.id), ('kind', '=', 'duplicate')]).unlink()
        self._route('duplicate', 'finance', owner=self.user_fin)
        first = self._plain_bill(self.acme, 1150.0, ref='INV-OWN')
        first.action_post()
        dup = self._plain_bill(self.acme, 1150.0, ref='INV-OWN')
        exc = self._recon(dup).exception_ids
        self.assertEqual(exc.kind, 'duplicate')
        self.assertEqual(exc.owner_id, self.user_fin)


@tagged('post_install', '-at_install')
class TestDuplicateBillsAcceptance(SpmMatchingCommon):
    """Requirements: BR-SPM-0015, FR-SPM-0075"""

    def setUp(self):
        super().setUp()
        self._clear_config()
        self._default_routes()

    def test_sc_0075_1_same_reference_is_flagged_with_the_other_bill(self):
        """Requirements: FR-SPM-0075, BR-SPM-0015. Scenario SC-SPM-0075-1."""
        first = self._plain_bill(self.acme, 1150.0, ref='INV-778')
        first.action_post()
        new = self._plain_bill(self.acme, 500.0, ref='INV-778')
        recon = self._recon(new)
        self.assertEqual(recon.duplicate_move_ids, first)
        self.assertEqual(new.spm_possible_duplicate_ids, first)
        exc = recon.exception_ids
        self.assertEqual(exc.kind, 'duplicate')
        self.assertIn(first.name, (exc.expected_value or '') + (exc.found_value or ''))

    def test_the_same_reference_of_another_vendor_is_not_flagged(self):
        """Requirements: BR-SPM-0015. Only bills of the same vendor count."""
        other = self._plain_bill(self.cleanco, 1150.0, ref='INV-778')
        other.action_post()
        new = self._plain_bill(self.acme, 1150.0, ref='INV-778', date=fields.Date.from_string('2025-01-01'))
        self.assertFalse(self._recon(new).duplicate_move_ids)

    def test_a_cancelled_bill_is_not_a_duplicate(self):
        """Requirements: BR-SPM-0015. Cancelled bills are left out."""
        old = self._plain_bill(self.acme, 1150.0, ref='INV-CXL')
        old.button_cancel()
        new = self._plain_bill(self.acme, 1150.0, ref='INV-CXL')
        self.assertFalse(self._recon(new).duplicate_move_ids)

    def test_sc_0075_2_same_amount_ten_days_apart_is_not_flagged(self):
        """Requirements: BR-SPM-0015. Scenario SC-SPM-0075-2: 7-day window."""
        self.company.spm_duplicate_bill_days = 7
        self._plain_bill(self.acme, 1150.0, ref='A-1', date=fields.Date.from_string('2026-11-10'))
        second = self._plain_bill(self.acme, 1150.0, ref='A-2',
                                  date=fields.Date.from_string('2026-11-20'))
        self.assertFalse(self._recon(second).duplicate_move_ids)
        self.assertFalse(second.spm_possible_duplicate_ids)

    def test_br_0015_same_amount_six_days_apart_is_flagged(self):
        """Requirements: BR-SPM-0015. Just inside the 7-day window."""
        self.company.spm_duplicate_bill_days = 7
        first = self._plain_bill(self.acme, 1150.0, ref='B-1', date=fields.Date.from_string('2026-11-10'))
        second = self._plain_bill(self.acme, 1150.0, ref='B-2',
                                  date=fields.Date.from_string('2026-11-16'))
        self.assertEqual(self._recon(second).duplicate_move_ids, first)

    def test_br_0015_same_amount_eight_days_apart_is_not_flagged(self):
        """Requirements: BR-SPM-0015. Just outside the 7-day window."""
        self.company.spm_duplicate_bill_days = 7
        self._plain_bill(self.acme, 1150.0, ref='C-1', date=fields.Date.from_string('2026-11-10'))
        second = self._plain_bill(self.acme, 1150.0, ref='C-2',
                                  date=fields.Date.from_string('2026-11-18'))
        self.assertFalse(self._recon(second).duplicate_move_ids)

    def test_the_window_is_the_company_setting(self):
        """Requirements: BR-SPM-0015. A 12-day window flags the ten-days-apart pair."""
        self.company.spm_duplicate_bill_days = 12
        first = self._plain_bill(self.acme, 1150.0, ref='D-1', date=fields.Date.from_string('2026-11-10'))
        second = self._plain_bill(self.acme, 1150.0, ref='D-2',
                                  date=fields.Date.from_string('2026-11-20'))
        self.assertEqual(self._recon(second).duplicate_move_ids, first)

    def test_the_flag_is_cleared_only_with_a_comment(self):
        """Requirements: FR-SPM-0075. Not a duplicate needs a Finance reviewer's comment."""
        self._plain_bill(self.acme, 1150.0, ref='INV-9').action_post()
        new = self._plain_bill(self.acme, 1150.0, ref='INV-9')
        recon = self._recon(new).with_user(self.user_fin)
        with self.assertRaises(UserError):
            recon.action_spm_mark_not_duplicate('  ')
        self.assertFalse(recon.duplicate_cleared)
        recon.action_spm_mark_not_duplicate('Different delivery')
        self.assertTrue(recon.duplicate_cleared)
        self.assertEqual(recon.duplicate_cleared_by_id, self.user_fin)
        self.assertEqual(recon.duplicate_comment, 'Different delivery')


@tagged('post_install', '-at_install')
class TestPostingGuardAcceptance(SpmMatchingCommon):
    """Requirements: FR-SPM-0077, FR-SPM-0078, BR-SPM-0016"""

    def setUp(self):
        super().setUp()
        self._clear_config()
        self._policy('three_way')
        self._tolerance('price', percent=2.0, absolute=5.0)
        self._tolerance('quantity')

    def _price_bill(self):
        order = self._order(qty=100, receive=100)
        bill = self._bill(order, 100, 12.5)
        return bill, self._recon(bill).exception_ids

    def test_sc_0077_1_posting_is_refused_while_the_exception_is_open(self):
        """Requirements: FR-SPM-0077. Scenario SC-SPM-0077-1."""
        self._default_routes(approval_kinds=('price',))
        bill, exc = self._price_bill()
        self.assertEqual(exc.kind, 'price')
        with self.assertRaises(UserError) as caught:
            bill.with_user(self.user_acc).action_post()
        self.assertIn('price', str(caught.exception).lower())
        self.assertEqual(bill.state, 'draft')

    def test_posting_is_allowed_when_no_approval_is_required(self):
        """Requirements: FR-SPM-0077. The hold exists only where the route requires approval."""
        self._default_routes(approval_kinds=())
        bill, exc = self._price_bill()
        self.assertEqual(exc.kind, 'price')
        bill.with_user(self.user_acc).action_post()
        self.assertEqual(bill.state, 'posted')

    def test_sc_0077_2_accepted_exception_lets_the_bill_post(self):
        """Requirements: FR-SPM-0077. Scenario SC-SPM-0077-2."""
        self._default_routes(approval_kinds=('price',))
        bill, exc = self._price_bill()
        exc.with_user(self.user_fin).action_spm_accept('Agreed surcharge')
        self.assertEqual(exc.state, 'accepted')
        self.assertEqual(exc.decided_by_id, self.user_fin)
        self.assertEqual(exc.decision_comment, 'Agreed surcharge')
        bill.with_user(self.user_acc).action_post()
        self.assertEqual(bill.state, 'posted')

    def test_a_resolved_exception_lets_the_bill_post(self):
        """Requirements: FR-SPM-0077. Resolved also clears the hold."""
        self._default_routes(approval_kinds=('price',))
        bill, exc = self._price_bill()
        exc.with_user(self.user_fin).action_spm_resolve()
        self.assertEqual(exc.state, 'resolved')
        bill.with_user(self.user_acc).action_post()
        self.assertEqual(bill.state, 'posted')

    def test_an_exception_in_review_still_holds_the_bill(self):
        """Requirements: FR-SPM-0077. Only Accepted or Resolved release the bill."""
        self._default_routes(approval_kinds=('price',))
        bill, exc = self._price_bill()
        exc.sudo().state = 'in_review'
        with self.assertRaises(UserError):
            bill.with_user(self.user_acc).action_post()

    def test_accepting_needs_a_comment(self):
        """Requirements: FR-SPM-0077. Accept with a blank comment is refused."""
        self._default_routes(approval_kinds=('price',))
        _bill, exc = self._price_bill()
        with self.assertRaises(UserError):
            exc.with_user(self.user_fin).action_spm_accept(' ')
        self.assertEqual(exc.state, 'open')

    def test_a_procurement_manager_may_accept(self):
        """Requirements: FR-SPM-0077. Finance reviewer or Procurement manager."""
        self._default_routes(approval_kinds=('price',))
        _bill, exc = self._price_bill()
        exc.with_user(self.user_pm).action_spm_accept('OK by procurement')
        self.assertEqual(exc.state, 'accepted')

    def test_an_accountant_or_buyer_may_not_accept(self):
        """Requirements: FR-SPM-0077. No one else may accept."""
        self._default_routes(approval_kinds=('price',))
        _bill, exc = self._price_bill()
        for user in (self.user_acc, self.user_ann):
            with self.assertRaises((AccessError, UserError)):
                exc.with_user(user).action_spm_accept('Let it through')
        self.assertEqual(exc.state, 'open')

    def test_sc_0078_1_matching_and_decisions_leave_the_draft_bill_unchanged(self):
        """Requirements: FR-SPM-0078, BR-SPM-0016. Scenario SC-SPM-0078-1."""
        self._default_routes(approval_kinds=('price', 'quantity'))
        order = self._order(qty=100, receive=90)
        bill = self._bill(order, 100, 12.5)

        def snapshot():
            self.env.flush_all()
            bill.invalidate_recordset()
            return (bill.state, bill.amount_total, bill.invoice_date, bill.date, bill.name,
                    bill.write_date,
                    [(l.id, l.quantity, l.price_unit, l.price_subtotal)
                     for l in bill.invoice_line_ids.sorted('id')])

        before = snapshot()
        exceptions = self._recon(bill).exception_ids
        self.assertEqual(sorted(exceptions.mapped('kind')), ['price', 'quantity'])
        exceptions.filtered(lambda e: e.kind == 'price').with_user(
            self.user_fin).action_spm_accept('Agreed')
        exceptions.filtered(lambda e: e.kind == 'quantity').with_user(
            self.user_fin).action_spm_resolve()
        self.assertEqual(snapshot(), before)

    def test_sc_0078_2_resolving_an_exception_of_a_locked_bill_leaves_the_bill_alone(self):
        """Requirements: FR-SPM-0078, BR-SPM-0016. Scenario SC-SPM-0078-2."""
        self._default_routes(approval_kinds=())
        bill, exc = self._price_bill()
        bill.with_user(self.user_acc).action_post()
        self.company.fiscalyear_lock_date = bill.date
        self.env.flush_all()
        bill.invalidate_recordset()
        before = (bill.state, bill.amount_total, bill.date, bill.write_date)
        exc.with_user(self.user_fin).action_spm_resolve()
        self.assertEqual(exc.state, 'resolved')
        self.env.flush_all()
        bill.invalidate_recordset()
        self.assertEqual((bill.state, bill.amount_total, bill.date, bill.write_date), before)
