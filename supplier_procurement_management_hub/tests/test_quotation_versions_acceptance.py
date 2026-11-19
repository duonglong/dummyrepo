"""Acceptance tests of T-014 (quotation responses: portal submit, immutable
versions, deadline, buyer entry), written from the specification.

Requirements: FR-SPM-0020, FR-SPM-0021, FR-SPM-0022, FR-SPM-0034, FR-SPM-0017,
FR-SPM-0027, FR-SPM-0028, FR-SPM-0042, BR-SPM-0003, NFR-SPM-0001

Names used (the design names none, fixed in tests/portal_common.py):
procurement.sourcing.response.spm_submit_version(lines, attachment_ids=None, **terms)
(the call behind POST /my/procurement/rfq/<id>/submit, run as the portal user) and
spm_enter_quotation(lines, ...) for the Buyer; a line is a dict with event_line_id,
price_unit, discount, lead_time_days. The submit form is not driven field by field:
what the form adds (parsing its keys) is left to the build; the refusals of the
route (not found for events and responses of other suppliers) are driven over HTTP.
Time is frozen so the dates of the scenarios are exact.
"""
from datetime import datetime

from freezegun import freeze_time

from odoo.exceptions import UserError, ValidationError

from .portal_common import SpmPortalCase

REFUSED = (302, 303, 401, 403, 404)
DEADLINE = datetime(2026, 11, 30, 17, 0, 0)


class TestQuotationVersionsAcceptance(SpmPortalCase):
    """Requirements: FR-SPM-0020, FR-SPM-0021, FR-SPM-0022, FR-SPM-0034, FR-SPM-0042"""

    def setUp(self):
        super().setUp()
        with freeze_time('2026-11-17 09:00:00'):
            self.event = self._event(self.acme | self.beta | self.gamma, deadline=DEADLINE)
        self.line = self.event.line_ids
        self.acme_response = self._response(self.event, self.acme)
        self.beta_response = self._response(self.event, self.beta)
        self.gamma_response = self._response(self.event, self.gamma)

    def _offer(self, price, lead=10, discount=0.0):
        return [{'event_line_id': self.line.id, 'price_unit': price,
                 'discount': discount, 'lead_time_days': lead}]

    def _submit(self, when, response=None, user=None, price=12.5):
        with freeze_time(when):
            return (response or self.acme_response).with_user(user or self.acme_user) \
                .spm_submit_version(self._offer(price))

    def test_sc_0020_1_invited_supplier_submits_a_quotation(self):
        """SC-SPM-0020-1: Acme submits 12.50 for 100 chairs with a 10-day lead
        time on 2026-11-20; its response holds that line, version 1 and the
        submission time."""
        version = self._submit('2026-11-20 10:00:00')
        self.env.flush_all()
        self.assertEqual(version.response_id, self.acme_response)
        self.assertEqual(version.version, 1)
        self.assertEqual(version.submitted_date, datetime(2026, 11, 20, 10, 0, 0))
        self.assertEqual(version.submitted_by_id, self.acme_user)
        self.assertFalse(version.entered_by_buyer)
        self.assertEqual(len(version.line_ids), 1)
        self.assertEqual(version.line_ids.event_line_id, self.line)
        self.assertEqual(version.line_ids.price_unit, 12.5)
        self.assertEqual(version.line_ids.lead_time_days, 10)
        self.assertEqual(version.amount_untaxed, 1250.0)
        self.assertEqual(self.acme_response.current_version_id, version)
        self.assertEqual(self.acme_response.state, 'submitted')
        self.assertFalse(self.beta_response.version_ids)

    def test_sc_0020_1_a_line_the_buyer_did_not_ask_for_is_refused(self):
        """FR-SPM-0020: suppliers can only fill the lines the buyer asked for;
        a line of another event is refused and nothing is stored."""
        other = self._event(self.acme, title='Other event')
        offer = self._offer(12.5) + [{
            'event_line_id': other.line_ids.id, 'price_unit': 1.0, 'discount': 0.0,
            'lead_time_days': 1}]
        with freeze_time('2026-11-20 10:00:00'):
            with self.assertRaises((UserError, ValidationError)):
                self.acme_response.with_user(self.acme_user).spm_submit_version(offer)
        self.assertFalse(self.acme_response.version_ids)

    def test_sc_0021_1_revision_keeps_the_earlier_version_unchanged(self):
        """SC-SPM-0021-1: Acme's version 1 (12.50, 2026-11-20 10:00) stays as it
        was when version 2 (11.90, 2026-11-22 09:00) becomes current."""
        first = self._submit('2026-11-20 10:00:00', price=12.5)
        second = self._submit('2026-11-22 09:00:00', price=11.9)
        self.env.flush_all()
        self.assertEqual(second.version, 2)
        self.assertEqual(self.acme_response.current_version_id, second)
        self.assertTrue(second.is_current)
        self.assertFalse(first.is_current)
        self.assertEqual(second.line_ids.price_unit, 11.9)
        self.assertEqual(first.line_ids.price_unit, 12.5)
        self.assertEqual(first.submitted_date, datetime(2026, 11, 20, 10, 0, 0))
        self.assertEqual(len(self.acme_response.version_ids), 2)

    def test_sc_0021_1_a_submitted_version_cannot_be_changed_or_deleted(self):
        """FR-SPM-0021: a version is written once; later changes and deletion are refused."""
        version = self._submit('2026-11-20 10:00:00')
        with self.assertRaises(UserError):
            version.sudo().write({'notes': 'changed'})
        with self.assertRaises(UserError):
            version.line_ids.sudo().write({'price_unit': 1.0})
        with self.assertRaises(UserError):
            version.sudo().unlink()
        self.assertEqual(version.line_ids.price_unit, 12.5)

    def test_sc_0022_1_submission_after_the_deadline_is_refused(self):
        """SC-SPM-0022-1: Acme submitting at 2026-11-30 17:01 (deadline 17:00)
        is refused with a message giving the deadline."""
        with self.assertRaises(UserError) as ctx:
            self._submit('2026-11-30 17:01:00')
        message = str(ctx.exception)
        self.assertIn('2026', message)
        self.assertIn('30', message)
        self.assertFalse(self.acme_response.version_ids)

    def test_sc_0022_1_submission_at_the_deadline_is_accepted(self):
        """FR-SPM-0022 boundary: at 17:00:00 the deadline has not passed; one
        second later the submission is refused."""
        version = self._submit('2026-11-30 17:00:00')
        self.assertEqual(version.version, 1)
        with self.assertRaises(UserError):
            self._submit('2026-11-30 17:00:01', response=self.acme_response)

    def test_sc_0022_1_a_revision_after_the_deadline_is_refused(self):
        """FR-SPM-0022: a revision after the deadline is refused as well; version 1 stays current."""
        first = self._submit('2026-11-29 10:00:00')
        with self.assertRaises(UserError):
            self._submit('2026-12-01 09:00:00', price=9.0)
        self.assertEqual(self.acme_response.current_version_id, first)

    def test_sc_0022_2_a_reopened_supplier_may_submit_and_others_may_not(self):
        """SC-SPM-0022-2: the deadline passed and the Buyer reopened Acme until
        2026-12-02 12:00; Acme submits on 2026-12-01 and is accepted while
        Beta still cannot submit."""
        self._submit('2026-11-29 10:00:00')
        self.acme_response.sudo().write({
            'reopened_deadline': datetime(2026, 12, 2, 12, 0, 0), 'state': 'reopened'})
        version = self._submit('2026-12-01 09:00:00', price=11.0)
        self.assertEqual(version.version, 2)
        self.assertEqual(self.acme_response.current_version_id, version)
        with self.assertRaises(UserError):
            self._submit('2026-12-01 09:00:00', response=self.beta_response, user=self.beta_user)
        self.assertFalse(self.beta_response.version_ids)

    def test_sc_0022_2_a_reopened_deadline_that_passed_refuses(self):
        """FR-SPM-0022: after the supplier's own new deadline the submission is refused again."""
        self.acme_response.sudo().write({
            'reopened_deadline': datetime(2026, 12, 2, 12, 0, 0), 'state': 'reopened'})
        with self.assertRaises(UserError):
            self._submit('2026-12-02 12:01:00')

    def test_sc_0042_1_a_cancelled_event_takes_no_submission(self):
        """SC-SPM-0042-1 (suppliers can no longer submit): after the Buyer
        cancels the event a supplier's submission is refused."""
        self.event.with_user(self.buyer).action_spm_cancel('Budget frozen')
        with self.assertRaises(UserError):
            self._submit('2026-11-20 10:00:00')
        self.assertFalse(self.acme_response.version_ids)

    def test_sc_0034_1_buyer_enters_a_quotation_received_by_email(self):
        """SC-SPM-0034-1: the Buyer enters Gamma's e-mailed 13.10 with the e-mail
        attached; Gamma's response version 1 shows 13.10, entered by the buyer, with the time."""
        mail_file = self.env['ir.attachment'].with_user(self.buyer).create({
            'name': 'gamma-reply.eml', 'raw': b'Subject: quote\n\n13.10 per chair',
            'mimetype': 'message/rfc822'})
        with freeze_time('2026-11-21 08:30:00'):
            version = self.gamma_response.with_user(self.buyer).spm_enter_quotation(
                self._offer(13.1), attachment_ids=[mail_file.id])
        self.env.flush_all()
        self.assertEqual(version.response_id, self.gamma_response)
        self.assertEqual(version.version, 1)
        self.assertEqual(version.line_ids.price_unit, 13.1)
        self.assertTrue(version.entered_by_buyer)
        self.assertEqual(version.submitted_by_id, self.buyer)
        self.assertEqual(version.submitted_date, datetime(2026, 11, 21, 8, 30, 0))
        self.assertEqual(version.attachment_ids, mail_file)
        self.assertEqual(self.gamma_response.current_version_id, version)

    def test_sc_0034_1_a_buyer_entry_is_a_new_version_of_an_existing_response(self):
        """FR-SPM-0034: entering a quotation for a supplier that already
        submitted adds a new version and keeps the first."""
        first = self._submit('2026-11-20 10:00:00')
        with freeze_time('2026-11-21 08:30:00'):
            second = self.acme_response.with_user(self.buyer).spm_enter_quotation(self._offer(12.0))
        self.assertEqual(second.version, 2)
        self.assertTrue(second.entered_by_buyer)
        self.assertFalse(first.entered_by_buyer)
        self.assertEqual(first.line_ids.price_unit, 12.5)


class TestQuotationPortalAcceptance(SpmPortalCase):
    """Requirements: FR-SPM-0017, FR-SPM-0020, FR-SPM-0027, FR-SPM-0028, BR-SPM-0003, NFR-SPM-0001"""

    def setUp(self):
        super().setUp()
        self.shared = self._event(self.acme | self.beta, title='Shared tender')
        self.beta_only = self._event(self.beta, title='Beta only tender')
        self.beta_response = self._response(self.beta_only, self.beta)

    def _beta_quote(self, attachments=()):
        line = self.beta_only.line_ids
        return self.beta_response.with_user(self.beta_user).spm_submit_version(
            [{'event_line_id': line.id, 'price_unit': 7.77, 'discount': 0.0, 'lead_time_days': 3}],
            attachment_ids=[a.id for a in attachments])

    def test_sc_0020_2_event_without_invitation_is_not_found(self):
        """SC-SPM-0020-2: an Acme portal user posting a quotation for an event
        Acme is not invited to stores nothing and gets a not found answer; its
        own event page is open."""
        self._login(self.acme_user)
        self.assertEqual(self._get(f'/my/procurement/rfq/{self.shared.id}').status_code, 200)
        response = self._post(f'/my/procurement/rfq/{self.beta_only.id}/submit', {
            f'price_unit_{self.beta_only.line_ids.id}': '1.00'})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.beta_response.version_ids)
        self.assertEqual(self._get(f'/my/procurement/rfq/{self.beta_only.id}').status_code, 404)

    def test_sc_0017_3_form_naming_another_suppliers_response_saves_nothing(self):
        """SC-SPM-0017-3: an Acme user posts a quotation form naming Beta's
        response; nothing is saved on Beta's response and not found is returned."""
        self._login(self.acme_user)
        self.assertEqual(self._get(f'/my/procurement/rfq/{self.shared.id}').status_code, 200)
        response = self._post(f'/my/procurement/rfq/{self.beta_only.id}/submit', {
            'response_id': self.beta_response.id,
            f'price_unit_{self.beta_only.line_ids.id}': '1.00'})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.beta_response.version_ids)
        self.assertFalse(self.beta_response.current_version_id)

    def test_sc_0017_3_a_crafted_response_id_on_an_invited_event_does_not_reach_beta(self):
        """SC-SPM-0017-3 / BR-SPM-0002: even on an event both are invited to, the
        response id of the form is ignored; Beta's response stays untouched."""
        beta_response = self._response(self.shared, self.beta)
        self._login(self.acme_user)
        self._post(f'/my/procurement/rfq/{self.shared.id}/submit', {
            'response_id': beta_response.id,
            f'price_unit_{self.shared.line_ids.id}': '1.00'})
        self.assertFalse(beta_response.version_ids)
        self.assertEqual(self.env['procurement.sourcing.response.version'].sudo().search_count(
            [('response_id', '=', beta_response.id)]), 0)

    def test_sc_0017_2_attachment_of_another_suppliers_quotation(self):
        """SC-SPM-0017-2: an attachment of Beta's quotation is downloadable by
        Beta but not by an Acme user, with or without its access token, and not
        readable through the record layer."""
        attachment = self.env['ir.attachment'].with_user(self.beta_user).create({
            'name': 'beta-offer.pdf', 'raw': b'%PDF-1.4 beta secret price 7.77',
            'mimetype': 'application/pdf'})
        version = self._beta_quote(attachment)
        attachment.invalidate_recordset()
        token = attachment.sudo().generate_access_token()[0]
        self.authenticate(self.beta_user.login, 'spm-portal-pass')
        own = self._get(f'/my/procurement/quote-file/{attachment.id}')
        self.assertEqual(own.status_code, 200)
        self.assertIn(b'beta secret price', own.content)
        self.assertEqual(attachment.sudo().res_model, version._name)
        self._login(self.acme_user)
        for url in (f'/my/procurement/quote-file/{attachment.id}',
                    f'/my/procurement/quote-file/{attachment.id}?access_token={token}',
                    f'/web/content/{attachment.id}',
                    f'/web/content/{attachment.id}?access_token={token}'):
            response = self._get(url)
            self.assertNotEqual(response.status_code, 200, url)
            self.assertNotIn(b'beta secret price', response.content, url)
        visible = self.env['ir.attachment'].with_user(self.acme_user).search([('id', '=', attachment.id)])
        self.assertFalse(visible)
        self.assertFalse(version.with_user(self.acme_user).search([('id', '=', version.id)]))

    def test_sc_0027_1_download_is_logged(self):
        """SC-SPM-0027-1: when an Acme portal user downloads the drawing of the
        event, the portal activity log has an entry with the user, Acme, the
        time, download, the event and allowed."""
        drawing = self.env['ir.attachment'].create({
            'name': 'drawing.pdf', 'raw': b'%PDF-1.4 drawing', 'mimetype': 'application/pdf',
            'res_model': 'procurement.sourcing.event', 'res_id': self.shared.id})
        # BR-SPM-0003: only files in the supplier files field of the event are
        # served to its invited suppliers; the Buyer puts the drawing there.
        self.shared.write({'supplier_file_ids': [(4, drawing.id)]})
        self._login(self.acme_user)
        response = self._get(f'/my/procurement/quote-file/{drawing.id}')
        self.assertEqual(response.status_code, 200)
        self.env.flush_all()
        entry = self.env['procurement.portal.log'].sudo().search([
            ('user_id', '=', self.acme_user.id), ('action', '=', 'download')])
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry.partner_id, self.acme)
        self.assertEqual(entry.res_model, 'procurement.sourcing.event')
        self.assertEqual(entry.res_id, self.shared.id)
        self.assertEqual(entry.result, 'ok')
        self.assertTrue(entry.date)

    def test_sc_0027_1_a_refused_download_is_logged_as_denied(self):
        """FR-SPM-0027: a refused download is an entry too (action denied)."""
        drawing = self.env['ir.attachment'].create({
            'name': 'secret.pdf', 'raw': b'%PDF-1.4 secret', 'mimetype': 'application/pdf',
            'res_model': 'procurement.sourcing.event', 'res_id': self.beta_only.id})
        self._login(self.acme_user)
        self.assertEqual(self._get(f'/my/procurement/quote-file/{drawing.id}').status_code, 404)
        entry = self.env['procurement.portal.log'].sudo().search([
            ('user_id', '=', self.acme_user.id), ('action', '=', 'denied')])
        self.assertTrue(entry)
        self.assertIn(entry[:1].result, ('denied', 'refused'))

    def test_sc_0028_1_blocking_the_supplier_ends_access_on_the_next_request(self):
        """SC-SPM-0028-1: Acme's user is logged in; a Procurement manager sets
        Acme to Blocked; opening the event again is refused."""
        self._login(self.acme_user)
        self.assertEqual(self._get(f'/my/procurement/rfq/{self.shared.id}').status_code, 200)
        self.acme.with_user(self.manager).action_spm_set_status(self.blocked, 'Fraud suspected')
        response = self._get(f'/my/procurement/rfq/{self.shared.id}')
        self.assertIn(response.status_code, REFUSED)
        self.assertNotIn(self.shared.title, response.text)
