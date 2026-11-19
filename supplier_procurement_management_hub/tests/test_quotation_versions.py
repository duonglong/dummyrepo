"""Own tests of T-014 (quotation versions): guards, validation, the buyer dialog and the portal form.

Requirements: FR-SPM-0020, FR-SPM-0021, FR-SPM-0022, FR-SPM-0034
"""
from datetime import datetime

from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError
from odoo.tests import Form

from .portal_common import SpmPortalCase

DEADLINE = datetime(2026, 11, 30, 17, 0, 0)


class TestQuotationVersions(SpmPortalCase):
    """Requirements: FR-SPM-0020, FR-SPM-0021, FR-SPM-0022, FR-SPM-0034"""

    def setUp(self):
        super().setUp()
        with freeze_time('2026-11-17 09:00:00'):
            self.event = self._event(self.acme | self.beta, deadline=DEADLINE)
        self.line = self.event.line_ids
        self.response = self._response(self.event, self.acme)

    def _offer(self, **values):
        return [dict({'event_line_id': self.line.id, 'price_unit': 10.0, 'discount': 0.0,
                      'lead_time_days': 5}, **values)]

    def _submit(self, offer=None, **kw):
        with freeze_time('2026-11-20 10:00:00'):
            return self.response.with_user(self.acme_user).spm_submit_version(
                self._offer() if offer is None else offer, **kw)

    def test_a_user_cannot_write_the_versions_of_a_response(self):
        """Requirements: FR-SPM-0021 - the current version and state change with the submissions only."""
        version = self._submit()
        with self.assertRaises(AccessError):
            self.response.with_user(self.buyer).write({'current_version_id': False})
        with self.assertRaises(AccessError):
            self.response.with_user(self.buyer).write({'state': 'invited'})
        self.assertEqual(self.response.current_version_id, version)

    def test_a_line_cannot_be_added_to_a_submitted_version(self):
        """Requirements: FR-SPM-0021 - lines exist only with the version being submitted."""
        version = self._submit()
        with self.assertRaises(UserError):
            self.env['procurement.sourcing.response.line'].sudo().create({
                'version_id': version.id, 'event_line_id': self.line.id, 'quantity': 1.0,
                'price_unit': 1.0})

    def test_invalid_offers_are_refused_and_store_nothing(self):
        """Requirements: FR-SPM-0020 - a discount over 100, a negative price, a repeated line, no line."""
        for offer in (self._offer(discount=101.0), self._offer(price_unit=-1.0),
                      self._offer() + self._offer(), [], self._offer(price_unit='abc')):
            with self.assertRaises(UserError):
                self._submit(offer)
        self.assertFalse(self.response.version_ids)

    def test_a_file_of_someone_else_or_already_attached_is_refused(self):
        """Requirements: FR-SPM-0020 - only the user's own unattached files can be attached."""
        buyers_file = self.env['ir.attachment'].with_user(self.buyer).create({'name': 'x.pdf', 'raw': b'x'})
        with self.assertRaises(UserError):
            self._submit(attachment_ids=[buyers_file.id])
        with self.assertRaises(UserError):
            self._submit(attachment_ids=[0])
        self.assertFalse(self.response.version_ids)

    def test_a_file_of_a_version_takes_no_access_token(self):
        """Requirements: FR-SPM-0017 - a token written on a quotation file is dropped."""
        own = self.env['ir.attachment'].with_user(self.acme_user).create({'name': 'o.pdf', 'raw': b'o'})
        own.sudo().generate_access_token()
        version = self._submit(attachment_ids=[own.id])
        self.assertEqual(own.sudo().res_id, version.id)
        own.sudo().generate_access_token()
        self.assertFalse(own.sudo().access_token)

    def test_buyer_dialog_enters_a_quotation(self):
        """Requirements: FR-SPM-0034 - opened and filled with Form; the Enter button is checked on screen."""
        gamma_response = self._response(self._event(self.gamma, title='G'), self.gamma)
        with Form(self.env['procurement.quotation.entry'].with_user(self.buyer).with_context(
                default_response_id=gamma_response.id)) as form:
            with form.line_ids.edit(0) as line:
                line.price_unit = 8.0
                line.lead_time_days = 4
            form.warranty_months = 12
        wizard = form.save()
        wizard.action_confirm()
        version = gamma_response.current_version_id
        self.assertTrue(version.entered_by_buyer)
        self.assertEqual(version.line_ids.price_unit, 8.0)
        self.assertEqual(version.warranty_months, 12)

    def test_portal_form_stores_a_version_with_terms(self):
        """Requirements: FR-SPM-0020 - the form's keys become the lines and the terms of the version."""
        self._login(self.acme_user)
        with freeze_time('2026-11-20 10:00:00'):
            response = self._post(f'/my/procurement/rfq/{self.event.id}/submit', {
                f'price_unit_{self.line.id}': '9,50', f'lead_time_{self.line.id}': '7',
                'warranty_months': '24', 'notes': 'Delivered'})
        self.assertEqual(response.status_code, 303)
        version = self.response.current_version_id
        self.assertEqual(version.line_ids.price_unit, 9.5)
        self.assertEqual(version.line_ids.lead_time_days, 7)
        self.assertEqual(version.warranty_months, 24)
        self.assertEqual(version.notes, 'Delivered')

    def test_portal_form_after_the_deadline_shows_the_refusal(self):
        """Requirements: FR-SPM-0022 - the page says the deadline and stores nothing."""
        self._login(self.acme_user)
        with freeze_time('2026-12-01 10:00:00'):
            response = self._post(f'/my/procurement/rfq/{self.event.id}/submit', {
                f'price_unit_{self.line.id}': '9.50'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('has passed', response.text)
        self.assertFalse(self.response.version_ids)
