"""Own tests of T-023: guards around schedules and shipment events.

Requirements: FR-SPM-0055, FR-SPM-0056, FR-SPM-0057
"""
from freezegun import freeze_time

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
        self.assertEqual(event.origin, 'backoffice')
        self.assertEqual(event.user_id, self.buyer)

    def test_f1_line_bought_in_dozens_and_fully_received_is_done(self):
        """Requirements: FR-SPM-0055, FR-SPM-0059. 10 dozens fully received: nothing remains, not overdue."""
        order = self._dorder(qty=10.0, confirm=False)
        order.order_line.uom_id = self.env.ref('uom.product_uom_dozen')
        order.button_confirm()
        if order.state != 'purchase':
            order.button_approve(force=True)
        commitment = self._commitment(order)
        self.assertEqual(commitment.ordered_qty, 10.0)
        self._receive(order, 120.0, '2026-12-09 10:00:00')  # the receipt move counts units: 10 dozens
        with freeze_time('2027-01-20 09:00:00'):
            commitment.invalidate_recordset()
            self.assertEqual(commitment.remaining_qty, 0.0)
            self.assertEqual(commitment.state, 'done')
            self.assertFalse(commitment.is_partial)
            self.assertEqual(commitment.overdue_days, 0)

    def test_f2_cancelled_order_is_not_overdue(self):
        """Requirements: FR-SPM-0059. A cancelled order's commitment shows no late days and is not found as overdue."""
        order = self._dorder()
        commitment = self._commitment(order)
        order.button_cancel()
        with freeze_time('2027-01-20 09:00:00'):
            commitment.invalidate_recordset()
            self.assertEqual(commitment.state, 'cancel')
            self.assertEqual(commitment.overdue_days, 0)
            found = self.env['procurement.delivery.commitment'].search([('is_overdue', '=', True)])
            self.assertNotIn(commitment, found)

    def test_q3_overdue_search_follows_the_users_day(self):
        """Requirements: FR-SPM-0059. Overdue found by the user's own day, like the computed days."""
        order = self._dorder(planned='2026-12-10 20:00:00')
        commitment = self._commitment(order)
        commitments = self.env['procurement.delivery.commitment'].with_user(self.buyer)
        self.buyer.tz = 'Asia/Tokyo'  # 2026-12-11 05:00 local: the agreed day is Dec 11
        with freeze_time('2026-12-11 10:00:00'):  # Tokyo day Dec 11: not yet late
            self.assertNotIn(commitment, commitments.search([('is_overdue', '=', True)]))
            self.assertEqual(commitment.with_user(self.buyer).overdue_days, 0)
        with freeze_time('2026-12-12 10:00:00'):  # Tokyo day Dec 12: one day late
            commitment.invalidate_recordset()
            self.assertIn(commitment, commitments.search([('is_overdue', '=', True)]))
            self.assertEqual(commitment.with_user(self.buyer).overdue_days, 1)
            self.assertNotIn(commitment, commitments.search([('is_overdue', '!=', True)]))

    def test_q1_event_refused_to_read_only_internal_user(self):
        """Requirements: FR-SPM-0057. A finance user who can read the order cannot record an event."""
        order = self._dorder()
        events = self.env['procurement.delivery.event']
        with self.assertRaises(AccessError):
            events.with_user(self.finance).spm_record(order, 'shipped', '2026-12-08 09:00:00')

    def test_q1_event_refused_to_portal_user_of_a_non_supplier(self):
        """Requirements: FR-SPM-0057, BR-SPM-0002. A portal user of a contact that is no hub supplier is refused."""
        order = self._dorder()
        self.acme.sudo().spm_is_supplier = False
        with self.assertRaises(AccessError):
            self.env['procurement.delivery.event'].with_user(self.acme_user).spm_record(
                order, 'shipped', '2026-12-08 09:00:00')

    def test_q2_schedule_check_cannot_be_switched_off_by_context(self):
        """Requirements: FR-SPM-0056. The context key of the old batch mode no longer skips the check."""
        commitment = self._commitment(self._dorder(qty=100.0))
        commitment.write({'schedule_ids': [
            (0, 0, {'date': '2026-12-10 08:00:00', 'quantity': 60.0}),
            (0, 0, {'date': '2027-01-10 08:00:00', 'quantity': 40.0}),
        ]})
        first = commitment.schedule_ids[0].with_context(spm_schedule_batch=True)
        with self.assertRaises(ValidationError):
            first.quantity = 50.0
        with self.assertRaises(ValidationError):
            first.unlink()

    def test_q6_direct_event_create_on_draft_order_is_refused(self):
        """Requirements: FR-SPM-0057. The confirmed-order rule holds for a direct create too."""
        order = self._dorder(confirm=False)
        with self.assertRaises(UserError):
            self.env['procurement.delivery.event'].with_user(self.buyer).create({
                'order_id': order.id, 'kind': 'delayed', 'date': '2026-12-09 09:00:00'})

    def test_q5_buyer_cannot_overwrite_receipt_facts(self):
        """Requirements: FR-SPM-0055. A buyer edits the confirmed date, not the quantities."""
        commitment = self._commitment(self._dorder()).with_user(self.buyer)
        commitment.confirmed_date = '2026-12-12 08:00:00'
        with self.assertRaises(AccessError):
            commitment.write({'received_qty': 100.0})
