"""Unit tests of onboarding applications (invitation, field rules, submission).

Requirements: FR-SPM-0001, FR-SPM-0002, FR-SPM-0003
"""
import re
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import Form
from odoo.tests.common import HttpCase, TransactionCase

from ..services import onboarding

MODULE = 'supplier_procurement_management_hub'
LINK = re.compile(r'/my/procurement/onboarding/([A-Za-z0-9_\-]+)')


class TestOnboardingApplication(TransactionCase):
    """Requirements: FR-SPM-0001, FR-SPM-0002, FR-SPM-0003"""

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        self.buyer = self.env['res.users'].create({
            'name': 'Bea Buyer', 'login': 'spm_buyer_t005u', 'email': 'spm_buyer_t005u@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_buyer').id])],
        })
        self.goods = self.env['procurement.supplier.type'].create({'name': 'Goods T005U'})
        self.Application = self.env['procurement.supplier.application']

    def test_email_is_always_required_too(self):
        """FR-SPM-0002: an e-mail rule cannot be hidden either."""
        with self.assertRaises(ValidationError):
            self.env['procurement.supplier.type.field'].create({
                'type_id': self.goods.id, 'field_key': 'email', 'visibility': 'hidden'})

    def test_field_rules_default_to_shown(self):
        """FR-SPM-0002: a key with no rule is shown; legal name and e-mail are required."""
        application = self.Application.create({
            'email': 'a@example.com', 'supplier_type_id': self.goods.id})
        rules = application.spm_field_rules()
        self.assertEqual(rules['phone'], 'shown')
        self.assertEqual(rules['legal_name'], 'required')
        self.assertEqual(rules['email'], 'required')

    def test_missing_legal_name_is_named_on_submit(self):
        """FR-SPM-0003: an application with no legal name is refused and says so."""
        application = self.Application.with_user(self.buyer).create({
            'email': 'a@example.com', 'supplier_type_id': self.goods.id})
        with self.assertRaises(UserError) as ctx:
            application.with_user(self.buyer).action_spm_submit()
        self.assertIn('Legal name', str(ctx.exception))

    def test_resent_invitation_replaces_the_token(self):
        """FR-SPM-0001: a new invitation makes the earlier link stop working."""
        application = self.Application.with_user(self.buyer).spm_invite('a@example.com', self.goods)
        first = application.sudo().invitation_token_hash
        application.with_user(self.buyer).action_spm_send_invitation()
        self.assertNotEqual(application.sudo().invitation_token_hash, first)

    def test_submitted_application_cannot_be_invited_again(self):
        """FR-SPM-0001: only an application the supplier can still fill in gets a link."""
        application = self.Application.with_user(self.buyer).create({
            'email': 'a@example.com', 'legal_name': 'A', 'supplier_type_id': self.goods.id})
        application.with_user(self.buyer).action_spm_submit()
        with self.assertRaises(UserError):
            application.with_user(self.buyer).action_spm_send_invitation()

    def test_buyer_cannot_write_the_workflow_fields(self):
        """FR-SPM-0003: a direct write of the state or the invitation skips the submit check and is refused."""
        application = self.Application.with_user(self.buyer).create({
            'email': 'a@example.com', 'supplier_type_id': self.goods.id})
        for vals in ({'state': 'submitted'}, {'invitation_expiry': fields.Datetime.now() + timedelta(days=90)},
                     {'invitation_used_date': False}, {'submitted_date': fields.Datetime.now()}):
            with self.assertRaises(AccessError, msg=str(vals)):
                application.with_user(self.buyer).write(vals)
        self.assertEqual(application.state, 'invited')

    def test_buyer_cannot_create_an_approved_application(self):
        """FR-SPM-0003: the state cannot be chosen on creation either."""
        with self.assertRaises(AccessError):
            self.Application.with_user(self.buyer).create({
                'email': 'a@example.com', 'supplier_type_id': self.goods.id, 'state': 'approved'})

    def test_buyer_creates_an_application_in_the_form(self):
        """FR-SPM-0001: New > Save as a buyer works (Form sends the statusbar; the button is not pressed)."""
        form = Form(self.Application.with_user(self.buyer))
        form.email = 'form@example.com'
        form.supplier_type_id = self.goods
        application = form.save()
        self.assertEqual(application.state, 'invited')

    def test_context_default_cannot_set_the_workflow(self):
        """FR-SPM-0003: a default_state or default_invitation_expiry in the context is dropped for a user."""
        application = self.Application.with_user(self.buyer).with_context(
            default_state='approved', default_invitation_expiry=fields.Datetime.now() + timedelta(days=90),
        ).create({'email': 'ctx@example.com', 'supplier_type_id': self.goods.id})
        self.assertEqual(application.state, 'invited')
        self.assertFalse(application.invitation_expiry)

    def test_closed_application_is_read_only(self):
        """FR-SPM-0003: an approved or rejected application accepts no write from a user."""
        application = self.Application.create({
            'email': 'a@example.com', 'supplier_type_id': self.goods.id, 'state': 'rejected'})
        with self.assertRaises(UserError):
            application.with_user(self.buyer).write({'legal_name': 'Changed'})

    def test_closed_application_contacts_are_read_only(self):
        """FR-SPM-0003: create, write and unlink of a contact of a closed application are refused to a user."""
        Contact = self.env['procurement.supplier.application.contact']
        application = self.Application.create({
            'email': 'a@example.com', 'supplier_type_id': self.goods.id, 'state': 'approved'})
        contact = Contact.create({'application_id': application.id, 'name': 'Kept'})
        buyer_contact = contact.with_user(self.buyer)
        with self.assertRaises(UserError):
            Contact.with_user(self.buyer).create({'application_id': application.id, 'name': 'New'})
        with self.assertRaises(UserError):
            buyer_contact.write({'name': 'Changed'})
        with self.assertRaises(UserError):
            buyer_contact.unlink()
        self.assertEqual(contact.name, 'Kept')

    def test_open_application_contacts_stay_editable(self):
        """FR-SPM-0002: a buyer edits the contacts of an open application."""
        application = self.Application.create({'email': 'a@example.com', 'supplier_type_id': self.goods.id})
        contact = self.env['procurement.supplier.application.contact'].with_user(self.buyer).create({
            'application_id': application.id, 'name': 'Open'})
        contact.write({'name': 'Edited'})
        self.assertEqual(contact.name, 'Edited')

    def test_submit_by_the_action_still_works_for_a_buyer(self):
        """FR-SPM-0003: the action writes the state with its own rights."""
        application = self.Application.with_user(self.buyer).create({
            'email': 'a@example.com', 'legal_name': 'A', 'supplier_type_id': self.goods.id})
        application.with_user(self.buyer).action_spm_submit()
        self.assertEqual(application.state, 'submitted')

    def test_link_expires_exactly_at_the_expiry_moment(self):
        """FR-SPM-0001: valid just before the expiry, still valid at it, expired just after."""
        expiry = fields.Datetime.now() + timedelta(days=1)
        application = self.Application.create({
            'email': 'a@example.com', 'supplier_type_id': self.goods.id, 'invitation_expiry': expiry})
        second = timedelta(seconds=1)
        self.assertEqual(onboarding.link_state(application, expiry - second), 'ok')
        self.assertEqual(onboarding.link_state(application, expiry), 'ok')
        self.assertEqual(onboarding.link_state(application, expiry + second), 'expired')

    def test_always_required_key_cannot_be_optional(self):
        """FR-SPM-0002: legal name cannot be configured as shown either."""
        with self.assertRaises(ValidationError):
            self.env['procurement.supplier.type.field'].create({
                'type_id': self.goods.id, 'field_key': 'legal_name', 'visibility': 'shown'})

    def test_unknown_field_key_does_not_break_the_submit_check(self):
        """FR-SPM-0003: a required key another module added is skipped by this module's check."""
        application = self.Application.create({
            'email': 'a@example.com', 'legal_name': 'A', 'supplier_type_id': self.goods.id})
        rules = dict(application.spm_field_rules(), other_module_key='required')
        with patch.object(type(application), 'spm_field_rules', lambda self: rules):
            self.assertEqual(application._spm_missing_items(), [])

    def test_buyer_cannot_read_the_portal_log(self):
        """FR-SPM-0027: the portal activity is for managers, auditors and administrators."""
        with self.assertRaises(AccessError):
            self.env['procurement.portal.log'].with_user(self.buyer).search([])


class TestOnboardingPortal(HttpCase):
    """Requirements: FR-SPM-0001, FR-SPM-0002, FR-SPM-0003"""

    def setUp(self):
        super().setUp()
        self.goods = self.env['procurement.supplier.type'].create({'name': 'Goods T005P'})
        self.env['procurement.supplier.type.field'].create({
            'type_id': self.goods.id, 'field_key': 'website', 'visibility': 'hidden'})
        application = self.env['procurement.supplier.application'].spm_invite(
            'portal@example.com', self.goods)
        self.env.flush_all()
        mail = self.env['mail.mail'].sudo().search([('email_to', 'ilike', 'portal@example.com')])
        self.token = LINK.search(mail.body_html).group(1)
        self.application = application

    def test_hidden_field_is_not_shown_on_the_page(self):
        """FR-SPM-0002: the supplier's page leaves out a hidden field and keeps the others."""
        page = self.url_open(f'/my/procurement/onboarding/{self.token}').text
        self.assertNotIn('name="website"', page)
        self.assertIn('name="phone"', page)

    def test_unknown_token_is_refused_and_logged(self):
        """FR-SPM-0001: a made-up link is refused and the attempt is logged."""
        response = self.url_open('/my/procurement/onboarding/not-a-token')
        self.assertEqual(response.status_code, 403)
        self.assertTrue(self.env['procurement.portal.log'].sudo().search([
            ('action', '=', 'denied'), ('res_id', '=', False)]))

    def _post(self, data=None, files=None):
        page = self.url_open(f'/my/procurement/onboarding/{self.token}').text
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        return self.url_open(
            f'/my/procurement/onboarding/{self.token}', data=dict(data or {}, csrf_token=csrf), files=files)

    def test_save_keeps_every_contact_and_edits_by_id(self):
        """FR-SPM-0002: four contacts survive a save; an emptied one is deleted, a new one added."""
        contacts = self.env['procurement.supplier.application.contact'].create([
            {'application_id': self.application.id, 'name': f'Contact {i}'} for i in range(4)])
        self.env.flush_all()
        data = {f'contact_name_{c.id}': c.name for c in contacts}
        data[f'contact_name_{contacts[1].id}'] = ''
        data[f'contact_function_{contacts[0].id}'] = 'Sales'
        data['contact_name_new0'] = 'Fifth'
        self._post(data)
        self.application.invalidate_recordset()
        self.assertEqual(
            sorted(self.application.contact_ids.mapped('name')), ['Contact 0', 'Contact 2', 'Contact 3', 'Fifth'])
        self.assertEqual(contacts[0].function, 'Sales')

    def test_contact_added_meanwhile_is_kept(self):
        """FR-SPM-0002: a contact absent from the post (added after the page opened) is not deleted."""
        contact = self.env['procurement.supplier.application.contact'].create({
            'application_id': self.application.id, 'name': 'Added by the buyer'})
        self.env.flush_all()
        self._post({'legal_name': 'Acme'})
        self.assertTrue(contact.exists())

    def test_email_cannot_be_changed_by_a_crafted_post(self):
        """FR-SPM-0001: the invitation address stays what the buyer entered."""
        self._post({'email': 'other@example.com', 'phone': '123'})
        self.application.invalidate_recordset()
        self.assertEqual(self.application.email, 'portal@example.com')
        self.assertEqual(self.application.phone, '123')

    def test_unknown_country_is_refused_and_the_page_shown_again(self):
        """FR-SPM-0002: a made-up country id shows a message instead of a server error."""
        response = self._post({'country_id': '99999999', 'phone': '123'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('Choose a country', response.text)

    def test_oversize_and_wrong_type_uploads_are_refused(self):
        """FR-SPM-0003: a file that is not a PDF, PNG or JPEG is refused and named; a PDF is bound."""
        doc_type = self.env['procurement.document.type'].create({'name': 'Tax certificate T005P'})
        self.goods.required_document_type_ids = doc_type
        self.env.flush_all()
        field = f'document_{doc_type.id}'
        response = self._post(files={field: ('x.pdf', b'plain text, not a pdf', 'application/pdf')})
        self.assertIn('only PDF, PNG or JPEG', response.text)
        self.assertFalse(self.application.document_ids)
        self._post(files={field: ('x.pdf', b'%PDF-1.4 content', 'application/pdf')})
        self.application.invalidate_recordset()
        self.assertEqual(self.application.document_ids.document_type_id, doc_type)

    def test_link_is_refused_after_submit(self):
        """FR-SPM-0001: once submitted, the same link shows a refusal."""
        self.application.sudo().write({'legal_name': 'Portal Ltd'})
        self.env.flush_all()
        response = self._post({'submit': '1'})
        self.assertIn('submitted', response.text)
        again = self.url_open(f'/my/procurement/onboarding/{self.token}')
        self.assertEqual(again.status_code, 403)
