"""Acceptance tests of T-033 (procurement dashboard with work queues), written from the
specification.

Requirements: FR-SPM-0098, FR-SPM-0105

Names used (the design names ``procurement.dashboard.get_tiles()`` and says each tile opens a
filtered window action; the rest is fixed here and recorded in the module notes):
get_tiles() returns a list of dicts, one per tile the user may see, with ``key``, ``name``,
``count`` and ``action`` (the window action the tile opens, with its domain); the keys are
approvals, closing_events, expiring_documents, overdue_deliveries, invoice_exceptions,
open_requests and the spend tile ``spend_month``. The dashboard screen itself (an OWL client
action) is not driven: what it shows is what get_tiles() returns.
"""
from freezegun import freeze_time

from .approval_common import SpmApprovalCase

MODULE = 'supplier_procurement_management_hub'


class TestDashboardAcceptance(SpmApprovalCase):
    """Requirements: FR-SPM-0098"""

    def setUp(self):
        super().setUp()
        self.bob.write({'group_ids': [(4, self.env.ref(f'{MODULE}.group_spm_manager').id)]})

    def _tiles(self, user):
        tiles = self.env['procurement.dashboard'].with_user(user).get_tiles()
        return {tile['key']: tile for tile in tiles}

    def _overdue_order(self):
        order = self.env['purchase.order'].create({
            'partner_id': self.acme.id, 'user_id': self.buyer.id,
            'order_line': [(0, 0, {
                'product_id': self.chair.id, 'name': 'Chair', 'product_qty': 10.0,
                'price_unit': 11.5, 'tax_ids': [(6, 0, [])], 'date_planned': '2026-12-10 08:00:00'})],
        })
        order.button_confirm()
        if order.state != 'purchase':
            order.button_approve(force=True)
        return order

    def test_sc_0098_1_tiles_count_the_queues_and_open_their_lists(self):
        """SC-SPM-0098-1: Bob has 3 approvals waiting and 2 overdue deliveries exist in his
        company; on his dashboard the approvals tile shows 3 and the overdue deliveries tile shows 2;
        opening the approvals tile lists the 3 steps."""
        with freeze_time('2026-12-15 09:00:00'):
            self._overdue_order()
            self._overdue_order()
            self._policy('Bob approves', steps=[self._step('Bob', 1, users=self.bob)])
            orders = [self._order(price=600.0, qty=10.0) for _i in range(3)]
            for order in orders:
                self._submit(order)
            self.env.flush_all()
            tiles = self._tiles(self.bob)
            self.assertEqual(tiles['approvals']['count'], 3)
            self.assertEqual(tiles['overdue_deliveries']['count'], 2)
            action = tiles['approvals']['action']
            self.assertEqual(action['res_model'], 'procurement.approval.step')
            steps = self.env[action['res_model']].with_user(self.bob).search(action['domain'])
            self.assertEqual(len(steps), 3)
            self.assertEqual(steps.approver_ids, self.bob)
            late = tiles['overdue_deliveries']['action']
            listed = self.env[late['res_model']].with_user(self.bob).search(late['domain'])
            self.assertEqual(len(listed), 2)

    def test_fr_0098_another_user_does_not_see_bobs_approvals(self):
        """FR-SPM-0098: the approvals tile counts the steps waiting for the user: Eve, who is no
        approver of them, sees none."""
        with freeze_time('2026-12-15 09:00:00'):
            self._policy('Bob approves', steps=[self._step('Bob', 1, users=self.bob)])
            self._submit(self._order(price=600.0, qty=10.0))
            self.env.flush_all()
            self.assertEqual(self._tiles(self.bob)['approvals']['count'], 1)
            eve_tile = self._tiles(self.eve).get('approvals')
            self.assertEqual(eve_tile['count'] if eve_tile else 0, 0)

    def test_fr_0098_a_delivery_not_yet_overdue_is_not_counted(self):
        """FR-SPM-0098: a line expected on 2026-12-10 is not overdue on that day."""
        with freeze_time('2026-12-10 07:00:00'):
            self._overdue_order()
            self.env.flush_all()
            self.assertEqual(self._tiles(self.bob)['overdue_deliveries']['count'], 0)
