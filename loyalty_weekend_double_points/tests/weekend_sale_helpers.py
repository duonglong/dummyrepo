"""Fixtures shared by the sales-order acceptance tests (not a test case itself)."""
from freezegun import freeze_time

from odoo import Command


class WeekendSaleMixin:
    """Helpers on top of ``sale.tests.common.SaleCommon``."""

    TZ = 'Europe/Brussels'
    # Europe/Brussels is UTC+1 in November 2026; 2026-11-07 is a Saturday.
    SAT_10H_LOCAL = '2026-11-07 09:00:00'
    SUN_2359_LOCAL = '2026-11-08 22:59:00'
    SAT_0030_LOCAL = '2026-11-06 23:30:00'  # Friday 23:30 UTC
    WED_LOCAL = '2026-11-04 11:00:00'
    FRI_2330_LOCAL = '2026-11-06 22:30:00'
    MON_0000_LOCAL = '2026-11-08 23:00:00'  # Monday 00:00 local, Sunday 23:00 UTC
    MON_UTC_NOON = '2026-11-09 12:00:00'

    def _set_company_tz(self, tz=None):
        self.env.company.tz = tz or self.TZ

    def _make_program(self, weekend=True, program_type='loyalty', mode='money',
                      amount=1, product=None, with_reward=False):
        rule = {'reward_point_mode': mode, 'reward_point_amount': amount}
        if product:
            rule['product_ids'] = [Command.set(product.ids)]
        vals = {
            'name': 'Weekend test program',
            'program_type': program_type,
            'trigger': 'auto',
            'applies_on': 'both',
            'rule_ids': [Command.create(rule)],
            'reward_ids': [Command.create({
                'reward_type': 'discount',
                'discount_mode': 'per_order',
                'discount': 10,
                'discount_applicability': 'order',
                'required_points': 100 if with_reward else 1000000,
            })],
        }
        if weekend:
            vals['weekend_double_points'] = True
        return self.env['loyalty.program'].sudo().create(vals)

    def _confirm_order_at(self, utc_dt, price=100.0, qty=1.0, product=None,
                          program=None, claim=None):
        """Create and confirm a sales order whose date (and the clock) is ``utc_dt``."""
        product = product or self.product
        with freeze_time(utc_dt):
            order = self.env['sale.order'].create({
                'partner_id': self.partner.id,
                'date_order': utc_dt,
                'order_line': [Command.create({
                    'product_id': product.id,
                    'product_uom_qty': qty,
                    'price_unit': price,
                })],
            })
            if claim:
                reward, card = claim
                order._update_programs_and_rewards()
                order._apply_program_reward(reward, card)
            order.action_confirm()
            self.env.flush_all()
        return order

    def _card_points(self, program):
        card = self.env['loyalty.card'].sudo().search([
            ('program_id', '=', program.id),
            ('partner_id', '=', self.partner.id),
        ])
        self.assertEqual(len(card), 1, "exactly one card expected for the customer")
        return card.points
