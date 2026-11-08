from odoo import Command
from odoo.tests.common import TransactionCase


class TestMergeProducts(TransactionCase):
    """Unit tests of the product template merge.

    Requirements: FR-DRD-0011, FR-DRD-0012, FR-DRD-0016.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.T = cls.env['product.template']
        cls.tag_shared, cls.tag_only = cls.env['product.tag'].create([
            {'name': 'T006 shared'}, {'name': 'T006 only'}])

    def _merge(self, kept, *others):
        wizard = self.env['duplicate.merge.wizard'].with_context(
            active_model='product.template', active_ids=(kept | sum(others, self.T)).ids).create({})
        wizard.kept_res_id = kept.id
        wizard.action_merge()
        self.env.invalidate_all()

    def test_shared_tag_stays_once_and_other_tag_moves(self):
        """Requirements: FR-DRD-0011. A link the kept product has stays once; the others move."""
        kept = self.T.create({'name': 'Desk', 'product_tag_ids': [Command.set(self.tag_shared.ids)]})
        gone = self.T.create({
            'name': 'Desk ', 'product_tag_ids': [Command.set((self.tag_shared | self.tag_only).ids)]})
        self._merge(kept, gone)
        self.assertEqual(kept.product_tag_ids, self.tag_shared | self.tag_only)

    def test_empty_default_code_is_filled(self):
        """Requirements: FR-DRD-0012. An empty internal reference takes the merged-away value."""
        kept = self.T.create({'name': 'Desk'})
        gone = self.T.create({'name': 'Desk ', 'default_code': 'T006-REF'})
        self._merge(kept, gone)
        self.assertEqual(kept.default_code, 'T006-REF')

    def test_variant_structure_stays_with_its_template(self):
        """Requirements: FR-DRD-0011. The merged-away variant is not moved under the kept template."""
        kept = self.T.create({'name': 'Desk'})
        gone = self.T.create({'name': 'Desk '})
        variant = gone.product_variant_id
        self._merge(kept, gone)
        self.assertEqual(variant.product_tmpl_id, gone)
        self.assertEqual(kept.product_variant_ids.ids, [kept.product_variant_id.id])

    def _with_attribute(self, template, attribute, value_name):
        value = self.env['product.attribute.value'].create({'attribute_id': attribute.id, 'name': value_name})
        self.env['product.template.attribute.line'].create({
            'product_tmpl_id': template.id, 'attribute_id': attribute.id, 'value_ids': [Command.set(value.ids)]})

    def test_attribute_combination_stays_with_its_variant(self):
        """Requirements: FR-DRD-0011. The kept variant does not take the attribute values of another template."""
        attribute = self.env['product.attribute'].create({'name': 'T006 Brand'})
        kept = self.T.create({'name': 'Desk'})
        gone = self.T.create({'name': 'Desk '})
        self._with_attribute(kept, attribute, 'Acme')
        self._with_attribute(gone, attribute, 'Oak')
        self._merge(kept, gone)
        combination = kept.product_variant_id.product_template_attribute_value_ids
        self.assertEqual(combination.product_tmpl_id, kept)
        self.assertEqual(len(combination), 1)

    def test_activity_and_attachment_move_to_the_kept_template(self):
        """Requirements: FR-DRD-0011. Activities and attachments follow the kept product."""
        kept = self.T.create({'name': 'Desk'})
        gone = self.T.create({'name': 'Desk '})
        activity = gone.activity_schedule('mail.mail_activity_data_todo', summary='T006')
        attachment = self.env['ir.attachment'].create({
            'name': 'T006.txt', 'res_model': 'product.template', 'res_id': gone.id, 'raw': b'x'})
        self._merge(kept, gone)
        self.assertEqual(activity.res_id, kept.id)
        self.assertEqual(attachment.res_id, kept.id)

    def test_archived_variant_does_not_count_as_several(self):
        """Requirements: FR-DRD-0016. A product with one active variant and an archived one merges."""
        attribute = self.env['product.attribute'].create({'name': 'T006 Size', 'create_variant': 'always'})
        kept = self.T.create({'name': 'Desk'})
        gone = self.T.create({'name': 'Desk '})
        values = self.env['product.attribute.value'].create([
            {'attribute_id': attribute.id, 'name': 'S'}, {'attribute_id': attribute.id, 'name': 'M'}])
        self.env['product.template.attribute.line'].create({
            'product_tmpl_id': gone.id, 'attribute_id': attribute.id, 'value_ids': [Command.set(values.ids)]})
        gone.product_variant_ids[1:].write({'active': False})
        self.assertEqual(len(gone.with_context(active_test=False).product_variant_ids), 2)
        self._merge(kept, gone)
        self.assertFalse(gone.active)

    def test_barcode_fills_and_only_that_barcode_is_released(self):
        """Requirements: FR-DRD-0012. The kept product takes one barcode; the others stay on their products."""
        kept = self.T.create({'name': 'Desk'})
        first = self.T.create({'name': 'Desk 1', 'barcode': 'T006-A'})
        second = self.T.create({'name': 'Desk 2', 'barcode': 'T006-B'})
        self._merge(kept, first, second)
        self.assertIn(kept.barcode, ('T006-A', 'T006-B'))
        moved_from, stays = (first, second) if kept.barcode == 'T006-A' else (second, first)
        self.assertFalse(moved_from.barcode)
        self.assertEqual(stays.barcode, 'T006-B' if kept.barcode == 'T006-A' else 'T006-A')
