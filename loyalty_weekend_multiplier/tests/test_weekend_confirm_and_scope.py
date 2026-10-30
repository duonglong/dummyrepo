from freezegun import freeze_time

from odoo import Command

from .common import WeekendMultiplierCase


class TestWeekendConfirmAndScope(WeekendMultiplierCase):
    """Unit tests of the confirmation day recompute.

    Requirements: FR-LAP-009, FR-LAP-010, FR-LAP-012, BR-LAP-005
    """

    def test_confirmation_context_does_not_leak_to_draft_points(self):
        """Requirements: FR-LAP-009 - a draft computed on a weekday keeps the native points."""
        program = self.env['loyalty.program'].create({
            'name': 'Confirm day',
            'program_type': 'loyalty',
            'trigger': 'auto',
            'applies_on': 'both',
            'weekend_multiplier': 2.0,
            'rule_ids': [Command.clear(), Command.create({
                'reward_point_mode': 'money', 'reward_point_amount': 1})],
        })
        with freeze_time('2026-10-26 12:00:00'):
            order = self.env['sale.order'].with_user(self.user_no_tz).create({
                'partner_id': self.partner.id,
                'date_order': '2026-10-26 12:00:00',
                'order_line': [Command.create({
                    'product_id': self.product.id, 'product_uom_qty': 1,
                    'price_unit': 100.0, 'tax_ids': [Command.clear()]})],
            })
            order.action_confirm()
        self.env.flush_all()
        card = self.env['loyalty.card'].search([('program_id', '=', program.id)])
        self.assertAlmostEqual(card.points, 100.0, places=2)
