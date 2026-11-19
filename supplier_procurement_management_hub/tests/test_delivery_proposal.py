"""Own tests of T-024: portal routes, guards and the decisions the acceptance tests do not reach.

Requirements: FR-SPM-0024, FR-SPM-0025, FR-SPM-0058, FR-SPM-0060
The portal forms are posted as the supplier's user; a button of the back office is not pressed here
(the screen review does that): the methods the buttons call are.
"""
from datetime import date

from odoo.exceptions import AccessError, UserError
from odoo.tests import Form

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

    def test_access_link_without_login_cannot_move_a_date(self):
        """FR-SPM-0025: the emailed order link of Odoo's own date route changes no date for a public visitor."""
        order, commitment = self._acknowledged()
        planned, agreed = order.order_line.date_planned, commitment.agreed_date
        self.authenticate(None, None)
        self.make_jsonrpc_request(f'/my/purchase/{order.id}/update', {
            'access_token': order._portal_ensure_token(), str(order.order_line.id): '2026-12-30'})
        order.order_line.invalidate_recordset()
        commitment.invalidate_recordset()
        self.assertEqual((order.order_line.date_planned, commitment.agreed_date), (planned, agreed))

    def test_buyer_saves_a_discrepancy_typed_on_the_delivery_line(self):
        """FR-SPM-0060: the commitment form of the Buyer saves a new discrepancy row (Form saves; no button is pressed)."""
        _order, commitment = self._acknowledged()
        reason = self.env['procurement.discrepancy.reason'].search([('kind', '=', 'short')], limit=1)
        with Form(commitment.with_user(self.buyer)) as form:
            with form.discrepancy_ids.new() as row:
                row.reason_id, row.quantity, row.action = reason, 5, 'Ask credit note'
                row.responsible_id, row.deadline = self.manager, '2026-12-20'
        self.assertEqual(commitment.discrepancy_ids.action, 'Ask credit note')
        with self.assertRaises(AccessError):
            commitment.with_user(self.buyer).write({'ordered_qty': 1})

    def test_order_page_ignores_a_message_written_in_the_address(self):
        """BR-SPM-0004: a status text in the address bar is never shown; the page reads the visitor's session."""
        order, _commitment = self._acknowledged()
        self._login(self.acme_user)
        page = self._get(f'/my/purchase/{order.id}?spm_error=Send+your+bank+details&spm_done=Fake+done')
        self.assertNotIn('Send your bank details', page.text)
        self.assertNotIn('Fake done', page.text)

    def test_refusal_words_reach_the_order_page_once(self):
        """FR-SPM-0025: a refused proposal shows its reason on the order page, then the banner is gone."""
        order, commitment = self._acknowledged()
        self._login(self.acme_user)
        self._post(f'/my/procurement/orders/{order.id}/propose', {
            'commitment_id': commitment.id, 'date': '2026-12-19', 'reason': ''})
        self.assertIn('Give the reason of the new date', self._get(f'/my/purchase/{order.id}').text)
        self.assertNotIn('Give the reason of the new date', self._get(f'/my/purchase/{order.id}').text)

    def test_bad_split_input_returns_to_the_order_page(self):
        """FR-SPM-0025: an empty or too large second quantity and a missing first date make no proposal and no error page."""
        order, commitment = self._acknowledged()
        self._login(self.acme_user)
        for extra in ({'quantity_2': ''}, {'quantity_2': '100'}, {'quantity_2': '40', 'date': ''}):
            data = {'commitment_id': commitment.id, 'date': '2026-12-19', 'date_2': '2027-01-10', 'reason': 'Capacity'}
            response = self._post(f'/my/procurement/orders/{order.id}/propose', dict(data, **extra))
            self.assertEqual(response.status_code, 303)
        self.assertFalse(commitment.proposal_ids)

    def test_proposal_guards(self):
        """FR-SPM-0025, FR-SPM-0058: one pending proposal per line, a split must total the order, a refusal needs a reason."""
        _order, commitment = self._acknowledged()
        supplier = commitment.with_user(self.acme_user)
        with self.assertRaises(UserError):
            supplier.spm_propose('Capacity', lines=[
                {'date': '2026-12-19 08:00:00', 'quantity': 100}, {'date': '2027-01-10 08:00:00', 'quantity': 0}])
        with self.assertRaises(Exception):
            supplier.spm_propose('Capacity', lines=[
                {'date': '2026-12-19 08:00:00', 'quantity': 50}, {'date': '2027-01-10 08:00:00', 'quantity': 10}])
        proposal = supplier.spm_propose('Shortage', date='2026-12-19 08:00:00')
        with self.assertRaises(UserError):
            supplier.spm_propose('Again', date='2026-12-20 08:00:00')
        with self.assertRaises(UserError):
            proposal.with_user(self.buyer).action_spm_refuse('  ')

    def test_refuse_dialog_forwards_its_reason(self):
        """FR-SPM-0058: the reason dialog refuses the proposal (Form fills it; the button is for the screen review)."""
        _order, commitment = self._acknowledged()
        proposal = commitment.with_user(self.acme_user).spm_propose('Shortage', date='2026-12-19 08:00:00')
        action = proposal.with_user(self.buyer).action_spm_open_refuse_wizard()
        with Form(self.env['procurement.reason.wizard'].with_user(self.buyer).with_context(**action['context'])) as form:
            form.reason = 'Production needs the chairs on time'
        form.save().action_confirm()
        self.assertEqual((proposal.state, proposal.refuse_reason), ('refused', 'Production needs the chairs on time'))
