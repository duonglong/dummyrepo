"""Task tests of T-022: vendor price update targets, contract totals, grouping, late order lines.

Requirements: FR-SPM-0067, FR-SPM-0068, FR-SPM-0070
"""
from odoo.tests import Form

from .contract_common import SpmContractCase


class TestContractUse(SpmContractCase):

    def _supplierinfo(self, partner, company_id, price=12.0):
        return self.env['product.supplierinfo'].create({
            'partner_id': partner.id, 'product_tmpl_id': self.chair.product_tmpl_id.id,
            'product_id': self.chair.id, 'price': price, 'min_qty': 20.0, 'company_id': company_id,
        })

    def _dialog(self, contract):
        """Open the dialog with Form (the Confirm button itself is left to the screen review)."""
        action = contract.with_user(self.bob).action_spm_update_vendor_prices()
        ctx = dict(action.get('context') or {})
        return Form(self.env[action['res_model']].with_user(self.bob).with_context(**ctx)).save()

    def _confirmed_order(self, qty=30.0):
        order = self.env['purchase.order'].with_user(self.ann).create({
            'partner_id': self.acme.id,
            'order_line': [(0, 0, {'product_id': self.chair.id, 'name': 'Chair', 'product_qty': qty,
                                   'price_unit': 10.0, 'tax_ids': [(6, 0, [])]})]})
        order.button_confirm()
        return order

    def test_confirm_rebuilds_targets_from_the_contract(self):
        """Requirements: FR-SPM-0067: a forged row (another vendor's price) is never written."""
        other = self._supplierinfo(self.beta, self.company.id, price=30.0)
        contract = self._active_contract(lines=[self._line_vals(price=11.5)])
        dialog = self._dialog(contract)
        dialog.line_ids.sudo().write({'supplierinfo_id': other.id, 'new_price': 1.0})
        dialog.with_user(self.bob).action_confirm()
        self.assertEqual(other.price, 30.0)
        mine = self.env['product.supplierinfo'].search([('spm_contract_id', '=', contract.id)])
        self.assertEqual(mine.partner_id, self.acme)
        self.assertEqual(mine.price, 11.5)

    def test_shared_vendor_price_is_not_taken_over(self):
        """Requirements: FR-SPM-0067: a price shared by all companies is left alone; a company one is created."""
        shared = self._supplierinfo(self.acme, False, price=12.0)
        contract = self._active_contract(lines=[self._line_vals(price=11.5)])
        dialog = self._dialog(contract)
        self.assertEqual(dialog.line_ids.operation, 'create')
        dialog.with_user(self.bob).action_confirm()
        self.assertEqual(shared.price, 12.0)
        self.assertFalse(shared.spm_contract_id)
        created = self.env['product.supplierinfo'].search([('spm_contract_id', '=', contract.id)])
        self.assertEqual(created.company_id, self.company)

    def test_contract_totals_and_grouping(self):
        """Requirements: FR-SPM-0068, FR-SPM-0070: remaining value and quantity, products and categories."""
        contract = self._active_contract(
            lines=[self._line_vals(price=10.0, ceiling=100.0)], ceiling_value=2000.0, ceiling_qty=500.0)
        self._confirmed_order()
        self.env.flush_all()
        contract.invalidate_recordset()
        self.assertEqual(contract.consumed_qty, 30.0)
        self.assertEqual(contract.remaining_qty, 470.0)
        self.assertEqual(contract.consumed_value, 300.0)
        self.assertEqual(contract.remaining_value, 1700.0)
        self.assertEqual(contract.spm_product_ids, self.chair)
        self.assertEqual(contract.spm_category_ids, self.chair.categ_id)

    def test_line_added_to_a_confirmed_order_is_linked(self):
        """Requirements: FR-SPM-0068: a line added after confirmation consumes the contract."""
        contract = self._active_contract(lines=[self._line_vals(price=10.0)])
        order = self._confirmed_order()
        late = self.env['purchase.order.line'].with_user(self.ann).create({
            'order_id': order.id, 'product_id': self.chair.id, 'name': 'Chair', 'product_qty': 5.0,
            'price_unit': 10.0, 'tax_ids': [(6, 0, [])]})
        self.assertEqual(late.spm_contract_line_id, contract.line_ids)
        contract.line_ids.invalidate_recordset()
        self.assertEqual(contract.line_ids.consumed_qty, 35.0)

    def test_variantless_price_of_a_multi_variant_template_is_not_taken_over(self):
        """Requirements: FR-SPM-0067: a contract price for one variant never overwrites the price of all variants."""
        attribute = self.env['product.attribute'].create({
            'name': 'Colour M4', 'value_ids': [(0, 0, {'name': 'Red'}), (0, 0, {'name': 'Blue'})]})
        template = self.env['product.template'].create({
            'name': 'Stool M4', 'type': 'consu',
            'attribute_line_ids': [(0, 0, {
                'attribute_id': attribute.id, 'value_ids': [(6, 0, attribute.value_ids.ids)]})]})
        red = template.product_variant_ids[0]
        shared = self.env['product.supplierinfo'].create({
            'partner_id': self.acme.id, 'product_tmpl_id': template.id, 'price': 12.0,
            'min_qty': 20.0, 'company_id': self.company.id})
        contract = self._active_contract(lines=[self._line_vals(price=11.5, product=red)])
        dialog = self._dialog(contract)
        self.assertEqual(dialog.line_ids.operation, 'create')
        dialog.with_user(self.bob).action_confirm()
        self.assertEqual(shared.price, 12.0)
        created = self.env['product.supplierinfo'].search([('spm_contract_id', '=', contract.id)])
        self.assertEqual(created.product_id, red)

    def test_out_of_scope_linked_price_loses_its_link_but_keeps_its_price(self):
        """Requirements: FR-SPM-0067: a price linked to the line for another vendor or product is never overwritten."""
        contract = self._active_contract(lines=[self._line_vals(price=11.5)])
        other_product = self.env['product.product'].create({'name': 'Desk M4', 'type': 'consu'})
        old = self.env['product.supplierinfo'].sudo().create({
            'partner_id': self.beta.id, 'product_tmpl_id': other_product.product_tmpl_id.id,
            'product_id': other_product.id, 'price': 99.0, 'min_qty': 20.0, 'company_id': self.company.id,
            'spm_contract_id': contract.id, 'spm_contract_line_id': contract.line_ids.id})
        dialog = self._dialog(contract)
        self.assertEqual(dialog.line_ids.operation, 'create')
        dialog.with_user(self.bob).action_confirm()
        self.assertEqual(old.price, 99.0)
        self.assertFalse(old.spm_contract_line_id)
        mine = self.env['product.supplierinfo'].search([('spm_contract_line_id', '=', contract.line_ids.id)])
        self.assertEqual(mine.product_id, self.chair)
        self.assertEqual(mine.price, 11.5)
