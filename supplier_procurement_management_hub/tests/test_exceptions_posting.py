"""Own tests of T-026 (exception life cycle, direct-write guards, dialogs).

Requirements: FR-SPM-0075, FR-SPM-0076, FR-SPM-0077, FR-SPM-0078
"""
from unittest.mock import patch

from lxml import etree

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, tagged

from ..models.procurement_exception_route import EXCEPTION_KINDS
from ..services import reconciliation as reconciliation_service

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
        self.assertIn('Agreed', self._decision_history(self.exc))
        with self.assertRaises(UserError):
            self.bill.with_user(self.user_acc).action_post()

    def test_bug_b_0279_the_matching_says_why_the_bill_cannot_be_posted(self):
        """Regression test for B-0279: the matching record names the exceptions that hold the posting, and
        says nothing once they are released.

        Requirements: FR-SPM-0077
        """
        recon = self._recon(self.bill).with_user(self.user_fin)
        self.assertIn(dict(EXCEPTION_KINDS)['price'], recon.posting_block_reason)
        self.exc.with_user(self.user_fin).action_spm_accept('Agreed')
        recon.invalidate_recordset()
        self.assertFalse(recon.posting_block_reason)

    def test_bug_b_0279_the_reason_is_the_one_the_posting_refusal_gives(self):
        """Regression test for B-0279: the posting is refused with the same lines the matching shows.

        Requirements: FR-SPM-0077
        """
        recon = self._recon(self.bill).with_user(self.user_fin)
        with self.assertRaises(UserError) as refusal:
            self.bill.with_user(self.user_acc).action_post()
        self.assertIn(recon.posting_block_reason, str(refusal.exception))

    def test_bug_b_0279_the_matching_form_shows_the_reason_and_computed_lines_cannot_be_added(self):
        """Regression test for B-0279: the form carries the reason, and the computed lines offer no add or delete.

        Requirements: FR-SPM-0071, FR-SPM-0077
        It cannot reach the rendered form: it reads the form the server sends to the finance reviewer.
        """
        view = self.env['procurement.reconciliation'].with_user(self.user_fin).get_view(
            self.env.ref('supplier_procurement_management_hub.procurement_reconciliation_view_form').id, 'form')
        arch = etree.fromstring(view['arch'])
        self.assertTrue(arch.xpath("//field[@name='posting_block_reason']"))
        lines = arch.xpath("//field[@name='line_ids']/list")[0]
        self.assertEqual((lines.get('create'), lines.get('delete')), ('0', '0'))

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
        # Findings S11 and Q17: the reviewer owns the cleared exception and the decision is in the trail
        self.assertEqual(recon.exception_ids.owner_id, self.user_fin)
        self.assertEqual(self._audit_entries(recon.exception_ids).comment, 'Different order')
        dup.write({'ref': 'INV-Z'})
        self.assertFalse(dup.spm_possible_duplicate_ids)

    def test_clearing_a_duplicate_is_audited_even_when_its_exception_was_accepted(self):
        """Requirements: FR-SPM-0075, BR-SPM-0018. Finding Q20: the clearing is on the matching's own trail."""
        self._plain_bill(self.acme, 1160.0, ref='INV-Y').action_post()
        dup = self._plain_bill(self.acme, 1160.0, ref='INV-Y')
        recon = self._recon(dup)
        recon.exception_ids.with_user(self.user_fin).action_spm_accept('Known')
        recon.with_user(self.user_fin).action_spm_mark_not_duplicate('Other order')
        entries = self._audit_entries(recon)
        self.assertEqual(entries.comment, 'Other order')
        self.assertEqual(entries.user_id, self.user_fin)

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


    def _decision_history(self, exception):
        # sudo: the test reads the whole chatter, as an auditor would
        return ' '.join(exception.sudo().message_ids.mapped('body'))

    def test_a_rejected_overcharge_that_comes_back_holds_the_bill(self):
        """Requirements: FR-SPM-0077. Finding Q10: a system resolve is not a person's release."""
        self.exc.with_user(self.user_fin).action_spm_reject('Not agreed')
        self.bill.invoice_line_ids.price_unit = 11.5
        self.env.flush_all()
        self.assertEqual(self.exc.state, 'resolved')
        self.bill.invoice_line_ids.price_unit = 12.5
        self.env.flush_all()
        self.assertEqual(self.exc.state, 'open')
        with self.assertRaises(UserError):
            self.bill.with_user(self.user_acc).action_post()

    def test_the_resolve_dialog_requires_a_comment_and_stores_it(self):
        """Requirements: FR-SPM-0077. Finding S7: the dialog is filled and saved as a person does;
        its Confirm button stays for the screen review."""
        action = self.exc.with_user(self.user_fin).action_spm_open_resolve_wizard()
        wizards = self.env['procurement.reason.wizard'].with_user(self.user_fin).with_context(**action['context'])
        blank = Form(wizards)
        blank.reason = '   '
        with self.assertRaises(UserError):
            blank.save().action_confirm()
        self.assertEqual(self.exc.state, 'open')
        dialog = Form(wizards)
        dialog.reason = 'Credit note CN/12 received'
        dialog.save().action_confirm()
        self.assertEqual(self.exc.state, 'resolved')
        self.assertEqual(self.exc.decision_comment, 'Credit note CN/12 received')

    def _audit_entries(self, exception):
        # sudo: the test reads the protected trail as an auditor would
        return self.env['procurement.audit.log'].sudo().search([
            ('event', '=', 'exception'), ('res_model', '=', exception._name), ('res_id', '=', exception.id)])

    def test_a_decision_is_written_to_the_audit_trail_and_survives_the_bill(self):
        """Requirements: FR-SPM-0077, BR-SPM-0018. Finding Q17: accept, the exception is deleted with its bill."""
        self.exc.with_user(self.user_fin).action_spm_accept('Agreed surcharge')
        exc_id = self.exc.id
        entries = self.env['procurement.audit.log'].sudo().search([
            ('event', '=', 'exception'), ('res_id', '=', exc_id)])
        self.assertEqual(entries.user_id, self.user_fin)
        self.assertEqual(entries.comment, 'Agreed surcharge')
        self.assertEqual(entries.new_values['state'], 'accepted')
        # the exception goes the way a deleted draft bill takes it (cascade); the trail is not linked to it
        self.exc.sudo().unlink()
        self.env.flush_all()
        self.assertEqual(self.env['procurement.audit.log'].sudo().search_count([
            ('event', '=', 'exception'), ('res_id', '=', exc_id)]), 1)

    def test_every_path_that_changes_a_decision_writes_the_audit_trail(self):
        """Requirements: FR-SPM-0077, BR-SPM-0018. Finding Q17 siblings: review, system resolve, reopen."""
        self.exc.with_user(self.user_fin).action_spm_review()
        self.assertEqual(len(self._audit_entries(self.exc)), 1)
        self.bill.invoice_line_ids.price_unit = 11.5
        self.env.flush_all()
        self.assertEqual(self.exc.state, 'resolved')
        self.assertEqual(len(self._audit_entries(self.exc)), 2)
        self.bill.invoice_line_ids.price_unit = 12.5
        self.env.flush_all()
        self.assertEqual(self.exc.state, 'open')
        self.assertEqual(len(self._audit_entries(self.exc)), 3)
        self.assertFalse(self.exc.decision_comment)

    def test_a_later_decision_keeps_the_earlier_one_in_the_history(self):
        """Requirements: FR-SPM-0077. Finding Q11, path 1: reject, then resolve."""
        self.exc.with_user(self.user_fin).action_spm_reject('Not agreed')
        self.env.flush_all()
        self.exc.with_user(self.user_pm).action_spm_resolve('Credit note')
        self.env.flush_all()
        self.assertIn('Not agreed', self._decision_history(self.exc))

    def test_removing_the_order_lines_keeps_decided_exceptions(self):
        """Requirements: FR-SPM-0076. Finding Q11, path 4: the matching is not deleted."""
        self.exc.with_user(self.user_fin).action_spm_accept('Agreed surcharge')
        self.env.flush_all()
        self.bill.invoice_line_ids.purchase_line_id = False
        self.env.flush_all()
        self.assertTrue(self.exc.exists())
        self.assertEqual((self.exc.state, self.exc.decision_comment), ('accepted', 'Agreed surcharge'))

    def test_a_system_resolve_keeps_the_rejection_in_the_history(self):
        """Requirements: FR-SPM-0077. Finding Q11, paths 2 and 3."""
        self.exc.with_user(self.user_fin).action_spm_reject('Not agreed')
        self.bill.invoice_line_ids.price_unit = 11.5
        self.env.flush_all()
        self.assertIn('Not agreed', self._decision_history(self.exc))

    def test_a_finance_route_without_a_person_notifies_every_finance_reviewer(self):
        """Requirements: FR-SPM-0076. Finding S6: the reviewer who starts the review owns it."""
        self._plain_bill(self.acme, 1150.0, ref='INV-F').action_post()
        exc = self._recon(self._plain_bill(self.acme, 1150.0, ref='INV-F')).exception_ids
        self.assertFalse(exc.owner_id)
        self.assertIn(self.user_fin.partner_id, exc.message_partner_ids)
        self.assertNotIn(self.user_ann.partner_id, exc.message_partner_ids)
        exc.with_user(self.user_fin).action_spm_review()
        self.assertEqual(exc.owner_id, self.user_fin)

    def test_the_decider_owns_an_exception_without_owner(self):
        """Requirements: FR-SPM-0076. Finding S6: deciding takes ownership."""
        self._plain_bill(self.acme, 1150.0, ref='INV-G').action_post()
        exc = self._recon(self._plain_bill(self.acme, 1150.0, ref='INV-G')).exception_ids
        exc.with_user(self.user_pm).action_spm_accept('Known repeat')
        self.assertEqual(exc.owner_id, self.user_pm)

    def test_a_reviewer_cannot_decide_for_another_company(self):
        """Requirements: FR-SPM-0075, FR-SPM-0077. Finding Q12: the company rule applies."""
        data = self.setup_other_company(name='Company B M5')
        moves = self.env['account.move'].with_company(data['company'])

        def bill_b():
            return moves.create({
                'move_type': 'in_invoice', 'partner_id': self.acme.id, 'ref': 'INV-B2',
                'invoice_date': fields.Date.from_string('2026-11-10'),
                'invoice_line_ids': [(0, 0, {
                    'name': 'Service', 'quantity': 1, 'price_unit': 90.0,
                    'account_id': data['default_account_expense'].id, 'tax_ids': [(6, 0, [])]})],
            })
        bill_b().action_post()
        recon = self._recon(bill_b())
        self.assertTrue(recon.duplicate_move_ids)
        with self.assertRaises(AccessError):
            recon.with_user(self.user_fin).action_spm_mark_not_duplicate('Not ours')
        with self.assertRaises(AccessError):
            recon.exception_ids.with_user(self.user_fin).action_spm_accept('Not ours')
        self.assertTrue(recon.duplicate_move_ids)
        self.assertEqual(recon.exception_ids.state, 'open')

    def test_a_bill_held_by_a_rejection_stays_to_review(self):
        """Requirements: FR-SPM-0077. Finding Q13."""
        self.exc.with_user(self.user_fin).action_spm_reject('Not agreed')
        self.bill.action_spm_run_matching()
        self.assertEqual(self._recon(self.bill).state, 'review')
        self.assertEqual(self.bill.spm_open_exception_count, 1)


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
        """Requirements: FR-SPM-0076. Finding Q16: a real matching error, then a good run."""
        bill = self._bill(self._order(qty=10, price=1.10), 13, 1.10)
        recon = self._recon(bill)
        before = recon.exception_ids
        before.with_user(self.user_fin).action_spm_accept('Agreed')
        with patch.object(reconciliation_service, '_line_values', side_effect=ValueError('boom')):
            bill.action_spm_run_matching()
        self.assertEqual(recon.state, 'error')
        bill.action_spm_run_matching()
        self.assertEqual(recon.exception_ids, before)
        self.assertEqual(before.state, 'accepted')

    def test_quantity_labels_carry_no_float_noise(self):
        """Requirements: FR-SPM-0076. Finding Q15."""
        bill = self._bill(self._order(qty=10, price=1.10), 13, 1.10)
        exc = self._recon(bill).exception_ids.filtered(lambda e: e.kind == 'quantity')
        self.assertEqual((exc.expected_value, exc.found_value), ('10.00', '13.00'))
