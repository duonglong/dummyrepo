from freezegun import freeze_time

from odoo import Command
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user

# 2026-10-21 is a Wednesday, 2026-10-24 a Saturday, 2026-10-25 a Sunday.


@tagged('post_install', '-at_install')
class TestSaleBridgeInstallAcceptance(TransactionCase):
    """Acceptance tests of T-011: the Sales bridge module.

    Requirements: FR-LAP-007, FR-LAP-009, FR-LAP-012, FR-LAP-014, FR-LAP-016
    """

    def _module(self, name):
        return self.env['ir.module.module'].sudo().search([('name', '=', name)])

    def test_manifest_depends_and_auto_install(self):
        """Requirements: FR-LAP-016 - SC-LAP-016-1: the bridge depends on core and sale_loyalty, auto-installs."""
        module = self._module('loyalty_weekend_multiplier_sale')
        self.assertTrue(module, 'bridge module is missing')
        depends = set(module.dependencies_id.mapped('name'))
        self.assertEqual(depends, {'loyalty_weekend_multiplier', 'sale_loyalty'})
        self.assertTrue(module.auto_install)

    def test_manifest_metadata(self):
        """Requirements: FR-LAP-016 - the bridge is LGPL-3, version 20.0.2.0.0, by Empty Sandbox."""
        module = self._module('loyalty_weekend_multiplier_sale')
        self.assertTrue(module, 'bridge module is missing')
        self.assertEqual(module.license, 'LGPL-3')
        self.assertEqual(module.author, 'Empty Sandbox')
        self.assertTrue(module.latest_version.startswith('20.0.2.0.0')
                        or module.installed_version == '20.0.2.0.0')

    def test_bridge_installed_with_core_and_sale_loyalty(self):
        """Requirements: FR-LAP-016 - SC-LAP-016-2: the bridge is installed when core and sale_loyalty are."""
        self.assertEqual(self._module('loyalty_weekend_multiplier').state, 'installed')
        self.assertEqual(self._module('sale_loyalty').state, 'installed')
        self.assertEqual(self._module('loyalty_weekend_multiplier_sale').state, 'installed')

    def test_point_of_sale_not_installed(self):
        """Requirements: FR-LAP-014 - SC-LAP-014-1: Sales works without Point of Sale; the bridge does not pull it."""
        module = self._module('loyalty_weekend_multiplier_sale')
        self.assertTrue(module, 'bridge module is missing')
        self.assertNotIn('point_of_sale', module.dependencies_id.mapped('name'))
        self.assertNotIn('pos_loyalty', module.dependencies_id.mapped('name'))
        self.assertNotEqual(self._module('point_of_sale').state, 'installed')

    def _make(self, program_mult=2.0):
        program = self.env['loyalty.program'].create({
            'name': 'Bridge Weekend',
            'program_type': 'loyalty',
            'trigger': 'auto',
            'applies_on': 'both',
            'weekend_multiplier': program_mult,
            'rule_ids': [Command.clear(), Command.create({
                'reward_point_mode': 'money',
                'reward_point_amount': 1,
            })],
        })
        partner = self.env['res.partner'].create({'name': 'Bridge Customer'})
        product = self.env['product.product'].create({'name': 'Bridge Product', 'list_price': 100.0})
        user = new_test_user(self.env, login='bridge_sale_user',
                             groups='base.group_user,sales_team.group_sale_salesman', tz='UTC')
        return program, partner, product, user

    def _order(self, partner, product, user, date_order):
        return self.env['sale.order'].with_user(user).create({
            'partner_id': partner.id,
            'date_order': date_order,
            'order_line': [Command.create({
                'product_id': product.id,
                'product_uom_qty': 1,
                'price_unit': 100.0,
                'tax_ids': [Command.clear()],
            })],
        })

    def _points(self, order, program):
        order._update_programs_and_rewards()
        self.env.flush_all()
        return sum(order.coupon_point_ids.filtered(
            lambda p: p.coupon_id.program_id == program).mapped('points'))

    def test_sales_only_saturday_doubles(self):
        """Requirements: FR-LAP-014 - SC-LAP-014-1: Sales without POS, Saturday 100.00 gives 200.00."""
        program, partner, product, user = self._make()
        order = self._order(partner, product, user, '2026-10-24 12:00:00')
        self.assertAlmostEqual(self._points(order, program), 200.0, places=2)

    def test_sales_only_wednesday_native(self):
        """Requirements: FR-LAP-014 - SC-LAP-014-2: Sales without POS, Wednesday 100.00 gives 100.00."""
        program, partner, product, user = self._make()
        order = self._order(partner, product, user, '2026-10-21 12:00:00')
        self.assertAlmostEqual(self._points(order, program), 100.0, places=2)

    def test_draft_sunday_quotation_shows_double(self):
        """Requirements: FR-LAP-007 - SC-LAP-007-2: a draft Sunday quotation shows 200.00."""
        program, partner, product, user = self._make()
        order = self._order(partner, product, user, '2026-10-25 12:00:00')
        self.assertAlmostEqual(self._points(order, program), 200.0, places=2)

    def test_friday_quote_confirmed_saturday_doubles(self):
        """Requirements: FR-LAP-009 - SC-LAP-009-1: Friday quotation confirmed on Saturday earns 200.00."""
        program, partner, product, user = self._make()
        order = self._order(partner, product, user, '2026-10-23 12:00:00')
        with freeze_time('2026-10-24 12:00:00'):
            order.action_confirm()
        self.env.flush_all()
        card = self.env['loyalty.card'].search([
            ('program_id', '=', program.id), ('partner_id', '=', partner.id)])
        self.assertAlmostEqual(sum(card.mapped('points')), 200.0, places=2)

    def test_sunday_quote_confirmed_monday_native(self):
        """Requirements: FR-LAP-009 - SC-LAP-009-2: Sunday quotation confirmed on Monday earns 100.00."""
        program, partner, product, user = self._make()
        order = self._order(partner, product, user, '2026-10-25 12:00:00')
        with freeze_time('2026-10-26 12:00:00'):
            order.action_confirm()
        self.env.flush_all()
        card = self.env['loyalty.card'].search([
            ('program_id', '=', program.id), ('partner_id', '=', partner.id)])
        self.assertAlmostEqual(sum(card.mapped('points')), 100.0, places=2)

    def test_reward_cost_not_multiplied(self):
        """Requirements: FR-LAP-012 - SC-LAP-012-1: a 100-point reward claimed on Sunday deducts 100."""
        program, partner, product, user = self._make()
        reward = self.env['loyalty.reward'].create({
            'program_id': program.id,
            'reward_type': 'discount',
            'discount': 10,
            'discount_mode': 'percent',
            'discount_applicability': 'order',
            'required_points': 100,
        })
        card = self.env['loyalty.card'].create({
            'program_id': program.id, 'partner_id': partner.id})
        card._adjust_points(300, 'Initial balance')
        order = self._order(partner, product, user, '2026-10-25 12:00:00')
        with freeze_time('2026-10-25 12:00:00'):
            order._apply_program_reward(reward, card)
        self.env.flush_all()
        self.assertAlmostEqual(
            sum(order.order_line.filtered('reward_id').mapped('points_cost')), 100.0, places=2)
