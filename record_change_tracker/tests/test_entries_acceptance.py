"""Acceptance tests of T-002: change entries recorded on save.

Written from the spec only (FR-RCT-001, FR-RCT-003, FR-RCT-012, FR-RCT-015).
"""
from odoo.tests.common import TransactionCase, new_test_user


class TestEntriesAcceptance(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = ('base.group_user,base.group_system,base.group_partner_manager,'
                  'sales_team.group_sale_salesman_all_leads,purchase.group_purchase_user')
        cls.anna = new_test_user(cls.env, login='rct_anna', name='Anna', groups=groups)
        cls.marc = new_test_user(cls.env, login='rct_marc2', name='Marc Demo', groups=groups)

    def _type(self, model):
        return self.env['record.change.model'].sudo().search([('model_id.model', '=', model)])

    def _set_line(self, model, fname, followed=True, sensitive=False):
        rtype = self._type(model)
        ir_field = self.env['ir.model.fields']._get(model, fname)
        line = rtype.field_ids.filtered(lambda l: l.field_id == ir_field)
        vals = {'is_followed': followed, 'is_sensitive': sensitive}
        if line:
            line.sudo().write(vals)
        else:
            self.env['record.change.field'].sudo().create(
                dict(vals, tracked_model_id=rtype.id, field_id=ir_field.id))
        self.env.flush_all()

    def _unfollow(self, model, fname):
        line = self._type(model).field_ids.filtered(lambda l: l.field_id.name == fname)
        if line:
            line.sudo().write({'is_followed': False})
        self.env.flush_all()

    def _entries(self, record, fname=None):
        self.env.flush_all()
        domain = [('res_model', '=', record._name), ('res_id', '=', record.id)]
        if fname:
            domain.append(('field_name', '=', fname))
        return self.env['record.change.entry'].sudo().search(domain)

    def _partner(self, **vals):
        return self.env['res.partner'].create(dict({'name': 'Azure Interior'}, **vals))

    # ---- FR-RCT-001
    def test_001_1_entry_holds_old_new_author_date(self):
        """Requirements: FR-RCT-001. Scenario SC-RCT-001-1."""
        self._type('res.partner').sudo().write({'is_followed': True})
        self._set_line('res.partner', 'phone')
        partner = self._partner(phone='555-0100')
        before = self._entries(partner, 'phone')
        partner.with_user(self.anna).write({'phone': '555-0199'})
        entries = self._entries(partner, 'phone') - before
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries.old_value, '555-0100')
        self.assertEqual(entries.new_value, '555-0199')
        self.assertEqual(entries.author_id, self.anna)
        self.assertTrue(entries.change_date)

    def test_001_2_unfollowed_field_leaves_no_entry(self):
        """Requirements: FR-RCT-001. Scenario SC-RCT-001-2."""
        self._set_line('res.partner', 'phone')
        self._unfollow('res.partner', 'website')
        partner = self._partner(phone='555-0100')
        before = self._entries(partner)
        partner.with_user(self.anna).write({'website': 'https://example.com'})
        self.assertEqual(self._entries(partner), before)
        self.assertFalse(self._entries(partner, 'website'))

    def test_001_3_same_value_leaves_no_entry(self):
        """Requirements: FR-RCT-001. Scenario SC-RCT-001-3."""
        self._set_line('res.partner', 'phone')
        partner = self._partner(phone='555-0100')
        before = self._entries(partner)
        partner.with_user(self.anna).write({'phone': '555-0100'})
        self.assertEqual(self._entries(partner), before)

    def test_001_one_entry_per_changed_field(self):
        """Requirements: FR-RCT-001. One save of two followed fields leaves one entry for each."""
        self._set_line('res.partner', 'phone')
        self._set_line('res.partner', 'city')
        partner = self._partner(phone='555-0100', city='Paris')
        before = self._entries(partner)
        partner.with_user(self.anna).write({'phone': '555-0199', 'city': 'Lyon'})
        new = self._entries(partner) - before
        self.assertEqual(sorted(new.mapped('field_name')), ['city', 'phone'])
        self.assertEqual(len(new), 2)

    def test_001_each_save_leaves_its_own_entry(self):
        """Requirements: FR-RCT-001. Two successive changes leave two entries."""
        self._set_line('res.partner', 'phone')
        partner = self._partner(phone='1')
        before = self._entries(partner, 'phone')
        partner.with_user(self.anna).write({'phone': '2'})
        partner.with_user(self.anna).write({'phone': '3'})
        new = self._entries(partner, 'phone') - before
        self.assertEqual(len(new), 2)
        self.assertEqual(set(new.mapped('new_value')), {'2', '3'})

    def test_001_other_covered_models_record(self):
        """Requirements: FR-RCT-001. Products and purchase orders record changes too."""
        self._type('product.template').sudo().write({'is_followed': True})
        self._set_line('product.template', 'name')
        product = self.env['product.template'].create({'name': 'Desk'})
        before = self._entries(product, 'name')
        product.with_user(self.anna).write({'name': 'Big desk'})
        new = self._entries(product, 'name') - before
        self.assertEqual(len(new), 1)
        self.assertEqual((new.old_value, new.new_value), ('Desk', 'Big desk'))

        self._type('purchase.order').sudo().write({'is_followed': True})
        self._set_line('purchase.order', 'partner_ref')
        order = self.env['purchase.order'].create({
            'partner_id': self._partner().id, 'partner_ref': 'A'})
        before = self._entries(order, 'partner_ref')
        order.with_user(self.anna).write({'partner_ref': 'B'})
        new = self._entries(order, 'partner_ref') - before
        self.assertEqual(len(new), 1)
        self.assertEqual((new.old_value, new.new_value), ('A', 'B'))

    # ---- FR-RCT-003
    def _order(self, **vals):
        self._type('sale.order').sudo().write({'is_followed': True})
        return self.env['sale.order'].create(dict({'partner_id': self._partner().id}, **vals))

    def test_003_1_many2one_shown_by_name(self):
        """Requirements: FR-RCT-003. Scenario SC-RCT-003-1."""
        self._set_line('sale.order', 'user_id')
        order = self._order(user_id=self.marc.id)
        before = self._entries(order, 'user_id')
        order.with_user(self.anna).write({'user_id': self.anna.id})
        new = self._entries(order, 'user_id') - before
        self.assertEqual(len(new), 1)
        self.assertEqual(new.field_label, 'Salesperson')
        self.assertEqual(new.old_value, 'Marc Demo')
        self.assertEqual(new.new_value, 'Anna')
        self.assertEqual(new.author_id, self.anna)
        self.assertTrue(new.change_date)

    def test_003_2_selection_shown_by_label(self):
        """Requirements: FR-RCT-003. Scenario SC-RCT-003-2."""
        self._set_line('sale.order', 'state')
        order = self._order()
        before = self._entries(order, 'state')
        order.with_user(self.anna).write({'state': 'sale'})
        new = self._entries(order, 'state') - before
        self.assertEqual(len(new), 1)
        self.assertEqual(new.old_value, 'Quotation')
        self.assertEqual(new.new_value, 'Sales Order')

    def test_003_3_first_value_has_blank_old_value(self):
        """Requirements: FR-RCT-003. Scenario SC-RCT-003-3."""
        self._set_line('res.partner', 'email')
        partner = self._partner()
        before = self._entries(partner, 'email')
        partner.with_user(self.anna).write({'email': 'a@example.com'})
        new = self._entries(partner, 'email') - before
        self.assertEqual(len(new), 1)
        self.assertFalse(new.old_value)
        self.assertNotEqual(new.old_value, 'None')
        self.assertEqual(new.new_value, 'a@example.com')

    def test_003_cleared_value_has_blank_new_value(self):
        """Requirements: FR-RCT-003. An emptied field shows a blank new value."""
        self._set_line('res.partner', 'email')
        partner = self._partner(email='a@example.com')
        before = self._entries(partner, 'email')
        partner.with_user(self.anna).write({'email': False})
        new = self._entries(partner, 'email') - before
        self.assertEqual(len(new), 1)
        self.assertEqual(new.old_value, 'a@example.com')
        self.assertFalse(new.new_value)

    # ---- FR-RCT-012
    def test_012_1_sensitive_followed_field_not_recorded(self):
        """Requirements: FR-RCT-012. Scenario SC-RCT-012-1."""
        self._set_line('res.partner', 'vat', followed=True, sensitive=True)
        partner = self._partner()
        partner.with_user(self.anna).write({'vat': 'BE0123456749'})
        self.assertFalse(self._entries(partner, 'vat'))

    def test_012_2_unmarked_field_recorded_again(self):
        """Requirements: FR-RCT-012. Scenario SC-RCT-012-2."""
        self._set_line('res.partner', 'vat', followed=True, sensitive=True)
        self._set_line('res.partner', 'vat', followed=True, sensitive=False)
        partner = self._partner()
        partner.with_user(self.anna).write({'vat': 'BE0123456749'})
        new = self._entries(partner, 'vat')
        self.assertEqual(len(new), 1)
        self.assertEqual(new.new_value, 'BE0123456749')

    def test_012_sensitive_does_not_block_other_fields(self):
        """Requirements: FR-RCT-012. Other followed fields of the same save are still recorded."""
        self._set_line('res.partner', 'vat', followed=True, sensitive=True)
        self._set_line('res.partner', 'phone')
        partner = self._partner(phone='1')
        partner.with_user(self.anna).write({'vat': 'BE0123456749', 'phone': '2'})
        self.assertFalse(self._entries(partner, 'vat'))
        self.assertEqual(len(self._entries(partner, 'phone')), 1)

    # ---- FR-RCT-015
    def test_015_1_type_not_followed_records_nothing(self):
        """Requirements: FR-RCT-015. Scenario SC-RCT-015-1."""
        self._set_line('product.template', 'list_price')
        self._type('product.template').sudo().write({'is_followed': False})
        self.env.flush_all()
        product = self.env['product.template'].create({'name': 'Desk', 'list_price': 10.0})
        product.with_user(self.anna).write({'list_price': 20.0})
        self.assertFalse(self._entries(product))

    def test_015_2_entries_kept_when_type_turned_off_and_on(self):
        """Requirements: FR-RCT-015. Scenario SC-RCT-015-2."""
        self._set_line('product.template', 'list_price')
        self._type('product.template').sudo().write({'is_followed': True})
        product = self.env['product.template'].create({'name': 'Desk', 'list_price': 1.0})
        for price in (2.0, 3.0, 4.0):
            product.with_user(self.anna).write({'list_price': price})
        self.assertEqual(len(self._entries(product, 'list_price')), 3)
        ptype = self._type('product.template')
        ptype.sudo().write({'is_followed': False})
        self.env.flush_all()
        product.with_user(self.anna).write({'list_price': 5.0})
        self.assertEqual(len(self._entries(product, 'list_price')), 3)
        ptype.sudo().write({'is_followed': True})
        self.env.flush_all()
        self.assertEqual(len(self._entries(product, 'list_price')), 3)
        product.with_user(self.anna).write({'list_price': 6.0})
        self.assertEqual(len(self._entries(product, 'list_price')), 4)
