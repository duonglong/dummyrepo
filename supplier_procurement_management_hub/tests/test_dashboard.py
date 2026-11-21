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
