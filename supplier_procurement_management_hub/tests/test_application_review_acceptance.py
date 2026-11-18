"""Acceptance tests of T-006 (application review), written from the specification.

Requirements: FR-SPM-0004, FR-SPM-0005, FR-SPM-0006

Names used (fixed here, the design names none): on
procurement.supplier.application: action_spm_submit(), action_spm_request_changes(comment),
action_spm_approve(), action_spm_reject(reason). The reason dialog
(procurement.reason.wizard) only forwards the reason to these methods; the form
step is left to the build.
"""
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase

MODULE = 'supplier_procurement_management_hub'


class TestApplicationReviewAcceptance(TransactionCase):
    """Requirements: FR-SPM-0004, FR-SPM-0005, FR-SPM-0006"""

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        partner_manager = self.env.ref('base.group_partner_manager').id
        self.buyer = self.env['res.users'].create({
            'name': 'Bea Buyer', 'login': 'spm_buyer_t006', 'email': 'spm_buyer_t006@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_buyer').id, partner_manager])],
        })
        self.manager = self.env['res.users'].create({
            'name': 'Mia Manager', 'login': 'spm_manager_t006', 'email': 'spm_manager_t006@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_manager').id, partner_manager])],
        })
        self.goods = self.env['procurement.supplier.type'].create({'name': 'Goods T006'})

    def _submitted(self, legal_name='Acme Ltd', vat='BE0123456789'):
        application = self.env['procurement.supplier.application'].with_user(self.buyer).create({
            'email': 'sales@acme.example', 'legal_name': legal_name, 'vat': vat,
            'supplier_type_id': self.goods.id, 'user_id': self.buyer.id,
        })
        application.with_user(self.buyer).action_spm_submit()
        self.assertEqual(application.state, 'submitted')
        return application

    def test_sc_0004_1_buyer_requests_changes_with_a_comment(self):
        """SC-SPM-0004-1: a Buyer requests changes with a comment; the
        application is Changes requested, the supplier is notified with that
        comment and can edit and submit it again."""
        comment = 'Insurance certificate is unreadable'
        application = self._submitted()
        application.with_user(self.buyer).action_spm_request_changes(comment)
        self.env.flush_all()
        self.assertEqual(application.state, 'changes_requested')
        self.assertEqual(application.change_request_comment, comment)
        mails = self.env['mail.mail'].sudo().search([
            ('email_to', 'ilike', 'sales@acme.example'), ('body_html', 'ilike', comment)])
        self.assertTrue(mails)
        application.with_user(self.buyer).write({'street': 'Main Street 1'})
        application.with_user(self.buyer).action_spm_submit()
        self.assertEqual(application.state, 'submitted')

    def test_sc_0004_2_request_without_a_comment_is_refused(self):
        """SC-SPM-0004-2: requesting changes without a comment is refused with
        a message that a comment is required; a blank comment is no comment."""
        application = self._submitted()
        for comment in (None, '', '   '):
            with self.assertRaises((UserError, ValidationError)) as ctx:
                application.with_user(self.buyer).action_spm_request_changes(comment)
            self.assertIn('comment', str(ctx.exception).lower())
        self.assertEqual(application.state, 'submitted')

    def test_sc_0005_1_approval_creates_one_shared_supplier_contact(self):
        """SC-SPM-0005-1: no contact has tax identifier BE0123456789; a
        Procurement manager approves; a company contact 'Acme Ltd' with no
        company is created, marked as a supplier, with the approved status."""
        Partner = self.env['res.partner']
        self.assertFalse(Partner.search([('vat', '=', 'BE0123456789')]))
        application = self._submitted()
        application.with_user(self.manager).action_spm_approve()
        self.env.flush_all()
        self.assertEqual(application.state, 'approved')
        partner = application.partner_id
        self.assertEqual(partner.name, 'Acme Ltd')
        self.assertTrue(partner.is_company)
        self.assertFalse(partner.company_id)
        self.assertTrue(partner.spm_is_supplier)
        self.assertEqual(partner.vat, 'BE0123456789')
        approved = self.env['procurement.supplier.status'].search(
            [('kind', '=', 'approved'), ('default_for_kind', '=', True)])
        self.assertEqual(partner.spm_status_id, approved)

    def test_sc_0005_2_approval_links_the_existing_contact_with_the_same_tax_id(self):
        """SC-SPM-0005-2: an existing contact 'ACME Limited' has the same tax
        identifier; approval links the application to it and creates no contact."""
        existing = self.env['res.partner'].create({
            'name': 'ACME Limited', 'is_company': True, 'vat': 'BE0123456789'})
        application = self._submitted()
        before = self.env['res.partner'].search_count([])
        application.with_user(self.manager).action_spm_approve()
        self.env.flush_all()
        self.assertEqual(application.partner_id, existing)
        self.assertEqual(self.env['res.partner'].search_count([]), before)
        self.assertTrue(existing.spm_is_supplier)

    def test_sc_0005_2_matching_ignores_case_and_spaces_of_the_tax_id(self):
        """FR-SPM-0005 / design: the tax identifier is compared stripped and
        case-insensitively."""
        existing = self.env['res.partner'].create({
            'name': 'ACME Limited', 'is_company': True, 'vat': 'be0123456789 '})
        application = self._submitted()
        application.with_user(self.manager).action_spm_approve()
        self.assertEqual(application.partner_id, existing)

    def test_sc_0005_3_a_buyer_cannot_approve(self):
        """SC-SPM-0005-3: a Buyer without the Procurement manager role who
        tries to approve gets an access error."""
        application = self._submitted()
        with self.assertRaises(AccessError):
            application.with_user(self.buyer).action_spm_approve()
        self.assertEqual(application.state, 'submitted')

    def test_sc_0006_1_rejection_with_a_reason(self):
        """SC-SPM-0006-1: a Procurement manager rejects with a reason; the
        application is Rejected and read-only, an audit entry holds the reason
        and the supplier receives a rejection e-mail."""
        application = self._submitted()
        application.with_user(self.manager).action_spm_reject('No ISO certificate')
        self.env.flush_all()
        self.assertEqual(application.state, 'rejected')
        self.assertEqual(application.rejection_reason, 'No ISO certificate')
        with self.assertRaises((UserError, AccessError, ValidationError)):
            application.with_user(self.manager).write({'legal_name': 'Changed'})
        audit = self.env['procurement.audit.log'].search([
            ('res_model', '=', 'procurement.supplier.application'),
            ('res_id', '=', application.id)])
        self.assertIn('No ISO certificate', ' '.join(
            [e.comment or '' for e in audit] + [str(e.new_values) for e in audit]))
        mails = self.env['mail.mail'].sudo().search([('email_to', 'ilike', 'sales@acme.example')])
        self.assertTrue(mails)

    def test_sc_0006_1_supplier_message_does_not_quote_the_internal_reason(self):
        """FR-SPM-0006: the message to the supplier does not quote internal notes."""
        application = self._submitted()
        application.with_user(self.manager).action_spm_reject('Internal: owner is on a watch list')
        mails = self.env['mail.mail'].sudo().search([('email_to', 'ilike', 'sales@acme.example')])
        self.assertTrue(mails)
        self.assertNotIn('watch list', ' '.join(mails.mapped(lambda m: m.body_html or '')))

    def test_sc_0006_2_rejection_without_a_reason_is_refused(self):
        """SC-SPM-0006-2: rejecting with no reason (or a blank one) is refused
        with a message that a reason is required."""
        application = self._submitted()
        for reason in (None, '', '  '):
            with self.assertRaises((UserError, ValidationError)) as ctx:
                application.with_user(self.manager).action_spm_reject(reason)
            self.assertIn('reason', str(ctx.exception).lower())
        self.assertEqual(application.state, 'submitted')
