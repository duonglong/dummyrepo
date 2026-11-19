"""Own tests of T-024: portal routes, guards and the decisions the acceptance tests do not reach.

Requirements: FR-SPM-0024, FR-SPM-0025, FR-SPM-0058, FR-SPM-0060
The portal forms are posted as the supplier's user; a button of the back office is not pressed here
(the screen review does that): the methods the buttons call are.
"""
from datetime import date

from odoo.exceptions import AccessError, UserError

from .delivery_common import SpmDeliveryCase


class TestDeliveryProposal(SpmDeliveryCase):

    def _acknowledged(self):
        order = self._dorder(planned='2026-12-10 08:00:00')
        order.with_user(self.acme_user).spm_acknowledge({})
        return order, self._commitment(order)

    def test_portal_form_acknowledges_the_order(self):
        """FR-SPM-0024: posting the order page form records the user and the confirmed date."""
        order = self._dorder(planned='2026-12-10 08:00:00')
        self._login(self.acme_user)
        response = self._post(f'/my/procurement/orders/{order.id}/acknowledge',
                              {f'date_{order.order_line.id}': '2026-12-12'})
        self.assertEqual(response.status_code, 303)
        order.invalidate_recordset()
        self.assertEqual(order.spm_acknowledged_user_id, self.acme_user)
        self.assertEqual(self._commitment(order).confirmed_date.date(), date(2026, 12, 12))

    def test_portal_form_of_another_supplier_is_refused(self):
        """FR-SPM-0024, BR-SPM-0002: a Beta user posting for Acme's order changes nothing."""
        order = self._dorder()
        self._login(self.beta_user)
        response = self._post(f'/my/procurement/orders/{order.id}/acknowledge', {})
        self.assertIn(response.status_code, (403, 404))
        order.invalidate_recordset()
        self.assertFalse(order.spm_acknowledged_user_id)

    def test_portal_form_proposes_a_date(self):
        """FR-SPM-0025: the proposal form stores a pending proposal and leaves the confirmed date."""
        order, commitment = self._acknowledged()
        before = commitment.confirmed_date
        self._login(self.acme_user)
        self._post(f'/my/procurement/orders/{order.id}/propose', {
            'commitment_id': commitment.id, 'date': '2026-12-19', 'reason': 'Shortage'})
        proposal = commitment.proposal_ids
        self.assertEqual(proposal.state, 'pending')
        self.assertEqual(commitment.confirmed_date, before)

    def test_portal_form_proposes_a_split(self):
        """FR-SPM-0025: a second date and quantity make a split of the ordered quantity."""
        order, commitment = self._acknowledged()
        self._login(self.acme_user)
        self._post(f'/my/procurement/orders/{order.id}/propose', {
            'commitment_id': commitment.id, 'date': '2026-12-19', 'date_2': '2027-01-10',
            'quantity_2': '40', 'reason': 'Capacity'})
        self.assertEqual(sorted(commitment.proposal_ids.line_ids.mapped('quantity')), [40.0, 60.0])

    def test_direct_date_update_route_is_closed_for_a_supplier(self):
        """FR-SPM-0025: Odoo's own portal date route no longer moves the order line date."""
        order, commitment = self._acknowledged()
        planned = order.order_line.date_planned
        self._login(self.acme_user)
        self.make_jsonrpc_request(f'/my/purchase/{order.id}/update', {str(order.order_line.id): '2026-12-30'})
        order.order_line.invalidate_recordset()
        self.assertEqual(order.order_line.date_planned, planned)

    def test_second_acknowledgement_is_refused(self):
        """FR-SPM-0024: an acknowledged order is not acknowledged twice."""
        order, _commitment = self._acknowledged()
        with self.assertRaises(UserError):
            order.with_user(self.acme_user).spm_acknowledge({})

    def test_acknowledgement_fields_cannot_be_written_by_a_buyer(self):
        """FR-SPM-0024: the acknowledgement is set by the supplier's action only."""
        order = self._dorder()
        with self.assertRaises(AccessError):
            order.with_user(self.buyer).write({'spm_acknowledged_user_id': self.buyer.id})

    def test_proposal_cannot_be_decided_twice_or_written_directly(self):
        """FR-SPM-0058: a decided proposal stays decided; the state is not written by hand."""
        order, commitment = self._acknowledged()
        proposal = commitment.with_user(self.acme_user).spm_propose('Shortage', date='2026-12-19 08:00:00')
        with self.assertRaises(AccessError):
            proposal.with_user(self.buyer).write({'state': 'accepted'})
        proposal.with_user(self.buyer).action_spm_accept()
        with self.assertRaises(UserError):
            proposal.with_user(self.buyer).action_spm_refuse('Too late')

    def test_foreign_supplier_cannot_propose(self):
        """FR-SPM-0025, BR-SPM-0002: a Beta user proposing for Acme's line is refused."""
        _order, commitment = self._acknowledged()
        with self.assertRaises(AccessError):
            commitment.with_user(self.beta_user).spm_propose('Shortage', date='2026-12-19 08:00:00')

    def test_closing_a_discrepancy_closes_its_activity(self):
        """FR-SPM-0060: the close action sets the state and ends the responsible person's activity."""
        order, commitment = self._acknowledged()
        reason = self.env['procurement.discrepancy.reason'].search([('kind', '=', 'late')], limit=1)
        discrepancy = self.env['procurement.delivery.discrepancy'].with_user(self.buyer).create({
            'commitment_id': commitment.id, 'reason_id': reason.id, 'action': 'Chase',
            'responsible_id': self.manager.id, 'deadline': '2026-12-20'})
        discrepancy.action_spm_done()
        self.assertEqual(discrepancy.state, 'done')
        self.assertFalse(discrepancy.activity_ids)
