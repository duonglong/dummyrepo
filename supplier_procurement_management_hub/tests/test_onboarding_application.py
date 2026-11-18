"""Unit tests of onboarding applications (invitation, field rules, submission).

Requirements: FR-SPM-0001, FR-SPM-0002, FR-SPM-0003
"""
import re

from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import HttpCase, TransactionCase

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
