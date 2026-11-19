"""Own tests of T-016: comparison rebuild, revision request guards, wizard, access.

Requirements: FR-SPM-0035, FR-SPM-0036, FR-SPM-0037, BR-SPM-0007
"""
from datetime import datetime

from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError
from odoo.tests import Form

from .comparison_common import SpmComparisonCase


class TestComparison(SpmComparisonCase):
    """Requirements: FR-SPM-0035, FR-SPM-0036, FR-SPM-0037, BR-SPM-0007"""

    def _two(self, **extra):
        event = self._sourcing(self.acme | self.beta, **extra)
        return event, event.line_ids, self._response(event, self.acme), self._response(event, self.beta)

    def test_rebuild_keeps_one_comparison_and_manual_scores(self):
        """FR-SPM-0035: a second Compare updates the same comparison and keeps manual scores."""
        event = self._sourcing(
            self.acme | self.beta, criteria=[('Service', 'manual', 'higher', 30.0)])
        acme = self._response(event, self.acme)
        self._quote(acme, [(event.line_ids, 12.0, 10)])
        comparison = self._compare(event)
        criterion = event.criterion_ids
        cell = self._cell(comparison, acme, 'criterion', criterion=criterion)
        cell.write({'manual_score': 7.0, 'manual_comment': 'Good references'})
        again = self._compare(event)
        self.assertEqual(again, comparison)
        kept = self._cell(again, acme, 'criterion', criterion=criterion)
        self.assertEqual((kept.manual_score, kept.manual_comment), (7.0, 'Good references'))

    def test_only_responses_with_a_quotation_are_compared(self):
        """FR-SPM-0035: an invited supplier with no quotation has no cell."""
        event, chairs, acme, beta = self._two()
        self._quote(acme, [(chairs, 12.0, 10)])
        comparison = self._compare(event)
        self.assertEqual(comparison.line_ids.response_id, acme)

    def test_raw_prices_in_two_currencies_are_not_highlighted(self):
        """BR-SPM-0007: without assumptions, 11.9 EUR and 13 USD are not ranked."""
        event, chairs, acme, beta = self._two()
        self._quote(acme, [(chairs, 11.9, 10)], currency=self.eur)
        self._quote(beta, [(chairs, 13.0, 5)], currency=self.usd)
        comparison = self._compare(event)
        self.assertFalse(comparison.line_ids.filtered('is_best').filtered(
            lambda c: c.row_kind in ('line', 'total')))

    def test_tax_included_basis_converts_the_price_with_its_tax(self):
        """FR-SPM-0037: with a tax-included basis the normalized price holds the tax."""
        event, chairs, acme, beta = self._two()
        self._assume(event)
        event.write({'tax_basis': 'included'})
        tax = self.env['account.tax'].create({
            'name': 'VAT 10 M3', 'amount': 10.0, 'amount_type': 'percent',
            'type_tax_use': 'purchase', 'company_id': self.company.id})
        version = self._quote(acme, [(chairs, 10.0, 10)])
        # a stored version is immutable: the tax link is inserted straight in the test setup
        self.env.cr.execute(
            'INSERT INTO procurement_sourcing_response_line_tax_rel (line_id, tax_id) VALUES (%s, %s)',
            [version.line_ids.id, tax.id])
        self.env.invalidate_all()
        comparison = self._compare(event)
        self.assertAlmostEqual(self._cell(comparison, acme, 'line', line=chairs).normalized_value, 11.0)

    def test_revision_refused_on_a_closed_event_or_an_early_deadline(self):
        """FR-SPM-0036: the event must be open and the new deadline later than the event's."""
        event, chairs, acme, beta = self._two()
        self._quote(acme, [(chairs, 12.0, 10)])
        with freeze_time('2026-11-20 08:00:00'):
            with self.assertRaises(UserError):
                acme.with_user(self.buyer).action_spm_request_revision(
                    'Please improve', datetime(2026, 11, 25, 12, 0, 0))
            event.with_user(self.buyer).action_spm_cancel('No longer needed')
            with self.assertRaises(UserError):
                acme.with_user(self.buyer).action_spm_request_revision(
                    'Please improve', datetime(2026, 12, 25, 12, 0, 0))
        self.assertFalse(event.negotiation_ids)

    def test_a_supplier_cannot_request_a_revision_or_compare(self):
        """NFR-SPM-0001: the portal user has no right on the comparison or the request."""
        event, chairs, acme, beta = self._two()
        self._quote(acme, [(chairs, 12.0, 10)])
        with freeze_time('2026-12-01 08:00:00'):
            with self.assertRaises(AccessError):
                acme.with_user(self.acme_user).action_spm_request_revision(
                    'Lower', datetime(2026, 12, 2, 12, 0, 0))
        with self.assertRaises(AccessError):
            event.with_user(self.acme_user).action_spm_compare()

    def test_the_revision_dialog_requests_the_revision(self):
        """FR-SPM-0036: the dialog values reach the request. A button is not pressed here
        (Form presses none); the Confirm button is left to the screen review."""
        event, chairs, acme, beta = self._two()
        self._quote(acme, [(chairs, 12.0, 10)])
        with freeze_time('2026-12-01 08:00:00'):
            action = acme.with_user(self.buyer).action_spm_open_revision_wizard()
            form = Form(self.env[action['res_model']].with_user(self.buyer).with_context(
                **action['context']))
            form.note_to_supplier = 'Best price?'
            form.new_deadline = datetime(2026, 12, 2, 12, 0, 0)
            wizard = form.save()
            wizard.action_confirm()
        self.assertEqual(event.negotiation_ids.note_to_supplier, 'Best price?')
        self.assertEqual(acme.state, 'reopened')

    def test_total_cost_criterion_on_a_normalized_event_has_one_best(self):
        """FR-SPM-0035 (Q1): a total cost criterion is ranked on normalized values; only the lowest wins."""
        event, chairs, acme, beta = self._two(criteria=[('Total cost', 'total_cost', 'lower', 1.0)])
        self._assume(event)
        self._quote(acme, [(chairs, 11.9, 10)])
        self._quote(beta, [(chairs, 12.4, 5)])
        comparison = self._compare(event)
        criterion = event.criterion_ids
        self.assertTrue(self._cell(comparison, acme, 'criterion', criterion=criterion).is_best)
        self.assertFalse(self._cell(comparison, beta, 'criterion', criterion=criterion).is_best)

    def test_an_incomplete_offer_never_wins_an_aggregate_row(self):
        """FR-SPM-0035 (Q2): a partial offer wins neither the price, total cost nor lead time rows."""
        criteria = [('Price', 'price', 'lower', 1.0), ('Cost', 'total_cost', 'lower', 1.0),
                    ('Lead', 'lead_time', 'lower', 1.0)]
        event = self._sourcing(self.acme | self.gamma, criteria=criteria, lines=[
            ('Chair', self.chair, 60.0), ('Desk', self.desk, 10.0)])
        chairs, desks = event.line_ids.sorted('id')
        acme, gamma = self._response(event, self.acme), self._response(event, self.gamma)
        self._quote(acme, [(chairs, 11.0, 10), (desks, 100.0, 12)])
        self._quote(gamma, [(chairs, 11.0, 2)])
        comparison = self._compare(event)
        for criterion in event.criterion_ids:
            partial = self._cell(comparison, gamma, 'criterion', criterion=criterion)
            self.assertTrue(partial.missing, criterion.name)
            self.assertFalse(partial.is_best, criterion.name)
            self.assertTrue(self._cell(comparison, acme, 'criterion', criterion=criterion).is_best)

    def test_term_rows_show_cost_lead_time_terms_warranty_and_validity(self):
        """FR-SPM-0035 (Q3): the fixed term rows appear without any configured criterion."""
        event, chairs, acme, beta = self._two()
        self._quote(acme, [(chairs, 11.9, 10)], warranty_months=12, validity_date='2026-12-31')
        self._quote(beta, [(chairs, 12.4, 5)], warranty_months=24)
        comparison = self._compare(event)
        terms = comparison.line_ids.filtered(lambda c: c.row_kind == 'term')
        self.assertEqual(set(terms.mapped('term_key')),
                         {'total_cost', 'lead_time', 'payment_terms', 'warranty', 'validity'})

        def term(response, key):
            return terms.filtered(lambda c: c.response_id == response and c.term_key == key)
        self.assertTrue(term(acme, 'total_cost').is_best)
        self.assertTrue(term(beta, 'lead_time').is_best)
        self.assertTrue(term(beta, 'warranty').is_best)
        self.assertEqual(term(acme, 'validity').raw_display, '2026-12-31')
        self.assertFalse(term(beta, 'validity').is_best)

    def test_read_only_roles_open_the_stored_comparison_and_an_award_freezes_it(self):
        """FR-SPM-0035 (Q4, Q6): finance opens the stored comparison; an awarded event is not rebuilt."""
        event, chairs, acme, beta = self._two()
        self._quote(acme, [(chairs, 11.9, 10)])
        with self.assertRaises(UserError):
            event.with_user(self.finance).action_spm_compare()
        comparison = self._compare(event)
        action = event.with_user(self.finance).action_spm_compare()
        self.assertEqual(action['res_id'], comparison.id)
        self._quote(beta, [(chairs, 12.4, 5)])
        event.sudo().write({'state': 'awarded'})
        self._compare(event)
        self.assertEqual(comparison.line_ids.response_id, acme)

    def test_a_buyer_cannot_write_the_reopened_deadline(self):
        """FR-SPM-0036 (Q5): the supplier's own deadline changes only through the revision request."""
        event, chairs, acme, beta = self._two()
        with self.assertRaises(AccessError):
            acme.with_user(self.buyer).write({'reopened_deadline': datetime(2027, 1, 1)})
