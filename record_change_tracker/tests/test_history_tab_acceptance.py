"""Acceptance tests of T-003: the change history tab (ordering, record access, sensitive hiding).

Written from the spec only (FR-RCT-002, FR-RCT-013, NFR-PERF-001, NFR-SEC-001).
"""
import time

from odoo.tests.common import TransactionCase, new_test_user


class TestHistoryTabAcceptance(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        groups = ('base.group_user,base.group_system,base.group_partner_manager,'
                  'sales_team.group_sale_salesman_all_leads,purchase.group_purchase_user')
        cls.anna = new_test_user(cls.env, login='rct_h_anna', name='Anna H', groups=groups)
        # internal user with no sales / purchase rights: cannot read orders
        cls.plain = new_test_user(cls.env, login='rct_h_plain', name='Plain H', groups='base.group_user')

    def _type(self, model):
        return self.env['record.change.model'].sudo().search([('model_id.model', '=', model)])

    def _entry(self, record, fname='phone', date='2026-03-01 10:00:00', old='a', new='b', author=None):
        return self.env['record.change.entry'].sudo().create({
            'res_model': record._name, 'res_id': record.id,
            'field_name': fname, 'field_label': fname.title(),
            'old_value': old, 'new_value': new,
            'author_id': (author or self.anna).id, 'change_date': date,
        })

    def _tab_ids(self, record, user):
        rec = record.with_user(user)
        return rec.change_entry_ids.ids

    # ---- FR-RCT-002
    def test_002_1_tab_in_form_and_newest_first(self):
        """Requirements: FR-RCT-002. Scenario SC-RCT-002-1."""
        self._type('sale.order').write({'is_followed': True})
        order = self.env['sale.order'].create({'partner_id': self.env['res.partner'].create({'name': 'C'}).id})
        old = self._entry(order, date='2026-03-01 10:00:00')
        new = self._entry(order, date='2026-03-03 10:00:00')
        self.assertTrue(order.with_user(self.anna).change_tracking_followed)
        self.assertEqual(self._tab_ids(order, self.anna), [new.id, old.id])
        arch = self.env['sale.order'].with_user(self.anna).get_view(view_type='form')['arch']
        self.assertIn('change_entry_ids', arch)

    def test_002_tab_in_the_four_forms(self):
        """Requirements: FR-RCT-002. Each of the four covered forms carries the history tab."""
        for model in ('res.partner', 'sale.order', 'purchase.order', 'product.template'):
            arch = self.env[model].with_user(self.anna).get_view(view_type='form')['arch']
            self.assertIn('change_entry_ids', arch, model)
            self.assertIn('change_tracking_followed', arch, model)

    def test_002_2_not_followed_no_tab(self):
        """Requirements: FR-RCT-002. Scenario SC-RCT-002-2."""
        self._type('purchase.order').write({'is_followed': False})
        order = self.env['purchase.order'].create({'partner_id': self.env['res.partner'].create({'name': 'S'}).id})
        self.assertFalse(order.with_user(self.anna).change_tracking_followed)

    def test_002_followed_flag_true_when_followed(self):
        """Requirements: FR-RCT-002. The flag that shows the tab is True for a followed type."""
        self._type('product.template').write({'is_followed': True})
        product = self.env['product.template'].create({'name': 'Desk'})
        self.assertTrue(product.with_user(self.anna).change_tracking_followed)

    def test_002_3_empty_message(self):
        """Requirements: FR-RCT-002. Scenario SC-RCT-002-3."""
        self._type('product.template').write({'is_followed': True})
        product = self.env['product.template'].create({'name': 'Desk'})
        self.assertEqual(self._tab_ids(product, self.anna), [])
        arch = self.env['product.template'].with_user(self.anna).get_view(view_type='form')['arch']
        self.assertIn('No changes recorded yet', arch)

    # ---- FR-RCT-013
    def test_013_1_sensitive_hides_earlier_entries(self):
        """Requirements: FR-RCT-013. Scenario SC-RCT-013-1."""
        rtype = self._type('res.partner')
        rtype.write({'is_followed': True})
        partner = self.env['res.partner'].create({'name': 'Azure Interior'})
        vat_entry = self._entry(partner, fname='vat')
        phone_entry = self._entry(partner, fname='phone')
        self.assertEqual(set(self._tab_ids(partner, self.anna)), {vat_entry.id, phone_entry.id})
        ir_field = self.env['ir.model.fields']._get('res.partner', 'vat')
        line = rtype.field_ids.filtered(lambda l: l.field_id == ir_field)
        if line:
            line.write({'is_sensitive': True})
        else:
            self.env['record.change.field'].create({
                'tracked_model_id': rtype.id, 'field_id': ir_field.id, 'is_sensitive': True})
        self.env.flush_all()
        partner.invalidate_recordset()
        self.assertEqual(self._tab_ids(partner, self.anna), [phone_entry.id])

    def test_013_unmarking_shows_entries_again(self):
        """Requirements: FR-RCT-013. WHILE a field is sensitive: when it stops being so, entries show again."""
        rtype = self._type('res.partner')
        rtype.write({'is_followed': True})
        partner = self.env['res.partner'].create({'name': 'Azure Interior'})
        vat_entry = self._entry(partner, fname='vat')
        ir_field = self.env['ir.model.fields']._get('res.partner', 'vat')
        line = rtype.field_ids.filtered(lambda l: l.field_id == ir_field) or \
            self.env['record.change.field'].create({
                'tracked_model_id': rtype.id, 'field_id': ir_field.id, 'is_sensitive': True})
        line.write({'is_sensitive': True})
        self.env.flush_all()
        partner.invalidate_recordset()
        self.assertEqual(self._tab_ids(partner, self.anna), [])
        line.write({'is_sensitive': False})
        self.env.flush_all()
        partner.invalidate_recordset()
        self.assertEqual(self._tab_ids(partner, self.anna), [vat_entry.id])

    # ---- NFR-SEC-001
    def test_sec_no_read_no_entries(self):
        """Requirements: NFR-SEC-001. A user who cannot read the record sees none of its entries."""
        self._type('sale.order').write({'is_followed': True})
        order = self.env['sale.order'].create({'partner_id': self.env['res.partner'].create({'name': 'C'}).id})
        entry = self._entry(order, fname='client_order_ref')
        Entry = self.env['record.change.entry']
        self.assertEqual(Entry.with_user(self.plain).search([('id', '=', entry.id)]).ids, [])
        self.assertEqual(Entry.with_user(self.anna).search([('id', '=', entry.id)]).ids, [entry.id])

    # ---- NFR-PERF-001
    def test_perf_first_page_of_1000_entries(self):
        """Requirements: NFR-PERF-001. First page of a 1,000-entry history within 2 seconds."""
        self._type('res.partner').write({'is_followed': True})
        partner = self.env['res.partner'].create({'name': 'Busy'})
        self.env['record.change.entry'].sudo().create([{
            'res_model': 'res.partner', 'res_id': partner.id,
            'field_name': 'phone', 'field_label': 'Phone', 'old_value': str(i),
            'new_value': str(i + 1), 'author_id': self.anna.id,
            'change_date': '2026-01-01 00:00:00',
        } for i in range(1000)])
        self.env.flush_all()
        self.env.invalidate_all()
        start = time.monotonic()
        res = partner.with_user(self.anna).web_read({
            'change_entry_ids': {
                'fields': {'field_label': {}, 'old_value': {}, 'new_value': {}, 'change_date': {}},
                'limit': 80,
            },
        })
        self.assertEqual(len(res[0]['change_entry_ids']), 80)
        self.assertLess(time.monotonic() - start, 2.0)
