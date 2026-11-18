"""Unit tests of supplier documents and compliance.

Requirements: FR-SPM-0007, FR-SPM-0008
"""
from datetime import timedelta

from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger

MODULE = 'supplier_procurement_management_hub'


class TestSupplierDocument(TransactionCase):
    """Requirements: FR-SPM-0007, FR-SPM-0008"""

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        Users = self.env['res.users']
        self.buyer = Users.create({
            'name': 'Bea', 'login': 'spm_doc_buyer', 'email': 'doc_buyer@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_buyer').id])]})
        self.requester = Users.create({
            'name': 'Rex', 'login': 'spm_doc_requester', 'email': 'doc_req@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_requester').id])]})
        self.today = fields.Date.context_today(self.env['res.partner'])
        self.insurance = self.env['procurement.document.type'].create({'name': 'Insurance T4'})
        self.tax = self.env['procurement.document.type'].create({'name': 'Tax T4'})
        supplier_type = self.env['procurement.supplier.type'].create({
            'name': 'T4 type', 'required_document_type_ids': [(6, 0, [self.insurance.id, self.tax.id])]})
        self.acme = self.env['res.partner'].create({
            'name': 'Acme T4', 'is_company': True, 'spm_supplier_type_id': supplier_type.id})

    def _doc(self, doc_type, **vals):
        vals.update(partner_id=self.acme.id, document_type_id=doc_type.id)
        return self.env['procurement.supplier.document'].with_user(self.buyer).create(vals)

    def test_non_buyer_cannot_verify(self):
        """FR-SPM-0007: only a Buyer or Procurement manager changes the verification."""
        doc = self._doc(self.insurance)
        with self.assertRaises(AccessError):
            doc.with_user(self.requester).write({'verification_state': 'verified'})

    def test_summary_names_each_required_type(self):
        """FR-SPM-0008: one line per required type: expired and missing."""
        self._doc(self.insurance, verification_state='verified',
                  expiry_date=self.today - timedelta(days=1))
        summary = self.acme.with_user(self.buyer).spm_compliance_summary
        self.assertIn('Insurance T4: Expired', summary)
        self.assertIn('Tax T4: Missing', summary)

    def test_search_by_compliance_state(self):
        """FR-SPM-0008: suppliers are searchable by their compliance state."""
        found = self.env['res.partner'].search([('spm_compliance_state', '=', 'expired_or_missing')])
        self.assertIn(self.acme, found)

    def test_back_to_to_verify_clears_the_review(self):
        """FR-SPM-0007: resetting to To verify clears reviewer and date."""
        doc = self._doc(self.insurance, verification_state='verified')
        self.assertEqual(doc.reviewer_id, self.buyer)
        doc.write({'verification_state': 'to_verify'})
        self.assertFalse(doc.reviewer_id)

    def test_file_is_readable_by_another_buyer(self):
        """FR-SPM-0007: the file of a document is bound to it, so another buyer opens it."""
        other = self.env['res.users'].create({
            'name': 'Bob', 'login': 'spm_doc_buyer2', 'email': 'doc_buyer2@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_buyer').id])]})
        attachment = self.env['ir.attachment'].with_user(self.buyer).create({
            'name': 'cert.pdf', 'raw': b'pdf'})
        document = self._doc(self.insurance, attachment_id=attachment.id)
        self.assertEqual(attachment.sudo().res_id, document.id)
        self.assertEqual(attachment.with_user(other).name, 'cert.pdf')

    def test_copy_does_not_keep_the_review(self):
        """FR-SPM-0007: a copy of a verified document starts to verify, without file."""
        attachment = self.env['ir.attachment'].with_user(self.buyer).create({
            'name': 'a.pdf', 'raw': b'x'})
        document = self._doc(self.insurance, attachment_id=attachment.id)
        document.write({'verification_state': 'verified'})
        copy = document.copy()
        self.assertEqual(copy.verification_state, 'to_verify')
        self.assertFalse(copy.reviewer_id)
        self.assertFalse(copy.attachment_id)

    def test_application_with_documents_cannot_be_deleted(self):
        """FR-SPM-0007: deleting an application never deletes the supplier's documents."""
        application = self.env['procurement.supplier.application'].create({
            'email': 'app@example.com', 'legal_name': 'App T4',
            'supplier_type_id': self.acme.spm_supplier_type_id.id})
        document = self._doc(self.insurance, application_id=application.id)
        with self.assertRaises(IntegrityError), self.cr.savepoint(), mute_logger('odoo.sql_db'):
            application.unlink()
        self.assertTrue(document.exists())

    def test_foreign_loose_file_is_refused(self):
        """FR-SPM-0007: a loose file uploaded by someone else is not taken over."""
        loose = self.env['ir.attachment'].create({'name': 'draft.pdf', 'raw': b'x'})
        with self.assertRaises(AccessError):
            self._doc(self.insurance, attachment_id=loose.id)
        self.assertFalse(loose.res_id)

    def test_file_bound_elsewhere_is_refused(self):
        """FR-SPM-0007: a file bound to another record stays there."""
        bound = self.env['ir.attachment'].create({
            'name': 'b.pdf', 'raw': b'x', 'res_model': 'res.partner', 'res_id': self.acme.id})
        with self.assertRaises(AccessError):
            self._doc(self.insurance, attachment_id=bound.id)
        self.assertEqual(bound.res_model, 'res.partner')
