"""Acceptance tests of T-024 (acknowledgement, delivery date proposals, discrepancies).

Requirements: FR-SPM-0024, FR-SPM-0025, FR-SPM-0058, FR-SPM-0060, FR-SPM-0061

Written from the specification. The portal pages (/my/procurement/orders/...) and
the refuse dialog are left to the build: the tests call the model methods named
in delivery_common as the supplier's portal user or the Buyer.
"""
from datetime import date

from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError, ValidationError

from .delivery_common import SpmDeliveryCase


class TestDeliveryProposalAcceptance(SpmDeliveryCase):

    def _pending_proposal(self, order, new_date='2026-12-19 08:00:00', **kwargs):
        commitment = self._commitment(order)
        proposal = commitment.with_user(self.acme_user).spm_propose(
            'Raw material shortage', date=new_date, **kwargs)
        self.env.flush_all()
        return commitment, proposal

    def _confirmed_order(self):
        """Order P of Acme, confirmed by the supplier for 2026-12-12."""
        order = self._dorder(qty=100.0, planned='2026-12-10 08:00:00')
        order.with_user(self.acme_user).spm_acknowledge({order.order_line.id: date(2026, 12, 12)})
        return order

    # FR-SPM-0024 -----------------------------------------------------------
    def test_sc_0024_1_acknowledgement_records_user_time_and_dates(self):
        """Requirements: FR-SPM-0024. SC-SPM-0024-1: a line expected 2026-12-10; the Acme portal
        user acknowledges confirming 2026-12-12."""
        order = self._dorder(planned='2026-12-10 08:00:00')
        with freeze_time('2026-12-01 10:30:00'):
            order.with_user(self.acme_user).spm_acknowledge({order.order_line.id: date(2026, 12, 12)})
        order.invalidate_recordset()
        self.assertEqual(order.spm_acknowledged_user_id, self.acme_user)
        self.assertEqual(str(order.spm_acknowledged_date), '2026-12-01 10:30:00')
        self.assertEqual(self._commitment(order).confirmed_date.date(), date(2026, 12, 12))

    def test_fr_0024_unspecified_line_defaults_to_expected_date(self):
        """Requirements: FR-SPM-0024: a line given no date is confirmed for its expected date."""
        order = self._dorder(planned='2026-12-10 08:00:00')
        order.with_user(self.acme_user).spm_acknowledge({})
        self.assertEqual(self._commitment(order).confirmed_date.date(), date(2026, 12, 10))
        self.assertEqual(order.spm_acknowledged_user_id, self.acme_user)

    def test_sc_0024_2_draft_order_cannot_be_acknowledged(self):
        """Requirements: FR-SPM-0024. SC-SPM-0024-2: order still in draft."""
        order = self._dorder(confirm=False)
        self.assertEqual(order.state, 'draft')
        with self.assertRaises((UserError, AccessError)):
            order.with_user(self.acme_user).spm_acknowledge({})
        self.assertFalse(order.spm_acknowledged_user_id)

    def test_fr_0024_other_suppliers_order_cannot_be_acknowledged(self):
        """Requirements: FR-SPM-0024, BR-SPM-0002: a Beta portal user cannot acknowledge Acme's order."""
        order = self._dorder()
        with self.assertRaises((UserError, AccessError)):
            order.with_user(self.beta_user).spm_acknowledge({})
        self.assertFalse(order.spm_acknowledged_user_id)

    # FR-SPM-0025 -----------------------------------------------------------
    def test_sc_0025_1_proposal_is_pending_and_changes_nothing(self):
        """Requirements: FR-SPM-0025. SC-SPM-0025-1: line confirmed 2026-12-12; Acme proposes
        2026-12-19 for 'Raw material shortage'."""
        order = self._confirmed_order()
        commitment, proposal = self._pending_proposal(order)
        self.assertEqual(proposal.state, 'pending')
        self.assertEqual(proposal.reason, 'Raw material shortage')
        self.assertEqual(proposal.date.date(), date(2026, 12, 19))
        self.assertEqual(proposal.commitment_id, commitment)
        commitment.invalidate_recordset()
        self.assertEqual(commitment.confirmed_date.date(), date(2026, 12, 12))
        activities = self.env['mail.activity'].search([('user_id', '=', self.buyer.id)]).filtered(
            lambda a: (a.res_model, a.res_id) in (
                ('purchase.order', order.id), ('procurement.delivery.proposal', proposal.id),
                ('procurement.delivery.commitment', commitment.id)))
        self.assertTrue(activities, "the order's buyer gets an activity")

    def test_fr_0025_split_proposal_must_add_up_to_the_ordered_quantity(self):
        """Requirements: FR-SPM-0025, FR-SPM-0056: a split proposal of 60 and 30 for 100 chairs is refused."""
        order = self._confirmed_order()
        commitment = self._commitment(order)
        with self.assertRaises(ValidationError):
            commitment.with_user(self.acme_user).spm_propose('Capacity', lines=[
                {'date': '2026-12-19 08:00:00', 'quantity': 60.0},
                {'date': '2027-01-10 08:00:00', 'quantity': 30.0}])
            self.env.flush_all()

    # FR-SPM-0058 -----------------------------------------------------------
    def test_sc_0058_1_accepting_moves_confirmed_date_and_receipt_move(self):
        """Requirements: FR-SPM-0058. SC-SPM-0058-1: Acme proposes to move a line from 2026-12-12
        to 2026-12-19; the Buyer accepts."""
        order = self._confirmed_order()
        commitment, proposal = self._pending_proposal(order)
        proposal.with_user(self.buyer).action_spm_accept()
        self.env.flush_all()
        commitment.invalidate_recordset()
        proposal.invalidate_recordset()
        self.assertEqual(commitment.confirmed_date.date(), date(2026, 12, 19))
        self.assertEqual(proposal.state, 'accepted')
        self.assertEqual(proposal.decided_by_id, self.buyer)
        moves = order.picking_ids.move_ids.filtered(lambda m: m.state not in ('done', 'cancel'))
        self.assertTrue(moves)
        self.assertEqual({m.date.date() for m in moves}, {date(2026, 12, 19)})

    def test_fr_0058_accepted_split_proposal_sets_the_schedules(self):
        """Requirements: FR-SPM-0058: accepting a split proposal (60 on 2026-12-19, 40 on 2027-01-10)
        replaces the schedules with it."""
        order = self._confirmed_order()
        commitment = self._commitment(order)
        proposal = commitment.with_user(self.acme_user).spm_propose('Capacity', lines=[
            {'date': '2026-12-19 08:00:00', 'quantity': 60.0},
            {'date': '2027-01-10 08:00:00', 'quantity': 40.0}])
        proposal.with_user(self.buyer).action_spm_accept()
        self.env.flush_all()
        schedules = commitment.schedule_ids.sorted('date')
        self.assertEqual(schedules.mapped('quantity'), [60.0, 40.0])
        self.assertEqual([d.date() for d in schedules.mapped('date')],
                         [date(2026, 12, 19), date(2027, 1, 10)])

    def test_sc_0058_2_refusing_keeps_dates_and_tells_the_supplier(self):
        """Requirements: FR-SPM-0058. SC-SPM-0058-2: the Buyer refuses with 'Production needs the
        chairs on time'; the confirmed date stays 2026-12-12 and Acme is notified with the reason."""
        order = self._confirmed_order()
        commitment, proposal = self._pending_proposal(order)
        reason = 'Production needs the chairs on time'
        proposal.with_user(self.buyer).action_spm_refuse(reason)
        self.env.flush_all()
        commitment.invalidate_recordset()
        proposal.invalidate_recordset()
        self.assertEqual(commitment.confirmed_date.date(), date(2026, 12, 12))
        self.assertEqual(proposal.state, 'refused')
        self.assertEqual(proposal.refuse_reason, reason)
        self.assertTrue(
            self._sent_to(self.acme_user.partner_id, reason) or self._sent_to(self.acme, reason),
            "Acme is notified with the reason")

    def test_fr_0058_refusing_requires_a_reason(self):
        """Requirements: FR-SPM-0058: a refusal without a reason is not accepted."""
        order = self._confirmed_order()
        commitment, proposal = self._pending_proposal(order)
        with self.assertRaises((UserError, ValidationError)):
            proposal.with_user(self.buyer).action_spm_refuse('')
            self.env.flush_all()
        self.assertEqual(proposal.state, 'pending')

    # FR-SPM-0060 -----------------------------------------------------------
    def test_sc_0060_1_discrepancy_is_stored_with_an_activity(self):
        """Requirements: FR-SPM-0060. SC-SPM-0060-1: 100 chairs with 95 received; the Buyer records
        'short' for 5, follow-up 'Ask credit note' for Ann by 2026-12-20."""
        order = self._dorder(qty=100.0)
        commitment = self._commitment(order)
        self._receive(order, 95.0, '2026-12-11 10:00:00')
        ann = self.manager
        reason = self.env['procurement.discrepancy.reason'].search([('kind', '=', 'short')], limit=1)
        self.assertTrue(reason, "a reason of kind short is shipped")
        discrepancy = self.env['procurement.delivery.discrepancy'].with_user(self.buyer).create({
            'commitment_id': commitment.id, 'reason_id': reason.id, 'quantity': 5.0,
            'action': 'Ask credit note', 'responsible_id': ann.id, 'deadline': '2026-12-20',
        })
        self.env.flush_all()
        self.assertEqual(discrepancy.state, 'open')
        self.assertEqual(discrepancy.quantity, 5.0)
        self.assertEqual(discrepancy.partner_id, self.acme)
        self.assertIn(discrepancy, commitment.discrepancy_ids)
        activity = self.env['mail.activity'].search([
            ('user_id', '=', ann.id), ('date_deadline', '=', '2026-12-20'),
            ('res_model', 'in', ('procurement.delivery.discrepancy', 'procurement.delivery.commitment',
                                 'purchase.order'))])
        self.assertTrue(activity, "the responsible person has an activity due 2026-12-20")

    # FR-SPM-0061 -----------------------------------------------------------
    def test_sc_0061_1_proposals_and_discrepancies_add_no_stock_move(self):
        """Requirements: FR-SPM-0061. SC-SPM-0061-1 (second part): accepting a proposal and
        recording a discrepancy leave the order's stock moves and receipts as Odoo made them."""
        order = self._confirmed_order()
        moves = self._move_count(order)
        pickings = len(order.picking_ids)
        commitment, proposal = self._pending_proposal(order)
        proposal.with_user(self.buyer).action_spm_accept()
        reason = self.env['procurement.discrepancy.reason'].search([('kind', '=', 'late')], limit=1)
        self.env['procurement.delivery.discrepancy'].with_user(self.buyer).create({
            'commitment_id': commitment.id, 'reason_id': reason.id, 'quantity': 0.0,
            'action': 'Chase', 'responsible_id': self.manager.id, 'deadline': '2026-12-20',
        })
        self.env.flush_all()
        self.assertEqual(self._move_count(order), moves)
        self.assertEqual(len(order.picking_ids), pickings)
