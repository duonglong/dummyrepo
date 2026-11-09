from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form
from odoo.tests.common import TransactionCase


class TestMergePreview(TransactionCase):
    """Unit tests of the merge preview dialog.

    Requirements: BR-DRD-0006, FR-DRD-0009, FR-DRD-0017
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        base = cls.env.ref('base.group_user')
        manager_group = cls.env.ref('new_duplicate_record_detector.group_duplicate_manager')
        cls.manager = cls.env['res.users'].create({
            'name': 'MP Mgr', 'login': 'mp_mgr', 'group_ids': [Command.set((base | manager_group).ids)]})
        cls.sales = cls.env['res.users'].create({
            'name': 'MP Sales', 'login': 'mp_sales', 'group_ids': [Command.set(base.ids)]})
        cls.a, cls.b = cls.env['res.partner'].create([
            {'name': 'MP Acme'}, {'name': 'MP Acme Ltd', 'phone': '555-0100'}])

    def _wizard(self, records, user=None):
        wizard = self.env['duplicate.merge.wizard'].with_user(user or self.manager)
        return wizard.with_context(
            active_model=records._name, active_ids=records.ids).create({})

    def test_keep_this_one_button_changes_the_kept_record(self):
        """Requirements: FR-DRD-0009. The line button makes its record the kept one."""
        wizard = self._wizard(self.a | self.b)
        wizard.record_line_ids.filtered(lambda l: l.res_id == self.b.id).action_keep_this()
        self.assertEqual(wizard.kept_res_id, self.b.id)
        self.assertEqual(wizard.archive_names, 'MP Acme')

    def test_bug_b_0161_archived_record_of_a_set_is_left_out_of_the_preview(self):
        """Regression test for B-0161: an archived record in an open set blocked Confirm merge.

        Requirements: FR-DRD-0010, BR-DRD-0004
        """
        archived = self.env['res.partner'].create({'name': 'MP Acme Old', 'active': False})
        rule = self.env['duplicate.rule'].create({
            'name': 'MP rule archived', 'model_id': self.env['ir.model']._get('res.partner').id,
            'condition_ids': [Command.create({
                'field_id': self.env['ir.model.fields']._get('res.partner', 'email').id})]})
        group = self.env['duplicate.group'].create({
            'rule_id': rule.id, 'model_name': 'res.partner',
            'line_ids': [Command.create({'res_model': 'res.partner', 'res_id': record.id, 'name': record.name})
                         for record in self.a | self.b | archived]})
        wizard = self.env['duplicate.merge.wizard'].with_user(self.manager).with_context(
            active_model='duplicate.group', active_id=group.id).create({})
        self.assertEqual(wizard.record_line_ids.mapped('res_id'), [self.a.id, self.b.id])
        wizard.action_merge()
        self.assertFalse(self.b.active)

    def test_non_manager_cannot_use_the_dialog(self):
        """Requirements: FR-DRD-0017. Starting, changing and the line button are all refused."""
        wizard = self._wizard(self.a | self.b)
        with self.assertRaises(AccessError):
            wizard.with_user(self.sales).write({'kept_res_id': self.b.id})
        with self.assertRaises(AccessError):
            wizard.record_line_ids[0].with_user(self.sales).action_keep_this()
        self.assertEqual(wizard.kept_res_id, self.a.id)

    def test_set_mixing_record_types_is_refused(self):
        """Requirements: FR-DRD-0009. One record type only."""
        rule = self.env['duplicate.rule'].create({
            'name': 'MP rule', 'model_id': self.env['ir.model']._get('res.partner').id,
            'condition_ids': [Command.create({
                'field_id': self.env['ir.model.fields']._get('res.partner', 'email').id})]})
        group = self.env['duplicate.group'].create({
            'rule_id': rule.id, 'model_name': 'res.partner',
            'line_ids': [
                Command.create({'res_model': 'res.partner', 'res_id': self.a.id, 'name': 'a'}),
                Command.create({'res_model': 'product.template', 'res_id': 1, 'name': 'b'})]})
        with self.assertRaisesRegex(UserError, 'one record type'):
            self.env['duplicate.merge.wizard'].with_user(self.manager).with_context(
                active_model='duplicate.group', active_id=group.id).create({})

    def test_other_record_types_are_refused(self):
        """Requirements: FR-DRD-0009. Only contacts and product templates can be merged."""
        users = self.sales | self.manager
        with self.assertRaisesRegex(UserError, 'contacts or product templates'):
            self.env['duplicate.merge.wizard'].with_user(self.manager).with_context(
                active_model='res.users', active_ids=users.ids).create({})

    def test_product_templates_can_be_previewed(self):
        """Requirements: FR-DRD-0009. The preview serves product templates too."""
        products = self.env['product.template'].create([
            {'name': 'MP Chair'}, {'name': 'MP Chair Pro', 'description_sale': 'Sturdy'}])
        wizard = self._wizard(products)
        code = wizard.value_line_ids.filtered(lambda l: l.field_name == 'description_sale')
        self.assertEqual(code.kept_value, 'Sturdy')
        self.assertTrue(code.is_filled)
        self.assertEqual(wizard.archive_names, 'MP Chair Pro')

    def test_leaving_out_the_kept_record_is_refused(self):
        """Requirements: BR-DRD-0006. The kept record must take part in the merge."""
        wizard = self._wizard(self.a | self.b)
        with self.assertRaises(UserError):
            wizard.record_line_ids.filtered(lambda l: l.res_id == self.a.id).include = False
            wizard._check_selection()

    def test_merge_actions_are_bound_to_managers_only(self):
        """Requirements: FR-DRD-0017. The list actions and the set button need the manager group."""
        manager_group = self.env.ref('new_duplicate_record_detector.group_duplicate_manager')
        for xml_id in ('action_merge_duplicate_contacts', 'action_merge_duplicate_products'):
            action = self.env.ref('new_duplicate_record_detector.' + xml_id)
            self.assertEqual(action.group_ids, manager_group)
        form = self.env['duplicate.group'].with_user(self.manager).get_view(view_type='form')['arch']
        self.assertIn('action_merge_preview', form)


class TestMergePreviewForm(TransactionCase):
    """The merge preview dialog as the browser form saves it.

    Requirements: FR-DRD-0009, FR-DRD-0011
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        manager_group = cls.env.ref('new_duplicate_record_detector.group_duplicate_manager')
        cls.manager = cls.env['res.users'].create({
            'name': 'MPF Mgr', 'login': 'mpf_mgr',
            'group_ids': [Command.set((cls.env.ref('base.group_user') | manager_group).ids)]})
        cls.a, cls.b = cls.env['res.partner'].create([{'name': 'MPF Acme'}, {'name': 'MPF Acme Ltd'}])

    def _form(self):
        wizard = self.env['duplicate.merge.wizard'].with_user(self.manager).with_context(
            active_model='res.partner', active_ids=(self.a | self.b).ids)
        return Form(wizard)

    def test_bug_b_0151_preview_saves_from_the_form_and_confirms(self):
        """Regression test for B-0151, B-0152: Confirm merge failed with a missing 'Res' (res_id).

        Requirements: FR-DRD-0009, FR-DRD-0011
        """
        wizard = self._form().save()
        self.assertEqual(len(wizard.record_line_ids), 2)
        wizard.action_merge()
        self.assertFalse(self.b.active)

    def test_bug_b_0150_keep_this_one_works_on_the_saved_preview(self):
        """Regression test for B-0150: Keep this one did not change the kept record.

        Requirements: FR-DRD-0009
        """
        wizard = self._form().save()
        wizard.record_line_ids.filtered(lambda line: line.res_id == self.b.id).action_keep_this()
        self.assertEqual(wizard.kept_res_id, self.b.id)

    def test_bug_b_0155_unticking_include_recomputes_the_preview(self):
        """Regression test for B-0155: unticking Include crashed the preview's onchange.

        Requirements: FR-DRD-0009, BR-DRD-0006
        """
        c = self.env['res.partner'].create({'name': 'MPF Acme Inc'})
        wizard = self.env['duplicate.merge.wizard'].with_user(self.manager).with_context(
            active_model='res.partner', active_ids=(self.a | self.b | c).ids)
        form = Form(wizard)
        with form.record_line_ids.edit(2) as line:
            line.include = False
        saved = form.save()
        self.assertEqual(saved.record_line_ids.filtered('include').mapped('res_id'), [self.a.id, self.b.id])
        self.assertNotIn('MPF Acme Inc', saved.archive_names)


class TestMergePreviewOpensSaved(TransactionCase):
    """The merge preview opens as a saved record, so the line button of the browser can run.

    Requirements: FR-DRD-0009
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        manager_group = cls.env.ref('new_duplicate_record_detector.group_duplicate_manager')
        cls.manager = cls.env['res.users'].create({
            'name': 'MPO Mgr', 'login': 'mpo_mgr',
            'group_ids': [Command.set((cls.env.ref('base.group_user') | manager_group).ids)]})
        cls.a, cls.b = cls.env['res.partner'].create([{'name': 'MPO Acme'}, {'name': 'MPO Acme Ltd'}])

    def _assert_opens_saved(self, action):
        wizard = self.env['duplicate.merge.wizard'].browse(action['res_id']).exists()
        self.assertEqual(action['res_model'], 'duplicate.merge.wizard')
        self.assertEqual(action['target'], 'new')
        self.assertEqual(wizard.record_line_ids.mapped('res_id'), [self.a.id, self.b.id])

    def test_bug_b_0158_list_action_opens_a_saved_preview(self):
        """Regression test for B-0158: Keep this one showed 'Please save your changes first'.

        The browser refuses a line button on a line of an unsaved dialog; the list action of
        contacts must open a preview that is already saved.

        Requirements: FR-DRD-0009
        """
        action = self.env.ref('new_duplicate_record_detector.action_merge_duplicate_contacts')
        result = action.with_user(self.manager).with_context(
            active_model='res.partner', active_ids=(self.a | self.b).ids).run()
        self._assert_opens_saved(result)

    def test_bug_b_0158_set_button_opens_a_saved_preview(self):
        """Regression test for B-0158: the Merge button of a set opens a saved preview.

        Requirements: FR-DRD-0009
        """
        rule = self.env['duplicate.rule'].create({
            'name': 'MPO rule', 'model_id': self.env['ir.model']._get('res.partner').id,
            'condition_ids': [Command.create({
                'field_id': self.env['ir.model.fields']._get('res.partner', 'email').id})]})
        group = self.env['duplicate.group'].create({
            'rule_id': rule.id, 'model_name': 'res.partner',
            'line_ids': [Command.create({'res_model': 'res.partner', 'res_id': r.id, 'name': r.name})
                         for r in (self.a, self.b)]})
        self._assert_opens_saved(group.with_user(self.manager).action_merge_preview())
