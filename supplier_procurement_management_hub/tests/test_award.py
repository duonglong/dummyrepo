"""Own tests of T-017: the guards around evaluation, award and order generation.

Requirements: FR-SPM-0038, FR-SPM-0039, FR-SPM-0040, FR-SPM-0041, BR-SPM-0008
"""
from odoo.exceptions import AccessError, UserError, ValidationError

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

    def test_one_draft_order_per_awarded_supplier(self):
        """FR-SPM-0040: generating orders gives one draft order to the awarded supplier."""
        event = self._event()
        self._award(event)
        event.with_user(self.buyer).action_spm_generate_orders()
        order = event.purchase_order_ids
        self.assertEqual(len(order), 1)
        self.assertEqual(order.partner_id, self.beta)
        self.assertEqual(order.state, 'draft')

    def _cells(self, event):
        return self.env['procurement.comparison.line'].search([('comparison_id.event_id', '=', event.id)])

    def test_the_evidence_of_an_awarded_event_cannot_be_deleted(self):
        """FR-SPM-0041: responses, cells, lines, criteria and the event itself survive a buyer's delete or edit."""
        event = self._event()
        self._award(event)
        self.assertTrue(self._cells(event))
        for records in (self.acme_response, self._cells(event), event.line_ids, event.criterion_ids, event):
            with self.assertRaises(UserError, msg=records._name):
                records.with_user(self.buyer).unlink()
        with self.assertRaises(UserError):
            self.acme_response.with_user(self.buyer).write({'partner_id': self.gamma.id})
        with self.assertRaises(UserError):
            self._cells(event)[:1].with_user(self.buyer).write({'score': 1.0})
        with self.assertRaises(UserError):
            event.line_ids.with_user(self.buyer).write({'quantity': 1.0})

    def test_a_manual_score_is_between_0_and_100(self):
        """BR-SPM-0008: -0.01 and 100.01 are refused, 0 and 100 are kept."""
        event = self._sourcing(self.acme | self.beta, criteria=[('Service', 'manual', 'higher', 1.0)])
        self._quote(self._response(event, self.acme), [(event.line_ids, 11.9, 10)])
        event.with_user(self.buyer).action_spm_evaluate()
        cell = self._cells(event).filtered(
            lambda c: c.row_kind == 'criterion' and c.response_id.partner_id == self.acme)
        for value in (-0.01, 100.01):
            with self.assertRaises(ValidationError):
                cell.with_user(self.buyer).write({'manual_score': value, 'manual_comment': 'x'})
        for value in (0.0, 100.0):
            cell.with_user(self.buyer).write({'manual_score': value, 'manual_comment': 'x'})
            self.assertEqual(cell.manual_score, value)

    def test_a_manual_score_is_used_as_given(self):
        """BR-SPM-0008: 70 and 35 stay 70 and 35; a given 0 with a comment is not missing."""
        event = self._sourcing(self.acme | self.beta, criteria=[('Service', 'manual', 'higher', 1.0)])
        self._quote(self._response(event, self.acme), [(event.line_ids, 11.9, 10)])
        self._quote(self._response(event, self.beta), [(event.line_ids, 12.4, 5)])
        event.with_user(self.buyer).action_spm_evaluate()
        cells = self._cells(event).filtered(lambda c: c.row_kind == 'criterion')
        by_partner = {c.response_id.partner_id: c for c in cells}
        by_partner[self.acme].with_user(self.buyer).write({'manual_score': 70.0, 'manual_comment': 'good'})
        by_partner[self.beta].with_user(self.buyer).write({'manual_score': 35.0, 'manual_comment': 'weak'})
        event.with_user(self.buyer).action_spm_evaluate()
        cells = self._cells(event).filtered(lambda c: c.row_kind == 'criterion')
        scores = {c.response_id.partner_id: c.score for c in cells}
        self.assertAlmostEqual(scores[self.acme], 70.0)
        self.assertAlmostEqual(scores[self.beta], 35.0)
        self.assertAlmostEqual(self._response(event, self.acme).score, 70.0)
        by_partner = {c.response_id.partner_id: c for c in cells}
        by_partner[self.beta].with_user(self.buyer).write({'manual_score': 0.0, 'manual_comment': 'none'})
        event.with_user(self.buyer).action_spm_evaluate()
        beta_cell = self._cells(event).filtered(
            lambda c: c.row_kind == 'criterion' and c.response_id.partner_id == self.beta)
        self.assertFalse(beta_cell.missing)

    def test_generating_again_updates_the_same_draft_order(self):
        """FR-SPM-0040: a second generation changes the linked draft, it does not add an order."""
        event = self._event()
        self._award(event)
        event.with_user(self.buyer).action_spm_generate_orders()
        order = event.purchase_order_ids
        order.order_line.write({'price_unit': 1.0})
        event.with_user(self.buyer).action_spm_generate_orders()
        self.assertEqual(event.purchase_order_ids, order)
        self.assertAlmostEqual(order.order_line.price_unit, 12.40)

    def test_a_confirmed_order_is_not_changed_when_a_draft_is_also_linked(self):
        """FR-SPM-0040: the draft is updated, the newer confirmed order keeps its prices."""
        event = self._event()
        self._award(event)
        event.with_user(self.buyer).action_spm_generate_orders()
        first = event.purchase_order_ids
        first.button_cancel()
        event.with_user(self.buyer).action_spm_generate_orders()
        second = event.purchase_order_ids - first
        second.button_confirm()
        first.button_draft()
        second.order_line.write({'price_unit': 99.0})
        event.with_user(self.buyer).action_spm_generate_orders()
        self.assertAlmostEqual(second.order_line.price_unit, 99.0)
        self.assertAlmostEqual(first.order_line.price_unit, 12.40)

    def test_a_line_without_product_is_refused_before_sending(self):
        """FR-SPM-0040: every line needs a product so its award can become an order line."""
        with self.assertRaises(UserError):
            self._sourcing(self.acme, lines=[('Free text', self.env['product.product'], 5.0)])

    def test_a_blocked_supplier_cannot_be_awarded(self):
        """FR-SPM-0013: with supplier control on, an award to a blocked supplier is refused."""
        event = self._event()
        self.company.spm_supplier_control = True
        self.beta.write({'spm_status_id': self.blocked.id})
        with self.assertRaises(UserError):
            self._award(event)
        self.assertEqual(event.state, 'open')

    def test_the_award_writes_an_audit_entry_with_the_rationale(self):
        """BR-SPM-0018: one protected entry per award, the rationale as its comment."""
        event = self._event()
        self._award(event, rationale='Cheapest and fastest')
        entry = self.env['procurement.audit.log'].search([
            ('event', '=', 'award'), ('res_model', '=', event._name), ('res_id', '=', event.id)])
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry.comment, 'Cheapest and fastest')

    def test_an_auditor_can_read_an_awarded_event_with_orders(self):
        """FR-SPM-0040: the order list is for purchase users, so read-only roles still open the event."""
        event = self._event()
        self._award(event)
        event.with_user(self.buyer).action_spm_generate_orders()
        auditor = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Al Auditor', 'login': 'spm_auditor_t17',
            'group_ids': [(6, 0, [self.env.ref('supplier_procurement_management_hub.group_spm_auditor').id])]})
        as_auditor = event.with_user(auditor)
        self.assertNotIn('purchase_order_ids', as_auditor.fields_get())
        self.assertEqual(as_auditor.read(['title', 'state'])[0]['state'], 'awarded')
