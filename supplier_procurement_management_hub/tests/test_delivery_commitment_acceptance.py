"""Acceptance tests of T-023 (delivery commitments, schedules, shipment events, overdue view).

Requirements: BR-SPM-0012, FR-SPM-0055, FR-SPM-0056, FR-SPM-0057, FR-SPM-0059, FR-SPM-0061

Written from the specification. Receipts are validated through Odoo's own receipt
(button_validate); the app never creates a stock move. The supplier's portal
route for shipment events belongs to the portal task (T-024); here the event is
recorded through the model method as the supplier's portal user.
"""
from datetime import date

from freezegun import freeze_time

from odoo.exceptions import ValidationError

from .delivery_common import SpmDeliveryCase


class TestDeliveryCommitmentAcceptance(SpmDeliveryCase):

    # FR-SPM-0055, BR-SPM-0012 ---------------------------------------------
    def test_sc_0055_1_commitment_brings_dates_and_quantities_together(self):
        """Requirements: FR-SPM-0055. SC-SPM-0055-1: 100 chairs expected 2026-12-10, confirmed
        2026-12-12, a done receipt of 60 on 2026-12-11."""
        order = self._dorder(qty=100.0, planned='2026-12-10 08:00:00')
        commitment = self._commitment(order)
        self.assertEqual(len(commitment), 1, "one commitment per goods line at confirmation")
        commitment.confirmed_date = '2026-12-12 08:00:00'
        self._receive(order, 60.0, '2026-12-11 10:00:00')
        commitment.invalidate_recordset()
        self.assertEqual(commitment.expected_date.date(), date(2026, 12, 10))
        self.assertEqual(commitment.confirmed_date.date(), date(2026, 12, 12))
        self.assertEqual(commitment.actual_date.date(), date(2026, 12, 11))
        self.assertEqual(commitment.ordered_qty, 100.0)
        self.assertEqual(commitment.received_qty, 60.0)
        self.assertEqual(commitment.remaining_qty, 40.0)

    def test_br_0012_agreed_date_is_confirmed_date_else_expected(self):
        """Requirements: BR-SPM-0012: the agreed date is the supplier-confirmed date when there
        is one and the expected date otherwise."""
        order = self._dorder(planned='2026-12-10 08:00:00')
        commitment = self._commitment(order)
        self.assertEqual(commitment.agreed_date.date(), date(2026, 12, 10))
        commitment.confirmed_date = '2026-12-12 08:00:00'
        self.assertEqual(commitment.agreed_date.date(), date(2026, 12, 12))

    # FR-SPM-0056 -----------------------------------------------------------
    def test_sc_0056_1_split_into_two_schedules(self):
        """Requirements: FR-SPM-0056. SC-SPM-0056-1: 100 chairs split into 60 on 2026-12-10 and 40 on 2027-01-10."""
        commitment = self._commitment(self._dorder(qty=100.0))
        commitment.write({'schedule_ids': [
            (0, 0, {'date': '2026-12-10 08:00:00', 'quantity': 60.0}),
            (0, 0, {'date': '2027-01-10 08:00:00', 'quantity': 40.0}),
        ]})
        self.env.flush_all()
        schedules = commitment.schedule_ids.sorted('date')
        self.assertEqual(len(schedules), 2)
        self.assertEqual(schedules.mapped('quantity'), [60.0, 40.0])
        self.assertEqual([d.date() for d in schedules.mapped('date')],
                         [date(2026, 12, 10), date(2027, 1, 10)])
        self.assertEqual(schedules.mapped('received_qty'), [0.0, 0.0])

    def test_sc_0056_2_split_not_adding_up_is_refused(self):
        """Requirements: FR-SPM-0056. SC-SPM-0056-2: 60 and 30 of 100 is refused with the totals."""
        commitment = self._commitment(self._dorder(qty=100.0))
        with self.assertRaises(ValidationError) as caught:
            commitment.write({'schedule_ids': [
                (0, 0, {'date': '2026-12-10 08:00:00', 'quantity': 60.0}),
                (0, 0, {'date': '2027-01-10 08:00:00', 'quantity': 30.0}),
            ]})
            self.env.flush_all()
        message = str(caught.exception)
        self.assertIn('90', message)
        self.assertIn('100', message)
        self.assertFalse(commitment.schedule_ids)

    # FR-SPM-0057 -----------------------------------------------------------
    def test_sc_0057_1_portal_user_records_shipment_event(self):
        """Requirements: FR-SPM-0057. SC-SPM-0057-1: an Acme portal user records 'shipped' on
        2026-12-08, carrier DHL, reference 1Z999, arrival 2026-12-11."""
        order = self._dorder()
        events = self.env['procurement.delivery.event'].with_user(self.acme_user)
        events.spm_record(order, 'delivered', '2026-12-11 09:00:00')
        events.spm_record(order, 'shipped', '2026-12-08 09:00:00', reference='1Z999',
                          carrier='DHL', eta='2026-12-11 09:00:00')
        self.env.flush_all()
        history = order.spm_delivery_event_ids
        self.assertEqual(len(history), 2)
        self.assertEqual(history.mapped('kind'), ['shipped', 'delivered'], "history in date order")
        shipped = history[0]
        self.assertEqual(shipped.shipment_reference, '1Z999')
        self.assertEqual(shipped.carrier, 'DHL')
        self.assertEqual(shipped.eta.date(), date(2026, 12, 11))
        self.assertEqual(shipped.date.date(), date(2026, 12, 8))
        self.assertEqual(shipped.origin, 'portal')
        self.assertEqual(shipped.user_id, self.acme_user)

    # FR-SPM-0059 -----------------------------------------------------------
    def test_sc_0059_1_overdue_partial_and_backorder(self):
        """Requirements: FR-SPM-0059. SC-SPM-0059-1: confirmed 2026-12-12, 60 of 100 received and
        a backorder for 40; on 2026-12-15 the line is overdue by 3 days, partial and backorder."""
        order = self._dorder(qty=100.0)
        commitment = self._commitment(order)
        commitment.confirmed_date = '2026-12-12 08:00:00'
        self._receive(order, 60.0, '2026-12-11 10:00:00', backorder=True)
        with freeze_time('2026-12-15 09:00:00'):
            commitment.invalidate_recordset()
            self.assertEqual(commitment.overdue_days, 3)
            self.assertTrue(commitment.is_partial)
            self.assertTrue(commitment.has_backorder)

    def test_sc_0059_2_fully_received_line_is_not_overdue(self):
        """Requirements: FR-SPM-0059. SC-SPM-0059-2: confirmed 2026-12-12, fully received 2026-12-14."""
        order = self._dorder(qty=100.0)
        commitment = self._commitment(order)
        commitment.confirmed_date = '2026-12-12 08:00:00'
        self._receive(order, 100.0, '2026-12-14 10:00:00')
        with freeze_time('2026-12-15 09:00:00'):
            commitment.invalidate_recordset()
            self.assertFalse(commitment.overdue_days)
            self.assertFalse(commitment.is_partial)
            self.assertFalse(commitment.has_backorder)

    def test_fr_0059_not_overdue_on_the_agreed_date(self):
        """Requirements: FR-SPM-0059. Boundary: on the agreed date itself nothing is late;
        a day later it is one day late."""
        order = self._dorder(qty=100.0)
        commitment = self._commitment(order)
        commitment.confirmed_date = '2026-12-12 08:00:00'
        with freeze_time('2026-12-12 20:00:00'):
            commitment.invalidate_recordset()
            self.assertFalse(commitment.overdue_days)
        with freeze_time('2026-12-13 09:00:00'):
            commitment.invalidate_recordset()
            self.assertEqual(commitment.overdue_days, 1)

    # FR-SPM-0061 -----------------------------------------------------------
    def test_sc_0061_1_no_stock_move_is_created_by_the_app(self):
        """Requirements: FR-SPM-0061. SC-SPM-0061-1: commitments, schedules and shipment events
        add no stock move to the order's own."""
        order = self._dorder(qty=100.0)
        commitment = self._commitment(order)
        self.assertTrue(commitment, "the commitment exists")
        moves = self._move_count(order)
        pickings = len(order.picking_ids)
        self.assertEqual(moves, 1)
        commitment.write({'schedule_ids': [
            (0, 0, {'date': '2026-12-10 08:00:00', 'quantity': 60.0}),
            (0, 0, {'date': '2027-01-10 08:00:00', 'quantity': 40.0}),
        ]})
        self.env['procurement.delivery.event'].with_user(self.acme_user).spm_record(
            order, 'shipped', '2026-12-08 09:00:00', carrier='DHL')
        self.env.flush_all()
        self.assertEqual(self._move_count(order), moves)
        self.assertEqual(len(order.picking_ids), pickings)

    def test_sc_0061_2_returned_quantity_reduces_received(self):
        """Requirements: FR-SPM-0061. SC-SPM-0061-2: 10 chairs of a received line are returned
        through Odoo's return: received 90, remaining 10."""
        order = self._dorder(qty=100.0)
        commitment = self._commitment(order)
        picking = self._receive(order, 100.0, '2026-12-11 10:00:00')
        commitment.invalidate_recordset()
        self.assertEqual(commitment.received_qty, 100.0)
        self._return(picking, 10.0)
        commitment.invalidate_recordset()
        self.assertEqual(commitment.received_qty, 90.0)
        self.assertEqual(commitment.remaining_qty, 10.0)
