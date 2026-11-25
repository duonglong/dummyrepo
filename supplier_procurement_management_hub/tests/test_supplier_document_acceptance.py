"""Acceptance tests of T-004 (supplier documents), written from the specification.

Requirements: FR-SPM-0007, FR-SPM-0008
"""
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase

MODULE = 'supplier_procurement_management_hub'


class TestSupplierDocumentAcceptance(TransactionCase):
    """Requirements: FR-SPM-0007, FR-SPM-0008"""

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        self.buyer = self.env['res.users'].create({
            'name': 'Bea Buyer',
            'login': 'spm_buyer_t004',
            'email': 'spm_buyer_t004@example.com',
            'group_ids': [(6, 0, [
                self.env.ref(f'{MODULE}.group_spm_buyer').id,
                self.env.ref('base.group_partner_manager').id,
            ])],
        })
        self.today = fields.Date.context_today(self.env['res.partner'])
        DocType = self.env['procurement.document.type']
        self.insurance = DocType.create({'name': 'Insurance', 'reminder_days': 30, 'needs_expiry': True})
        self.tax = DocType.create({'name': 'Tax certificate', 'reminder_days': 30, 'needs_expiry': True})
        self.supplier_type = self.env['procurement.supplier.type'].create({
            'name': 'Goods T004',
            'required_document_type_ids': [(6, 0, [self.insurance.id, self.tax.id])],
        })
        self.acme = self.env['res.partner'].create({
            'name': 'Acme', 'is_company': True,
            'spm_supplier_type_id': self.supplier_type.id,
        })

    def _doc(self, doc_type, issue, expiry, state='to_verify', **extra):
        vals = {
            'partner_id': self.acme.id, 'document_type_id': doc_type.id,
            'issue_date': issue, 'expiry_date': expiry,
        }
        if state != 'to_verify':
            vals['verification_state'] = state
        vals.update(extra)
        return self.env['procurement.supplier.document'].with_user(self.buyer).create(vals)

    def test_sc_0007_1_buyer_verifies_a_document(self):
        """SC-SPM-0007-1: a Buyer marks Acme's insurance Verified; its status
        is Verified with the buyer's name and date."""
        doc = self._doc(self.insurance, self.today - timedelta(days=30), self.today + timedelta(days=300))
        self.assertEqual(doc.verification_state, 'to_verify')
        doc.write({'verification_state': 'verified'})
        self.env.flush_all()
        self.assertEqual(doc.verification_state, 'verified')
        self.assertEqual(doc.reviewer_id, self.buyer)
        self.assertTrue(doc.review_date)

    def test_sc_0007_2_expiry_before_issue_is_refused(self):
        """SC-SPM-0007-2: an expiry date before the issue date is refused."""
        with self.assertRaises(ValidationError) as ctx:
            self._doc(self.insurance, self.today, self.today - timedelta(days=1))
        self.assertIn('expiry', str(ctx.exception).lower())

    def test_sc_0007_2_expiry_equal_to_issue_is_accepted(self):
        """FR-SPM-0007 boundary: expiry equal to issue is not before it."""
        doc = self._doc(self.insurance, self.today, self.today)
        self.assertTrue(doc.id)

    def test_sc_0007_3_rejected_without_comment_is_refused(self):
        """SC-SPM-0007-3: marking a document Rejected without a comment is refused."""
        doc = self._doc(self.insurance, self.today - timedelta(days=30), self.today + timedelta(days=30))
        with self.assertRaises((ValidationError, UserError)) as ctx:
            doc.write({'verification_state': 'rejected'})
        self.assertIn('comment', str(ctx.exception).lower())

    def test_sc_0007_3_rejected_with_comment_is_saved(self):
        """FR-SPM-0007: a rejected document with a comment is saved."""
        doc = self._doc(self.insurance, self.today - timedelta(days=30), self.today + timedelta(days=30))
        doc.write({'verification_state': 'rejected', 'review_comment': 'Unreadable scan'})
        self.assertEqual(doc.verification_state, 'rejected')

    def test_sc_0008_1_expired_and_missing_documents_are_flagged(self):
        """SC-SPM-0008-1: Acme requires Insurance and Tax certificate; its
        verified insurance expired yesterday and it has no tax certificate.
        The insurance is expired and the supplier's compliance is flagged
        expired or missing (the per-type table of the form is built by the
        view; the model exposes the state of the document and of the supplier)."""
        doc = self._doc(self.insurance, self.today - timedelta(days=400),
                        self.today - timedelta(days=1), state='verified')
        self.assertEqual(doc.validity_state, 'expired')
        self.assertEqual(self.acme.with_user(self.buyer).spm_compliance_state, 'expired_or_missing')

    def test_sc_0008_1_all_required_documents_valid_is_compliant(self):
        """FR-SPM-0008: every required document verified and valid gives valid."""
        self._doc(self.insurance, self.today - timedelta(days=10),
                  self.today + timedelta(days=300), state='verified')
        self._doc(self.tax, self.today - timedelta(days=10),
                  self.today + timedelta(days=300), state='verified')
        self.env.flush_all()
        self.acme.invalidate_recordset()
        self.assertEqual(self.acme.with_user(self.buyer).spm_compliance_state, 'valid')

    def test_expiry_today_is_not_yet_expired(self):
        """FR-SPM-0008 boundary: expired means expiry before today."""
        doc = self._doc(self.insurance, self.today - timedelta(days=100),
                        self.today, state='verified')
        self.assertNotEqual(doc.validity_state, 'expired')

    def test_sc_0008_2_document_expiring_within_reminder_period(self):
        """SC-SPM-0008-2: verified insurance expiring in 20 days with a
        reminder period of 30 days is shown as expiring."""
        doc = self._doc(self.insurance, self.today - timedelta(days=100),
                        self.today + timedelta(days=20), state='verified')
        self.assertEqual(doc.validity_state, 'expiring')

    def test_sc_0008_2_expiry_beyond_reminder_period_is_valid(self):
        """FR-SPM-0008 boundary: expiry in 31 days with a 30 day period is valid."""
        doc = self._doc(self.insurance, self.today - timedelta(days=100),
                        self.today + timedelta(days=31), state='verified')
        self.assertEqual(doc.validity_state, 'valid')

    def test_sc_0008_2_expiry_exactly_at_reminder_period_is_expiring(self):
        """FR-SPM-0008 boundary: expiry in exactly 30 days is within the period."""
        doc = self._doc(self.insurance, self.today - timedelta(days=100),
                        self.today + timedelta(days=30), state='verified')
        self.assertEqual(doc.validity_state, 'expiring')

    def test_unverified_document_is_to_verify(self):
        """FR-SPM-0008: a document not yet verified shows to verify."""
        doc = self._doc(self.insurance, self.today - timedelta(days=10),
                        self.today + timedelta(days=300))
        self.assertEqual(doc.validity_state, 'to_verify')
