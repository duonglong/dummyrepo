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
