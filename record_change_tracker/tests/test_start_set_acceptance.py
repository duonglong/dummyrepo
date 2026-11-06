"""Acceptance tests of T-005 (rework of T-001): the starting set of followed fields.

Written from the spec only. Requirements: FR-RCT-009.
"""
from odoo.tests.common import TransactionCase, new_test_user

START_SETS = {
    'res.partner': {'name', 'email', 'phone', 'street', 'city', 'country_id', 'vat', 'user_id'},
    'sale.order': {'partner_id', 'user_id', 'payment_term_id', 'validity_date', 'state', 'amount_total'},
    'purchase.order': {'partner_id', 'user_id', 'date_order', 'date_planned', 'payment_term_id',
                       'state', 'amount_total'},
    'product.template': {'name', 'default_code', 'list_price', 'categ_id', 'barcode'},
}


class TestStartSetAcceptance(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = new_test_user(cls.env, login='rct_start_admin', groups='base.group_user,base.group_system')
        cls.seller = new_test_user(
            cls.env, login='rct_start_seller',
            groups='base.group_user,sales_team.group_sale_manager,product.group_product_manager,purchase.group_purchase_manager')

    def _followed(self, model):
        lines = self.env['record.change.model'].with_user(self.admin).search([('model_id.model', '=', model)])
        self.assertEqual(len(lines), 1, "one setting line for %s" % model)
        self.assertTrue(lines.is_followed, "%s is followed after install" % model)
        return set(lines.field_ids.filtered('is_followed').mapped('field_id.name'))

    def test_009_1_after_install_four_types_followed_nothing_sensitive(self):
        """Requirements: FR-RCT-009. Scenario SC-RCT-009-1."""
        lines = self.env['record.change.model'].with_user(self.admin).search([])
        self.assertEqual(set(lines.mapped('model_id.model')), set(START_SETS))
        self.assertTrue(all(lines.mapped('is_followed')))
        self.assertEqual(self._followed('sale.order'), START_SETS['sale.order'])
        self.assertFalse(self.env['record.change.field'].with_user(self.admin).search(
            [('is_sensitive', '=', True)]))

    def test_009_each_type_has_exactly_its_own_starting_set(self):
        """Requirements: FR-RCT-009. Each record type starts with its own set, and product cost is left out."""
        for model, expected in START_SETS.items():
            with self.subTest(model=model):
                self.assertEqual(self._followed(model), expected)
        self.assertNotIn('standard_price', self._followed('product.template'))

    def test_009_2_product_price_change_recorded_from_install(self):
        """Requirements: FR-RCT-009. Scenario SC-RCT-009-2, with no settings touched."""
        product = self.env['product.template'].create({'name': 'Desk', 'list_price': 100.0})
        product.with_user(self.seller).write({'list_price': 120.0})
        self.env.flush_all()
        entries = self.env['record.change.entry'].sudo().search([
            ('res_model', '=', 'product.template'), ('res_id', '=', product.id),
            ('field_name', '=', 'list_price')])
        self.assertEqual(len(entries), 1)
        self.assertEqual(float(entries.old_value), 100.0)
        self.assertEqual(float(entries.new_value), 120.0)
