from odoo import Command, fields
from odoo.tests import tagged

from .common import IprCommon


@tagged('post_install', '-at_install')
class TestDuplicateAcceptance(IprCommon):
    """Acceptance tests of T-005: the possible duplicate vendor bill exception.

    Requirements: BR-IPR-0002, FR-IPR-0009, FR-IPR-0010.
    The check is run by its method (action_ipr_check); the Run check button is left to the build.
    """

    LINE = {'name': 'Service', 'quantity': 1, 'price_unit': 5.0}

    def _check(self, bill):
        bill.with_user(self.accountant).action_ipr_check()
        self.env.flush_all()
        self.env.invalidate_all()
        return bill

    def _plain_bill(self, partner, ref):
        return self.make_bill(partner, [dict(self.LINE)], ref=ref)

    def _posted(self, partner, ref):
        bill = self._plain_bill(partner, ref)
        bill.action_post()
        return bill

    def _duplicates(self, bill):
        return bill.ipr_exception_ids.filtered(lambda e: e.kind == 'duplicate')

    # SC-IPR-0009-1
    def test_same_ref_case_and_spaces(self):
        """SC-IPR-0009-1: posted bill from Acme ref INV-778; draft ' inv-778' gets a duplicate exception naming it."""
        posted = self._posted(self.supplier_acme, 'INV-778')
        draft = self._plain_bill(self.supplier_acme, ' inv-778')
        self._check(draft)
        dup = self._duplicates(draft)
        self.assertEqual(len(dup), 1)
        self.assertEqual(dup.duplicate_move_ids, posted)
        self.assertEqual(draft.ipr_state, 'waiting')

    def test_every_matching_bill_named(self):
        """FR-IPR-0009: the exception names each matching bill."""
        first = self._posted(self.supplier_acme, 'INV-778')
        second = self._plain_bill(self.supplier_acme, 'INV-778 ')
        draft = self._plain_bill(self.supplier_acme, 'inv-778')
        self._check(draft)
        self.assertEqual(self._duplicates(draft).duplicate_move_ids, first | second)

    def test_rerun_keeps_one_exception(self):
        """FR-IPR-0009: running the check again does not pile up duplicate exceptions."""
        self._posted(self.supplier_acme, 'INV-778')
        draft = self._plain_bill(self.supplier_acme, 'INV-778')
        self._check(draft)
        self._check(draft)
        self.assertEqual(len(self._duplicates(draft)), 1)

    def test_bill_is_not_its_own_duplicate(self):
        """BR-IPR-0002: a lone bill with a reference is not a duplicate of itself."""
        draft = self._plain_bill(self.supplier_acme, 'INV-778')
        self._check(draft)
        self.assertFalse(self._duplicates(draft))
        self.assertEqual(draft.ipr_state, 'matched')

    # SC-IPR-0009-2
    def test_cancelled_and_other_supplier(self):
        """SC-IPR-0009-2: a cancelled Acme bill and a Beta bill with the same reference are not duplicates."""
        cancelled = self._plain_bill(self.supplier_acme, 'INV-778')
        cancelled.button_cancel()
        self.assertEqual(cancelled.state, 'cancel')
        self._plain_bill(self.supplier_beta, 'INV-778')
        draft = self._plain_bill(self.supplier_acme, 'INV-778')
        self._check(draft)
        self.assertFalse(self._duplicates(draft))
        self.assertEqual(draft.ipr_state, 'matched')

    # SC-IPR-0009-3
    def test_no_reference(self):
        """SC-IPR-0009-3: bills without a reference are never flagged."""
        self._posted(self.supplier_acme, False)
        draft = self._plain_bill(self.supplier_acme, False)
        self._check(draft)
        self.assertFalse(self._duplicates(draft))

    def test_blank_reference(self):
        """BR-IPR-0002: a reference of spaces only counts as no reference."""
        self._plain_bill(self.supplier_acme, '   ')
        draft = self._plain_bill(self.supplier_acme, '  ')
        self._check(draft)
        self.assertFalse(self._duplicates(draft))

    def test_contact_person_counts(self):
        """BR-IPR-0002: a bill entered on a contact of Acme is caught as the same supplier."""
        contact = self.supplier_acme.child_ids[:1]
        self.assertTrue(contact)
        posted = self._posted(self.supplier_acme, 'INV-778')
        draft = self._plain_bill(contact, 'INV-778')
        self._check(draft)
        self.assertEqual(self._duplicates(draft).duplicate_move_ids, posted)

    def test_other_company_ignored(self):
        """BR-IPR-0002: a bill of another company with the same supplier and reference is not a duplicate."""
        self.env['account.move'].with_company(self.company_b).create({
            'move_type': 'in_invoice', 'partner_id': self.supplier_acme.id, 'ref': 'INV-778',
            'invoice_date': fields.Date.today(), 'company_id': self.company_b.id,
            'invoice_line_ids': [Command.create(dict(self.LINE))],
        })
        draft = self._plain_bill(self.supplier_acme, 'INV-778')
        self._check(draft)
        self.assertFalse(self._duplicates(draft))

    def test_credit_note_not_a_duplicate(self):
        """BR-IPR-0002: only vendor bills are compared; a credit note with the same reference is ignored."""
        self.env['account.move'].with_company(self.company_a).create({
            'move_type': 'in_refund', 'partner_id': self.supplier_acme.id, 'ref': 'INV-778',
            'invoice_date': fields.Date.today(), 'company_id': self.company_a.id,
            'invoice_line_ids': [Command.create(dict(self.LINE))],
        })
        draft = self._plain_bill(self.supplier_acme, 'INV-778')
        self._check(draft)
        self.assertFalse(self._duplicates(draft))

    def test_history_names_duplicate(self):
        """FR-IPR-0009 / FR-IPR-0020: the check entry counts the duplicate exception."""
        self._posted(self.supplier_acme, 'INV-778')
        draft = self._plain_bill(self.supplier_acme, 'INV-778')
        self._check(draft)
        entry = draft.ipr_history_ids
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry.exception_count, 1)
        self.assertEqual(entry.state_after, 'waiting')

    # SC-IPR-0010-1
    def test_two_exceptions_waiting(self):
        """SC-IPR-0010-1: one price exception and one duplicate: status Waiting for approval, two exceptions listed."""
        self._posted(self.supplier_acme, 'INV-778')
        order = self.make_order(100, 10.0, received=100)
        draft = self.make_bill_from_order(order, 100, 11.0, ref='INV-778')
        self._check(draft)
        self.assertEqual(draft.ipr_state, 'waiting')
        self.assertEqual(len(draft.ipr_exception_ids), 2)
        self.assertEqual(
            set(draft.ipr_exception_ids.mapped('kind')), {'price', 'duplicate'})
        self.assertEqual(draft.ipr_exception_count, 2)
        self.assertEqual(draft.ipr_history_ids.exception_count, 2)

    def test_duplicate_index_exists(self):
        """T-005: an expression index on lower(btrim(ref)) of vendor bills exists."""
        self.env.cr.execute("""
            SELECT indexdef FROM pg_indexes
             WHERE tablename = 'account_move' AND indexdef ILIKE '%%lower%%'
               AND indexdef ILIKE '%%commercial_partner_id%%'
               AND indexdef ILIKE '%%in_invoice%%'""")
        self.assertTrue(self.env.cr.fetchall())


@tagged('post_install', '-at_install')
class TestNormalizeRefAcceptance(IprCommon):
    """Acceptance tests of T-005 for the reference normalisation.

    Requirements: BR-IPR-0002.
    """

    def test_normalize_ref(self):
        """BR-IPR-0002: lower case, no surrounding spaces, nothing for no reference."""
        from odoo.addons.invoice_purchase_reconciliation.services import reconciliation
        self.assertEqual(reconciliation.normalize_ref(' inv-778'), 'inv-778')
        self.assertEqual(reconciliation.normalize_ref('INV-778 '), 'inv-778')
        self.assertEqual(reconciliation.normalize_ref(None), '')
        self.assertEqual(reconciliation.normalize_ref(False), '')
        self.assertEqual(reconciliation.normalize_ref('   '), '')
