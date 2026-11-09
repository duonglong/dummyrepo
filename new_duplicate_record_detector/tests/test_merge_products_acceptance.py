from odoo import Command
from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase


class TestMergeProductsAcceptance(TransactionCase):
    """Acceptance tests of T-006 (product template merge).

    Requirements: FR-DRD-0011, FR-DRD-0012, FR-DRD-0016.

    Pricelist items pointing to a variant stand for sales order lines (sale is not a
    dependency). The wizard is started with ``active_model='product.template'`` and
    ``active_ids`` in the context and confirmed with ``action_merge``.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        user_group = cls.env.ref('base.group_user')
        mgr_group = cls.env.ref('new_duplicate_record_detector.group_duplicate_manager')
        # a duplicate manager without any product right beyond reading
        cls.manager = cls.env['res.users'].create({
            'name': 'Dup Mgr T006', 'login': 'dup_mgr_t006',
            'group_ids': [Command.set((user_group | mgr_group).ids)],
        })
        cls.T = cls.env['product.template']
        cls.pricelist = cls.env['product.pricelist'].create({'name': 'T006 list'})
        cls.unit = cls.env.ref('uom.product_uom_unit')
        cls.litre = cls.env.ref('uom.product_uom_litre')

    # helpers
    def _product(self, name, **vals):
        vals.setdefault('uom_id', self.unit.id)
        return self.T.create(dict(vals, name=name))

    def _sell(self, variant):
        return self.env['product.pricelist.item'].create({
            'pricelist_id': self.pricelist.id, 'product_id': variant.id,
            'fixed_price': 5.0, 'compute_price': 'fixed'})

    def _start(self, ids, user=None):
        wizard = self.env['duplicate.merge.wizard'].with_user(user or self.manager)
        return wizard.with_context(active_model='product.template', active_ids=list(ids)).create({})

    def _merge(self, ids, user=None):
        wizard = self._start(ids, user=user)
        wizard.action_merge()
        return wizard

    def _set(self, templates):
        rule = self.env['duplicate.rule'].sudo().create({
            'name': 'T006 rule',
            'model_id': self.env['ir.model']._get('product.template').id,
            'condition_ids': [Command.create({
                'field_id': self.env['ir.model.fields']._get('product.template', 'barcode').id,
                'match_type': 'exact'})],
        })
        return self.env['duplicate.group'].sudo().create({
            'rule_id': rule.id, 'model_name': 'product.template',
            'line_ids': [Command.create({
                'res_model': 'product.template', 'res_id': t.id, 'name': t.display_name})
                for t in templates],
        })

    def _refresh(self):
        self.env.flush_all()
        self.env.invalidate_all()

    def _all(self, template):
        return template.with_context(active_test=False)

    # SC-DRD-0011-3
    def test_sc_0011_3_links_of_the_variant_move_to_the_kept_variant(self):
        """Requirements: FR-DRD-0011. Scenario SC-DRD-0011-3."""
        desk = self._product('Desk')
        desk2 = self._product('Desk ')
        kept_variant = desk.product_variant_id
        gone_variant = desk2.product_variant_id
        line = self._sell(gone_variant)
        self.assertEqual(line.product_id, gone_variant)
        wizard = self._start([desk.id, desk2.id])
        self.assertEqual(wizard.kept_res_id, desk.id)
        wizard.action_merge()
        self._refresh()
        self.assertEqual(line.product_id, kept_variant)
        self.assertFalse(desk2.active)
        self.assertTrue(desk2.exists())
        gone = self.env['product.product'].with_context(active_test=False).browse(gone_variant.id)
        self.assertTrue(gone.exists(), "the merged-away variant is archived, never deleted")
        self.assertFalse(gone.active)
        self.assertEqual(gone.product_tmpl_id, desk2, "the variant stays on its own template")
        self.assertTrue(desk.active)
        self.assertEqual(desk.product_variant_ids, kept_variant)

    def test_template_messages_move_to_the_kept_template(self):
        """Requirements: FR-DRD-0011. Messages of the merged-away template move too."""
        desk = self._product('Desk')
        desk2 = self._product('Desk ')
        desk2.message_post(body='T006 template note')
        self._merge([desk.id, desk2.id])
        self._refresh()
        count = lambda t: self.env['mail.message'].sudo().search_count([
            ('model', '=', 'product.template'), ('res_id', '=', t.id),
            ('body', 'ilike', 'T006 template note')])
        self.assertEqual(count(desk), 1)
        self.assertEqual(count(desk2), 0)

    # FR-DRD-0012 with the barcode uniqueness check
    def test_barcode_of_the_merged_away_product_fills_the_kept_one(self):
        """Requirements: FR-DRD-0012. Filling the barcode raises no uniqueness error."""
        desk = self._product('Desk', default_code='DESK-T006')
        desk2 = self._product('Desk ', barcode='T006-BARCODE', default_code='OTHER-T006')
        self._merge([desk.id, desk2.id])
        self._refresh()
        self.assertEqual(desk.barcode, 'T006-BARCODE')
        self.assertEqual(desk.default_code, 'DESK-T006', "a value already there is never overwritten")
        self.assertFalse(desk2.active)

    def test_group_becomes_merged(self):
        """Requirements: FR-DRD-0011, FR-DRD-0012. The set is marked merged on success."""
        desk = self._product('Desk')
        desk2 = self._product('Desk ')
        group = self._set(desk | desk2)
        wizard = self.env['duplicate.merge.wizard'].with_user(self.manager).with_context(
            active_model='duplicate.group', active_id=group.id, active_ids=group.ids).create({})
        wizard.action_merge()
        self._refresh()
        self.assertEqual(group.state, 'merged')
        self.assertEqual(group.kept_res_id, desk.id)
        self.assertEqual(group.merge_user_id, self.manager)

    def test_manager_without_product_rights_merges(self):
        """Requirements: FR-DRD-0011. Being a duplicate manager is enough to merge products."""
        self.assertFalse(self.manager.has_group('product.group_product_manager'))
        desk = self._product('Desk')
        desk2 = self._product('Desk ', barcode='T006-MGR')
        line = self._sell(desk2.product_variant_id)
        self._merge([desk.id, desk2.id], user=self.manager)
        self._refresh()
        self.assertFalse(desk2.active)
        self.assertEqual(desk.barcode, 'T006-MGR')
        self.assertEqual(line.product_id, desk.product_variant_id)

    # SC-DRD-0016-1
    def test_sc_0016_1_product_with_several_variants_refused(self):
        """Requirements: FR-DRD-0016. Scenario SC-DRD-0016-1."""
        attribute = self.env['product.attribute'].create({
            'name': 'T006 size',
            'value_ids': [Command.create({'name': n}) for n in ('S', 'M', 'L')],
        })
        tshirt = self._product('T-shirt', attribute_line_ids=[Command.create({
            'attribute_id': attribute.id, 'value_ids': [Command.set(attribute.value_ids.ids)]})])
        tshirt2 = self._product('Tshirt', barcode='T006-TSHIRT')
        self.assertEqual(len(tshirt.product_variant_ids), 3)
        with self.assertRaisesRegex(UserError, '(?i)several variants'):
            self._merge([tshirt.id, tshirt2.id])
        self._refresh()
        self.assertTrue(tshirt.active and tshirt2.active)
        self.assertEqual(len(tshirt.product_variant_ids), 3)
        self.assertFalse(tshirt.barcode)
        self.assertEqual(tshirt2.barcode, 'T006-TSHIRT')

    # SC-DRD-0016-2
    def test_sc_0016_2_different_units_of_measure_refused(self):
        """Requirements: FR-DRD-0016. Scenario SC-DRD-0016-2."""
        water = self._product('Water', uom_id=self.litre.id)
        water2 = self._product('Water ', barcode='T006-WATER')
        line = self._sell(water2.product_variant_id)
        with self.assertRaisesRegex(UserError, '(?i)units? of measure'):
            self._merge([water.id, water2.id])
        self._refresh()
        self.assertTrue(water.active and water2.active)
        self.assertFalse(water.barcode)
        self.assertEqual(line.product_id, water2.product_variant_id)

    def test_same_units_of_measure_are_accepted(self):
        """Requirements: FR-DRD-0016. Boundary: equal units are not refused."""
        water = self._product('Water', uom_id=self.litre.id)
        water2 = self._product('Water ', uom_id=self.litre.id)
        self._merge([water.id, water2.id])
        self._refresh()
        self.assertTrue(water.active)
        self.assertFalse(water2.active)
