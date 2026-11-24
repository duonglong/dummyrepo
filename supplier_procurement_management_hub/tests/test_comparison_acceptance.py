"""Acceptance tests of T-016 (comparison matrix, negotiation, normalization
assumptions), written from the specification.

Requirements: BR-SPM-0007, FR-SPM-0035, FR-SPM-0036, FR-SPM-0037

Names used (the design names none, fixed in tests/comparison_common.py):
procurement.sourcing.event.action_spm_compare() opens the comparison;
procurement.sourcing.response.action_spm_request_revision(note_to_supplier,
new_deadline, internal_note=None) is the "Request revision" button. The revision
dialog is not driven field by field: what the dialog adds (collecting the three
values) is left to the build; the method is the action behind its Confirm button.
"""
from datetime import datetime

from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError, ValidationError

from .comparison_common import SpmComparisonCase


class TestComparisonAcceptance(SpmComparisonCase):
    """Requirements: FR-SPM-0035, FR-SPM-0036, FR-SPM-0037, BR-SPM-0007"""

    # FR-SPM-0035 -----------------------------------------------------------
    def _two_suppliers(self):
        event = self._sourcing(
            self.acme | self.beta, criteria=[('Lead time', 'lead_time', 'lower', 40.0)])
        chairs = event.line_ids
        acme, beta = self._response(event, self.acme), self._response(event, self.beta)
        self._quote(acme, [(chairs, 11.90, 10)])
        self._quote(beta, [(chairs, 12.40, 5)])
        return event, chairs, acme, beta

    def test_sc_0035_1_best_price_and_best_lead_time_are_highlighted(self):
        """SC-SPM-0035-1: Acme 11.90 with 10 days, Beta 12.40 with 5 days: Acme's
        price and Beta's lead time are the best of their rows."""
        event, chairs, acme, beta = self._two_suppliers()
        comparison = self._compare(event)
        acme_price = self._cell(comparison, acme, 'line', line=chairs)
        beta_price = self._cell(comparison, beta, 'line', line=chairs)
        self.assertEqual(acme_price.raw_value, 11.90)
        self.assertEqual(beta_price.raw_value, 12.40)
        self.assertTrue(acme_price.is_best)
        self.assertFalse(beta_price.is_best)
        lead = event.criterion_ids.filtered(lambda c: c.kind == 'lead_time')
        acme_lead = self._cell(comparison, acme, 'criterion', criterion=lead)
        beta_lead = self._cell(comparison, beta, 'criterion', criterion=lead)
        self.assertEqual(acme_lead.raw_value, 10)
        self.assertEqual(beta_lead.raw_value, 5)
        self.assertTrue(beta_lead.is_best)
        self.assertFalse(acme_lead.is_best)

    def test_sc_0035_1_the_total_row_highlights_the_lowest_total(self):
        """FR-SPM-0035: each response has a total (60 x 11.90 = 714.00 and
        60 x 12.40 = 744.00); the lowest total is the best of its row."""
        event, chairs, acme, beta = self._two_suppliers()
        comparison = self._compare(event)
        acme_total = self._cell(comparison, acme, 'total')
        beta_total = self._cell(comparison, beta, 'total')
        self.assertAlmostEqual(acme_total.raw_value, 714.0, places=2)
        self.assertAlmostEqual(beta_total.raw_value, 744.0, places=2)
        self.assertTrue(acme_total.is_best)
        self.assertFalse(beta_total.is_best)

    def test_sc_0035_2_a_line_not_quoted_is_missing_and_the_total_incomplete(self):
        """SC-SPM-0035-2: lines chairs and desks; Gamma quoted chairs only: its
        desk line is missing and its total covers the chairs only, marked incomplete."""
        event = self._sourcing(
            self.acme | self.gamma,
            lines=[('Chair', self.chair, 60.0), ('Desk', self.desk, 20.0)])
        chairs, desks = event.line_ids.sorted('id')
        acme, gamma = self._response(event, self.acme), self._response(event, self.gamma)
        self._quote(acme, [(chairs, 12.0, 10), (desks, 200.0, 14)])
        self._quote(gamma, [(chairs, 11.0, 10)])
        comparison = self._compare(event)
        self.assertTrue(self._cell(comparison, gamma, 'line', line=desks).missing)
        self.assertFalse(self._cell(comparison, gamma, 'line', line=chairs).missing)
        self.assertFalse(self._cell(comparison, acme, 'line', line=desks).missing)
        gamma_total = self._cell(comparison, gamma, 'total')
        self.assertAlmostEqual(gamma_total.raw_value, 660.0, places=2)
        self.assertTrue(gamma_total.missing)
        self.assertFalse(self._cell(comparison, acme, 'total').missing)

    def test_sc_0035_1_only_the_current_version_is_compared(self):
        """FR-SPM-0035: Acme's version 2 (11.00) replaces version 1 (12.00) in
        the comparison; the earlier version stays on the response."""
        event = self._sourcing(self.acme | self.beta)
        chairs = event.line_ids
        acme, beta = self._response(event, self.acme), self._response(event, self.beta)
        self._quote(acme, [(chairs, 12.0, 10)])
        self._quote(beta, [(chairs, 12.4, 5)])
        with freeze_time('2026-11-22 09:00:00'):
            acme.with_user(self.buyer).spm_enter_quotation([{
                'event_line_id': chairs.id, 'price_unit': 11.0,
                'discount': 0.0, 'lead_time_days': 10}])
        comparison = self._compare(event)
        self.assertEqual(self._cell(comparison, acme, 'line', line=chairs).raw_value, 11.0)
        self.assertEqual(len(acme.version_ids), 2)

    # FR-SPM-0036 -----------------------------------------------------------
    def test_sc_0036_1_revision_request_reopens_one_supplier(self):
        """SC-SPM-0036-1: the deadline of the event passed; the Buyer asks Acme
        for a revised offer until 2026-12-02 12:00 with a note: Acme may submit
        until then, the negotiation history holds the note, Acme is told."""
        event = self._sourcing(self.acme | self.beta)
        acme, beta = self._response(event, self.acme), self._response(event, self.beta)
        self._quote(acme, [(event.line_ids, 12.0, 10)])
        until = datetime(2026, 12, 2, 12, 0, 0)
        with freeze_time('2026-12-01 08:00:00'):
            acme.with_user(self.buyer).action_spm_request_revision(
                'Best price for 60 units?', until, internal_note='Beta is cheaper')
            self.env.flush_all()
            negotiation = event.negotiation_ids
            self.assertEqual(len(negotiation), 1)
            self.assertEqual(negotiation.response_id, acme)
            self.assertEqual(negotiation.note_to_supplier, 'Best price for 60 units?')
            self.assertEqual(negotiation.internal_note, 'Beta is cheaper')
            self.assertEqual(negotiation.new_deadline, until)
            self.assertEqual(negotiation.user_id, self.buyer)
            self.assertEqual(acme.state, 'reopened')
            self.assertEqual(acme.effective_deadline, until)
            self.assertTrue(self._sent_to(self.acme, 'Best price for 60 units?'))
            version = acme.with_user(self.acme_user).spm_submit_version([{
                'event_line_id': event.line_ids.id, 'price_unit': 11.0,
                'discount': 0.0, 'lead_time_days': 10}])
            self.assertEqual(version.version, 2)
            with self.assertRaises(UserError):
                beta.with_user(self.beta_user).spm_submit_version([{
                    'event_line_id': event.line_ids.id, 'price_unit': 10.0,
                    'discount': 0.0, 'lead_time_days': 10}])
        self.assertFalse(self._sent_to(self.beta, 'Best price for 60 units?'))

    def test_sc_0036_1_the_new_deadline_is_the_last_moment(self):
        """FR-SPM-0036 boundary: Acme submits at 12:00:00 on the new deadline
        and is refused one second later."""
        event = self._sourcing(self.acme)
        acme = self._response(event, self.acme)
        self._quote(acme, [(event.line_ids, 12.0, 10)])
        with freeze_time('2026-12-01 08:00:00'):
            acme.with_user(self.buyer).action_spm_request_revision(
                'Please improve', datetime(2026, 12, 2, 12, 0, 0))
        offer = [{'event_line_id': event.line_ids.id, 'price_unit': 11.0,
                  'discount': 0.0, 'lead_time_days': 10}]
        with freeze_time('2026-12-02 12:00:00'):
            self.assertEqual(acme.with_user(self.acme_user).spm_submit_version(offer).version, 2)
        with freeze_time('2026-12-02 12:00:01'):
            with self.assertRaises(UserError):
                acme.with_user(self.acme_user).spm_submit_version(offer)

    def test_sc_0036_1_a_revision_request_needs_a_note(self):
        """FR-SPM-0036: the request carries a negotiation note; none is refused
        and nothing is stored or reopened."""
        event = self._sourcing(self.acme)
        acme = self._response(event, self.acme)
        self._quote(acme, [(event.line_ids, 12.0, 10)])
        with freeze_time('2026-12-01 08:00:00'):
            with self.assertRaises((UserError, ValidationError)):
                acme.with_user(self.buyer).action_spm_request_revision(
                    '  ', datetime(2026, 12, 2, 12, 0, 0))
        self.assertFalse(event.negotiation_ids)
        self.assertEqual(acme.state, 'submitted')

    def test_sc_0036_1_the_internal_note_is_never_shown_to_the_supplier(self):
        """FR-SPM-0036: the internal note of a negotiation is not readable by
        the supplier's portal user."""
        event = self._sourcing(self.acme)
        acme = self._response(event, self.acme)
        self._quote(acme, [(event.line_ids, 12.0, 10)])
        with freeze_time('2026-12-01 08:00:00'):
            acme.with_user(self.buyer).action_spm_request_revision(
                'Please improve', datetime(2026, 12, 2, 12, 0, 0), internal_note='Secret plan')
        self.env.flush_all()
        negotiation = event.negotiation_ids
        self.assertEqual(negotiation.internal_note, 'Secret plan')
        with self.assertRaises(AccessError):
            negotiation.with_user(self.acme_user).read(['internal_note'])

    # FR-SPM-0037, BR-SPM-0007 ------------------------------------------------
    def test_sc_0037_1_stated_assumptions_show_converted_values(self):
        """SC-SPM-0037-1: the event compares in EUR at the rate of 2026-11-30,
        without tax, per unit; Beta quoted 130 USD per box of 10 and the USD
        rate is 1.30 per EUR: the line shows 130 USD per box and 10.00 EUR per
        unit, and the assumptions are named on the comparison."""
        event = self._sourcing(self.acme | self.beta)
        chairs = event.line_ids
        self._assume(event, currency=self.eur)
        company_currency = self.company.currency_id
        self.assertIn(company_currency, self.eur | self.usd, "the fixture converts EUR and USD")
        if company_currency == self.eur:
            self._set_rate(self.usd, 1.30)
        else:
            self._set_rate(self.eur, 1 / 1.30)
        acme, beta = self._response(event, self.acme), self._response(event, self.beta)
        self._quote(acme, [(chairs, 11.0, 10)], currency=self.eur)
        self._quote(beta, [(chairs, 130.0, 5)], currency=self.usd, uom=self.box)
        comparison = self._compare(event)
        self.assertTrue(comparison.is_normalized)
        beta_cell = self._cell(comparison, beta, 'line', line=chairs)
        self.assertEqual(beta_cell.raw_value, 130.0)
        self.assertIn('130', beta_cell.raw_display)
        self.assertIn('USD', beta_cell.raw_display)
        self.assertAlmostEqual(beta_cell.normalized_value, 10.0, places=2)
        acme_cell = self._cell(comparison, acme, 'line', line=chairs)
        self.assertAlmostEqual(acme_cell.normalized_value, 11.0, places=2)
        assumptions = comparison.assumptions or ''
        self.assertIn('EUR', assumptions)
        self.assertIn('2026-11-30', assumptions)

    def test_sc_0037_1_the_best_price_is_found_on_the_converted_values(self):
        """FR-SPM-0037: with the assumptions of SC-SPM-0037-1 Beta's 10.00 EUR
        per unit beats Acme's 11.00 EUR although 130 is more than 11."""
        event = self._sourcing(self.acme | self.beta)
        chairs = event.line_ids
        self._assume(event, currency=self.eur)
        if self.company.currency_id == self.eur:
            self._set_rate(self.usd, 1.30)
        else:
            self._set_rate(self.eur, 1 / 1.30)
        acme, beta = self._response(event, self.acme), self._response(event, self.beta)
        self._quote(acme, [(chairs, 11.0, 10)], currency=self.eur)
        self._quote(beta, [(chairs, 130.0, 5)], currency=self.usd, uom=self.box)
        comparison = self._compare(event)
        self.assertTrue(self._cell(comparison, beta, 'line', line=chairs).is_best)
        self.assertFalse(self._cell(comparison, acme, 'line', line=chairs).is_best)

    def test_sc_0037_2_without_assumptions_values_are_raw_and_price_is_not_scored(self):
        """SC-SPM-0037-2: an event with no stated assumptions and quotes in EUR
        and USD: raw values are shown, marked not normalized, and price is left
        out of the weighted score with a warning."""
        event = self._sourcing(
            self.acme | self.beta,
            criteria=[('Price', 'price', 'lower', 60.0), ('Lead time', 'lead_time', 'lower', 40.0)])
        chairs = event.line_ids
        self.assertFalse(event.is_normalized)
        acme, beta = self._response(event, self.acme), self._response(event, self.beta)
        self._quote(acme, [(chairs, 11.9, 10)], currency=self.eur)
        self._quote(beta, [(chairs, 13.0, 5)], currency=self.usd)
        comparison = self._compare(event)
        self.assertFalse(comparison.is_normalized)
        self.assertIn('normaliz', (comparison.assumptions or '').lower())
        for response, raw in ((acme, 11.9), (beta, 13.0)):
            cell = self._cell(comparison, response, 'line', line=chairs)
            self.assertEqual(cell.raw_value, raw)
            self.assertFalse(cell.normalized_value)
        price = event.criterion_ids.filtered(lambda c: c.kind == 'price')
        for response in (acme, beta):
            self.assertFalse(self._cell(comparison, response, 'criterion', criterion=price).score)

    def test_sc_0037_2_one_missing_assumption_is_enough_to_stay_raw(self):
        """BR-SPM-0007: the event is normalized only when its currency, rate
        date, tax basis and every line's comparison unit are all stated."""
        event = self._sourcing(self.acme)
        self.assertFalse(event.is_normalized)
        self._assume(event)
        event.invalidate_recordset()
        self.assertTrue(event.is_normalized)
        event.with_user(self.buyer).write({'rate_date': False})
        self.assertFalse(event.is_normalized)
        event.with_user(self.buyer).write({'rate_date': '2026-11-30', 'tax_basis': 'unset'})
        self.assertFalse(event.is_normalized)
        event.with_user(self.buyer).write({'tax_basis': 'included'})
        self.assertTrue(event.is_normalized)
        event.line_ids.with_user(self.buyer).write({'comparison_uom_id': False})
        self.assertFalse(event.is_normalized)
