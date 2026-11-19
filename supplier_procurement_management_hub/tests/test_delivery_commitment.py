"""Own tests of T-023: guards around schedules and shipment events.

Requirements: FR-SPM-0055, FR-SPM-0056, FR-SPM-0057
"""
from odoo.exceptions import AccessError, UserError, ValidationError

from .delivery_common import SpmDeliveryCase


class TestDeliveryCommitment(SpmDeliveryCase):

    def test_one_commitment_per_goods_line_and_none_for_a_draft_order(self):
        """Requirements: FR-SPM-0055. A draft order has no commitment; a confirmed one has one per line."""
        draft = self._dorder(confirm=False)
        self.assertFalse(self._commitment(draft))
        order = self._dorder()
        self.assertEqual(len(self._commitment(order)), 1)
        order._spm_ensure_commitments()
        self.assertEqual(len(self._commitment(order)), 1)

    def test_schedule_changed_alone_is_checked(self):
        """Requirements: FR-SPM-0056. Changing one schedule outside the commitment write is refused."""
        commitment = self._commitment(self._dorder(qty=100.0))
        commitment.write({'schedule_ids': [
            (0, 0, {'date': '2026-12-10 08:00:00', 'quantity': 60.0}),
            (0, 0, {'date': '2027-01-10 08:00:00', 'quantity': 40.0}),
        ]})
        with self.assertRaises(ValidationError):
            commitment.schedule_ids[0].quantity = 50.0
        with self.assertRaises(ValidationError):
            commitment.schedule_ids[0].unlink()
        commitment.write({'schedule_ids': [
            (1, commitment.schedule_ids[0].id, {'quantity': 50.0}),
            (1, commitment.schedule_ids[1].id, {'quantity': 50.0}),
        ]})
        self.assertEqual(commitment.schedule_ids.mapped('quantity'), [50.0, 50.0])

    def test_received_quantity_fills_the_schedules_in_date_order(self):
        """Requirements: FR-SPM-0056. 70 received of 60 + 40 fills the first and part of the second."""
        order = self._dorder(qty=100.0)
        commitment = self._commitment(order)
        commitment.write({'schedule_ids': [
            (0, 0, {'date': '2027-01-10 08:00:00', 'quantity': 40.0}),
            (0, 0, {'date': '2026-12-10 08:00:00', 'quantity': 60.0}),
        ]})
        self._receive(order, 70.0, '2026-12-11 10:00:00', backorder=True)
        commitment.schedule_ids.invalidate_recordset()
        by_qty = {s.quantity: s for s in commitment.schedule_ids}
        self.assertEqual(by_qty[60.0].received_qty, 60.0)
        self.assertEqual(by_qty[40.0].received_qty, 10.0)
        self.assertEqual(by_qty[40.0].remaining_qty, 30.0)

    def test_event_refused_to_a_user_who_cannot_read_the_order(self):
        """Requirements: FR-SPM-0057. Another supplier's portal user cannot record on the order."""
        order = self._dorder()
        events = self.env['procurement.delivery.event']
        with self.assertRaises(AccessError):
            events.with_user(self.beta_user).spm_record(order, 'shipped', '2026-12-08 09:00:00')

    def test_event_refused_on_an_unconfirmed_order(self):
        """Requirements: FR-SPM-0057. A draft order takes no shipment event."""
        order = self._dorder(confirm=False)
        with self.assertRaises(UserError):
            self.env['procurement.delivery.event'].with_user(self.buyer).spm_record(
                order, 'shipped', '2026-12-08 09:00:00')

    def test_event_author_and_origin_follow_the_session(self):
        """Requirements: FR-SPM-0057. A buyer's direct create cannot claim a portal origin."""
        order = self._dorder()
        event = self.env['procurement.delivery.event'].with_user(self.buyer).create({
            'order_id': order.id, 'kind': 'delayed', 'date': '2026-12-09 09:00:00',
            'origin': 'portal', 'user_id': self.acme_user.id})
        self.assertEqual(event.origin, 'buyer')
        self.assertEqual(event.user_id, self.buyer)
