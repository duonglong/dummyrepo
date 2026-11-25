"""Acceptance tests of T-005 (onboarding applications: invite, configurable
fields, submit), written from the specification.

Requirements: FR-SPM-0001, FR-SPM-0002, FR-SPM-0003

Names used (the design names none for these steps, so they are fixed here):
- procurement.supplier.application.spm_invite(email, supplier_type) -> application
- procurement.supplier.application.spm_field_rules() -> {field_key: 'shown'|'required'|'hidden'}
- procurement.supplier.application.action_spm_submit()
- invitation link route /my/procurement/onboarding/<token>, found in the invitation e-mail.
The supplier-side form is not driven through a browser: the submit step is
the method, what the form adds is left to the build.
"""
import re
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import HttpCase, TransactionCase

MODULE = 'supplier_procurement_management_hub'
LINK = re.compile(r'/my/procurement/onboarding/([A-Za-z0-9_\-]+)')


class OnboardingCase:
    def _base_setup(self):
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        self.buyer = self.env['res.users'].create({
            'name': 'Bea Buyer', 'login': 'spm_buyer_t005',
            'email': 'spm_buyer_t005@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_buyer').id,
                                  self.env.ref('base.group_partner_manager').id])],
        })
        DocType = self.env['procurement.document.type']
        self.tax_cert = DocType.create({'name': 'Tax certificate'})
        self.insurance = DocType.create({'name': 'Insurance certificate'})
        self.goods = self.env['procurement.supplier.type'].create({
            'name': 'Goods T005F',
            'required_document_type_ids': [(6, 0, [self.tax_cert.id])],
        })

    def _field_key(self, word):
        selection = self.env['procurement.supplier.type.field']._fields['field_key'].selection
        keys = [k for k, label in selection if word in k or word in str(label).lower()]
        self.assertTrue(keys, f"a field key for '{word}' exists")
        return keys[0]

    def _rule(self, supplier_type, word, visibility):
        return self.env['procurement.supplier.type.field'].create({
            'type_id': supplier_type.id, 'field_key': self._field_key(word),
            'visibility': visibility})

    def _invite(self, email='sales@acme.example', supplier_type=None):
        return self.env['procurement.supplier.application'].with_user(self.buyer).spm_invite(
            email, supplier_type or self.goods)

    def _mails(self, email):
        return self.env['mail.mail'].sudo().search([('email_to', 'ilike', email)])


class TestOnboardingInviteAcceptance(OnboardingCase, TransactionCase):
    """Requirements: FR-SPM-0001"""

    def setUp(self):
        super().setUp()
        self._base_setup()

    def test_sc_0001_1_invitation_creates_an_application_and_sends_a_link(self):
        """SC-SPM-0001-1: a Buyer invites sales@acme.example as type Goods;
        an application in state Invited exists and an e-mail with an
        invitation link goes to that address."""
        application = self._invite()
        self.env.flush_all()
        self.assertEqual(application.state, 'invited')
        self.assertEqual(application.email, 'sales@acme.example')
        self.assertEqual(application.supplier_type_id, self.goods)
        mails = self._mails('sales@acme.example')
        self.assertEqual(len(mails), 1)
        self.assertRegex(mails.body_html, LINK)

    def test_sc_0001_1_the_token_is_stored_hashed_and_expires_in_14_days(self):
        """FR-SPM-0001: the link carries a token, the application stores only
        its hash, and the invitation expires after 14 days."""
        application = self._invite()
        token = LINK.search(self._mails('sales@acme.example').body_html).group(1)
        self.assertNotEqual(application.sudo().invitation_token_hash, token)
        self.assertTrue(application.sudo().invitation_token_hash)
        delta = application.invitation_expiry - fields.Datetime.now()
        self.assertGreater(delta, timedelta(days=13, hours=23))
        self.assertLessEqual(delta, timedelta(days=14, minutes=5))


class TestOnboardingLinkAcceptance(OnboardingCase, HttpCase):
    """Requirements: FR-SPM-0001"""

    def setUp(self):
        super().setUp()
        self._base_setup()
        self.application = self._invite()
        self.application.sudo().write({'legal_name': 'Acme Secret Name'})
        self.env.flush_all()
        self.token = LINK.search(self._mails('sales@acme.example').body_html).group(1)
        self.path = f'/my/procurement/onboarding/{self.token}'

    def test_sc_0001_2_expired_link_is_refused_with_a_message(self):
        """SC-SPM-0001-2: a link sent 15 days ago shows a message that the
        invitation expired and does not show the application."""
        self.application.sudo().write({
            'invitation_expiry': fields.Datetime.now() - timedelta(days=1)})
        self.env.flush_all()
        response = self.url_open(self.path)
        self.assertIn('expired', response.text.lower())
        self.assertNotIn('Acme Secret Name', response.text)

    def test_sc_0001_3_used_link_is_refused_and_logged(self):
        """SC-SPM-0001-3: a link already used is refused, the application is
        not shown and the attempt is written to the portal activity log."""
        self.application.sudo().write({'invitation_used_date': fields.Datetime.now()})
        self.env.flush_all()
        response = self.url_open(self.path)
        self.assertNotIn('Acme Secret Name', response.text)
        log = self.env['procurement.portal.log'].sudo().search([
            ('res_model', '=', 'procurement.supplier.application'),
            ('res_id', '=', self.application.id), ('action', '=', 'denied')])
        self.assertTrue(log)

    def test_a_valid_link_shows_the_application(self):
        """FR-SPM-0001: a fresh link opens the application it was made for."""
        response = self.url_open(self.path)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Acme Secret Name', response.text)


class TestOnboardingFieldsAndSubmitAcceptance(OnboardingCase, TransactionCase):
    """Requirements: FR-SPM-0002, FR-SPM-0003"""

    def setUp(self):
        super().setUp()
        self._base_setup()

    def _application(self, supplier_type=None, **vals):
        data = {'email': 'sales@acme.example', 'legal_name': 'Acme',
                'supplier_type_id': (supplier_type or self.goods).id,
                'user_id': self.buyer.id}
        data.update(vals)
        return self.env['procurement.supplier.application'].with_user(self.buyer).create(data)

    def test_sc_0002_1_field_rules_of_the_type_are_shown_on_the_application(self):
        """SC-SPM-0002-1: type Services has Bank account required and
        Operating locations hidden; the application of that type shows bank as
        required and operating locations not shown. Legal name stays required."""
        services = self.env['procurement.supplier.type'].create({'name': 'Services T005'})
        self._rule(services, 'bank', 'required')
        self._rule(services, 'location', 'hidden')
        rules = self._application(services).spm_field_rules()
        self.assertEqual(rules[self._field_key('bank')], 'required')
        self.assertEqual(rules[self._field_key('location')], 'hidden')
        self.assertEqual(rules[self._field_key('legal')], 'required')

    def test_sc_0003_1_complete_application_is_submitted_and_buyer_gets_an_activity(self):
        """SC-SPM-0003-1: Goods application with its required fields and the
        Tax certificate attached; submitted by the supplier's side it is in
        state Submitted and the responsible buyer has an activity to review it."""
        self._rule(self.goods, 'tax', 'required')
        application = self._application(vat='BE0123456789')
        self.env['procurement.supplier.document'].with_user(self.buyer).create({
            'application_id': application.id, 'document_type_id': self.tax_cert.id})
        application.with_user(self.buyer).action_spm_submit()
        self.env.flush_all()
        self.assertEqual(application.state, 'submitted')
        activities = self.env['mail.activity'].search([
            ('res_model', '=', 'procurement.supplier.application'),
            ('res_id', '=', application.id), ('user_id', '=', self.buyer.id)])
        self.assertTrue(activities)

    def test_sc_0003_2_incomplete_application_is_refused_naming_everything_missing(self):
        """SC-SPM-0003-2: missing tax identifier and the required Insurance
        certificate; the submission is refused and the message names both."""
        self._rule(self.goods, 'tax', 'required')
        self.goods.write({'required_document_type_ids': [(4, self.insurance.id)]})
        application = self._application()
        self.env['procurement.supplier.document'].with_user(self.buyer).create({
            'application_id': application.id, 'document_type_id': self.tax_cert.id})
        with self.assertRaises((UserError, ValidationError)) as ctx:
            application.with_user(self.buyer).action_spm_submit()
        message = str(ctx.exception).lower()
        self.assertIn('tax identifier', message)
        self.assertIn('insurance certificate', message)
        self.assertNotIn('tax certificate', message)
        self.assertEqual(application.state, 'invited')

    def test_sc_0003_2_one_missing_document_alone_refuses_the_submission(self):
        """FR-SPM-0003 boundary: every required field filled but the required
        document missing is refused and names that document."""
        application = self._application()
        with self.assertRaises((UserError, ValidationError)) as ctx:
            application.with_user(self.buyer).action_spm_submit()
        self.assertIn('tax certificate', str(ctx.exception).lower())
        self.assertEqual(application.state, 'invited')
