"""Tests of the procurement dashboard tiles.

Requirements: FR-SPM-0098
"""
from datetime import timedelta

from freezegun import freeze_time

from odoo import fields

from .approval_common import SpmApprovalCase

MODULE = 'supplier_procurement_management_hub'


class TestDashboard(SpmApprovalCase):
    """Requirements: FR-SPM-0098"""

    def setUp(self):
        super().setUp()
        self.bob.write({'group_ids': [(4, self.env.ref(f'{MODULE}.group_spm_manager').id)]})

    def _tiles(self, user):
        return {tile['key']: tile for tile in self.env['procurement.dashboard'].with_user(user).get_tiles()}

    def _event(self, days):
        event = self.env['procurement.sourcing.event'].with_user(self.bob).create({
            'title': f'Chairs {days}', 'buyer_id': self.bob.id,
            'deadline': fields.Datetime.now() + timedelta(days=days),
            'invited_partner_ids': [(6, 0, self.acme.ids)],
            'line_ids': [(0, 0, {'name': 'Chair', 'product_id': self.chair.id, 'quantity': 5.0,
                                 'uom_id': self.chair.uom_id.id})],
        })
        event.action_spm_send()
        return event

    def test_fr_0098_closing_events_counts_only_those_within_the_closing_days(self):
        """FR-SPM-0098: an open event closing in 2 days is counted, one closing in 10 days is not."""
        with freeze_time('2026-12-15 09:00:00'):
            near = self._event(2)
            self._event(10)
            self.env.flush_all()
            tile = self._tiles(self.bob)['closing_events']
            self.assertEqual(tile['count'], 1)
            listed = self.env['procurement.sourcing.event'].with_user(self.bob).search(tile['action']['domain'])
            self.assertEqual(listed, near)

    def test_fr_0098_spend_tile_is_left_off_for_a_user_without_the_spend_report(self):
        """FR-SPM-0098: Bob (manager) sees the spend tile of the month; Eve (department manager) does not."""
        with freeze_time('2026-12-15 09:00:00'):
            self.assertIn('spend_month', self._tiles(self.bob))
            self.assertNotIn('spend_month', self._tiles(self.eve))

    def test_fr_0098_closing_events_boundary_at_the_closing_days(self):
        """FR-SPM-0098: an event closing exactly in the closing-soon days counts; a minute later does not."""
        days = self.env.company.spm_closing_soon_days
        with freeze_time('2026-12-15 09:00:00'):
            at_limit = self._event(days)
            beyond = self._event(days)
            beyond.deadline = fields.Datetime.now() + timedelta(days=days, minutes=1)
            self.env.flush_all()
            tile = self._tiles(self.bob)['closing_events']
            listed = self.env['procurement.sourcing.event'].with_user(self.bob).search(tile['action']['domain'])
            self.assertEqual(listed, at_limit)
            self.assertEqual(tile['count'], 1)

    def test_fr_0098_documents_tile_ignores_replaced_versions(self):
        """FR-SPM-0098: an expired document replaced by a newer one is not counted; the current one is."""
        doc_type = self.env['procurement.document.type'].create({'name': 'Certificate T33'})
        document = self.env['procurement.supplier.document']
        with freeze_time('2026-12-15 09:00:00'):
            old = document.create({'partner_id': self.acme.id, 'document_type_id': doc_type.id,
                                   'expiry_date': '2026-11-01'})
            self.env.flush_all()
            self.assertEqual(self._tiles(self.bob)['expiring_documents']['count'], 1)
            old.is_current = False
            self.env.flush_all()
            tile = self._tiles(self.bob)['expiring_documents']
            self.assertEqual(tile['count'], 0)
            self.assertFalse(document.with_user(self.bob).search(tile['action']['domain']))

    def test_fr_0098_spend_tile_carries_the_month_amount(self):
        """FR-SPM-0098: a confirmed order of this month adds its untaxed amount to the spend tile."""
        with freeze_time('2026-12-15 09:00:00'):
            before = self._tiles(self.bob)['spend_month']['amount']
            order = self.env['purchase.order'].create({
                'partner_id': self.acme.id, 'date_order': '2026-12-15 09:00:00',
                'order_line': [(0, 0, {'product_id': self.chair.id, 'name': 'Line', 'product_qty': 4.0,
                                       'price_unit': 25.0, 'tax_ids': [(6, 0, [])]})]})
            order.button_confirm()
            if order.state != 'purchase':
                order.button_approve(force=True)
            self.env.flush_all()
            tile = self._tiles(self.bob)['spend_month']
            self.assertAlmostEqual(tile['amount'] - before, 100.0, places=2)
            self.assertEqual(tile['currency_id'], self.env.company.currency_id.id)
