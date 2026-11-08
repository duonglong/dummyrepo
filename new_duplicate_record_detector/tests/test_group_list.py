from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase


class TestGroupList(TransactionCase):
    """The duplicates list and the not-a-duplicate decision.

    Requirements: FR-DRD-0007, FR-DRD-0008
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        base = cls.env.ref('base.group_user')
        manager_group = cls.env.ref('new_duplicate_record_detector.group_duplicate_manager')
        cls.manager = cls.env['res.users'].create({
            'name': 'GL Mgr', 'login': 'gl_mgr', 'group_ids': [Command.set((base | manager_group).ids)]})
        cls.sales = cls.env['res.users'].create({
            'name': 'GL Sales', 'login': 'gl_sales', 'group_ids': [Command.set(base.ids)]})
        cls.rule = cls.env['duplicate.rule'].create({
            'name': 'GL rule', 'model_id': cls.env['ir.model']._get('res.partner').id,
            'condition_ids': [Command.create({
                'field_id': cls.env['ir.model.fields']._get('res.partner', 'email').id,
                'match_type': 'exact'})]})
        cls.env['res.partner'].create([
            {'name': 'GL A', 'email': 'g@gl-list.com'}, {'name': 'GL B', 'email': 'g@gl-list.com'}])
        cls.env.flush_all()
        cls.group = cls.env['duplicate.group'].search([('rule_id', '=', cls.rule.id)])

    def test_not_a_duplicate_button_closes_an_open_set(self):
        """Requirements: FR-DRD-0008. The button of a manager sets the state."""
        self.group.with_user(self.manager).action_not_duplicate()
        self.assertEqual(self.group.state, 'not_duplicate')

    def test_not_a_duplicate_leaves_merged_sets_alone(self):
        """Requirements: FR-DRD-0008. Only an open set is closed by the button."""
        self.group.state = 'merged'
        self.group.with_user(self.manager).action_not_duplicate()
        self.assertEqual(self.group.state, 'merged')

    def test_sales_user_cannot_read_or_close_sets(self):
        """Requirements: FR-DRD-0007, FR-DRD-0008. Ordinary users have no access to the sets."""
        with self.assertRaises(AccessError):
            self.env['duplicate.group'].with_user(self.sales).search([])
        with self.assertRaises(AccessError):
            self.group.with_user(self.sales).action_not_duplicate()

    def test_list_view_has_default_open_filter_and_filters(self):
        """Requirements: FR-DRD-0007. The action opens on open sets; type filters exist."""
        action = self.env.ref('new_duplicate_record_detector.duplicate_group_action')
        self.assertIn("'search_default_open': 1", action.context)
        arch = self.env['duplicate.group'].with_user(self.manager).get_view(view_type='search')['arch']
        for name in ('open', 'merged', 'not_duplicate', 'contacts', 'products'):
            self.assertIn(f'name="{name}"', arch)

    def test_bug_b_0156_list_shows_readable_type_and_sized_columns(self):
        """Regression test for B-0156: the list names the record type, not the model.

        Requirements: FR-DRD-0007
        """
        self.assertEqual(self.group.record_type, 'Contact')
        arch = self.env['duplicate.group'].with_user(self.manager).get_view(view_type='list')['arch']
        self.assertIn('name="record_type"', arch)
        self.assertNotIn('name="model_name"', arch)
        self.assertIn('string="Records"', arch)
        for name in ('rule_id', 'record_type', 'record_count'):
            self.assertRegex(arch, rf'<field name="{name}"[^>]*width=')
