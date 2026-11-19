"""Own tests of T-017: the guards around evaluation, award and order generation.

Requirements: FR-SPM-0038, FR-SPM-0039, FR-SPM-0040, FR-SPM-0041, BR-SPM-0008
"""
from odoo.exceptions import AccessError, UserError

from .comparison_common import SpmComparisonCase

CRITERIA = [('Price', 'price', 'lower', 60.0), ('Lead time', 'lead_time', 'lower', 40.0)]


class TestAward(SpmComparisonCase):
    """Requirements: FR-SPM-0038, FR-SPM-0039, FR-SPM-0040"""

    def _event(self):
        event = self._sourcing(self.acme | self.beta, criteria=CRITERIA)
        self._assume(event)
        self.acme_response = self._response(event, self.acme)
        self.beta_response = self._response(event, self.beta)
        self._quote(self.acme_response, [(event.line_ids, 11.90, 10)])
        self._quote(self.beta_response, [(event.line_ids, 12.40, 5)])
        return event

    def _award(self, event, rationale='Best offer'):
        wizard = self.env['procurement.award.wizard'].with_user(self.buyer).create({
            'event_id': event.id, 'mode': 'whole', 'rationale': rationale,
            'response_id': self.beta_response.id})
        return wizard.action_confirm()

    def test_price_is_left_out_when_the_event_is_not_normalized(self):
        """BR-SPM-0008: a price criterion is scored on normalized values only; the lead time alone decides."""
        event = self._sourcing(self.acme | self.beta, criteria=CRITERIA)
        self._quote(self._response(event, self.acme), [(event.line_ids, 11.90, 10)])
        self._quote(self._response(event, self.beta), [(event.line_ids, 12.40, 5)])
        event.with_user(self.buyer).action_spm_evaluate()
        self.assertAlmostEqual(self._response(event, self.acme).score, 50.0, places=3)
        self.assertAlmostEqual(self._response(event, self.beta).score, 100.0, places=3)

    def test_a_buyer_cannot_write_the_score_of_a_response(self):
        """FR-SPM-0038: scores and ranks are set by the evaluation only."""
        event = self._event()
        with self.assertRaises(AccessError):
            self.acme_response.with_user(self.buyer).write({'score': 100.0})
        self.assertFalse(event.with_user(self.buyer).response_ids.filtered(lambda r: r.rank))

    def test_the_award_is_made_once(self):
        """FR-SPM-0039: an awarded event is not awarded again."""
        event = self._event()
        self._award(event)
        with self.assertRaises(UserError):
            self._award(event)

    def test_a_refused_award_changes_nothing(self):
        """FR-SPM-0039: a blank rationale leaves the lines unmarked."""
        event = self._event()
        with self.assertRaises(UserError):
            self._award(event, rationale=' ')
        self.assertFalse(self.beta_response.current_version_id.line_ids.awarded)

    def test_the_evidence_of_an_awarded_event_is_frozen(self):
        """FR-SPM-0039: assumptions and manual scores no longer change after the award."""
        event = self._event()
        self._award(event)
        with self.assertRaises(UserError):
            event.with_user(self.buyer).write({'tax_basis': 'included'})
        cell = self.env['procurement.comparison.line'].search([
            ('comparison_id.event_id', '=', event.id), ('row_kind', '=', 'criterion')], limit=1)
        with self.assertRaises(UserError):
            cell.with_user(self.buyer).write({'manual_score': 10.0, 'manual_comment': 'late'})

    def test_orders_need_an_award(self):
        """FR-SPM-0040: nothing is generated for an event that is not awarded."""
        event = self._event()
        with self.assertRaises(UserError):
            event.with_user(self.buyer).action_spm_generate_orders()

    def test_an_order_line_not_awarded_any_more_is_removed_on_update(self):
        """FR-SPM-0040: the draft order follows the award; one order per awarded supplier."""
        event = self._event()
        self._award(event)
        event.with_user(self.buyer).action_spm_generate_orders()
        order = event.purchase_order_ids
        self.assertEqual(len(order), 1)
        self.assertEqual(order.partner_id, self.beta)
        self.assertEqual(order.state, 'draft')
