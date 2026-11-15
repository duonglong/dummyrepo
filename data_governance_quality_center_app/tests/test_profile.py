from odoo.exceptions import UserError

from .acceptance_m3_helpers import M3Case


class TestProfile(M3Case):
    """Own tests of the profile job: field choice, restart and the default field list.

    Requirements: FR-DGQ-0053, FR-DGQ-0054, FR-DGQ-0055
    """

    def test_no_field_chosen_profiles_every_usable_field(self):
        """FR-DGQ-0053: with no field chosen, usable fields get a line and sensitive ones do not."""
        self.isolate('res.partner')
        self.env['res.partner'].create({'name': 'All', 'email': 'a@example.com', 'company_id': self.cx.id})
        profile = self.run_profile(self.make_profile('res.partner', []))
        names = set(profile.sudo().line_ids.mapped('field_name'))
        self.assertIn('email', names)
        self.assertIn('name', names)
        self.assertNotIn('id', names)
        self.assertEqual(profile.state, 'done')

    def test_starting_again_replaces_the_lines(self):
        """FR-DGQ-0053: a second run of a profile does not duplicate its lines."""
        self.isolate('res.partner')
        self.env['res.partner'].create({'name': 'Again', 'company_id': self.cx.id})
        profile = self.run_profile(self.make_profile('res.partner', ['name']))
        profile.sudo().write({'state': 'failed'})
        self.run_profile(profile)
        self.assertEqual(len(profile.sudo().line_ids), 1)
        self.assertEqual(profile.record_count, 1)

    def test_an_empty_model_has_zero_shares(self):
        """FR-DGQ-0053: no record gives zero empty values, not a division by zero."""
        self.isolate('res.partner')
        profile = self.run_profile(self.make_profile('res.partner', ['email']))
        line = self.line(profile, 'email')
        self.assertEqual((profile.record_count, line.empty_count, line.empty_share), (0, 0, 0.0))

    def test_a_stored_field_without_column_is_refused(self):
        """FR-DGQ-0053: a many2many field has no column in the table and cannot be profiled."""
        field = self.fld('res.partner', 'category_id')
        with self.assertRaises(UserError):
            self.env['data.quality.profile'].with_user(self.operator).create({
                'model_id': self.model('res.partner').id, 'company_id': self.cx.id,
                'field_ids': [(6, 0, field.ids)]})

    def test_unusual_numbers_leave_out_other_companies_and_archived_records(self):
        """FR-DGQ-0054: a huge weight of another company or of an archived product is not counted."""
        self.isolate('product.template')
        Product = self.env['product.template']
        for n, weight in enumerate((4, 5, 5, 6, 7)):
            Product.create({'name': 'Light %s' % n, 'weight': weight, 'company_id': self.cx.id})
        Product.create({'name': 'Other', 'weight': 5000, 'company_id': self.cy.id})
        Product.create({'name': 'Old', 'weight': 6000, 'company_id': self.cx.id, 'active': False})
        profile = self.run_profile(self.make_profile('product.template', ['weight']))
        self.assertEqual(self.line(profile, 'weight').outlier_count, 0)

    def test_a_field_marked_sensitive_after_saving_is_not_profiled(self):
        """FR-DGQ-0053: the mark is checked when the job runs; the job fails and stores no line."""
        self.isolate('res.partner')
        self.env['res.partner'].create({'name': 'Secret', 'function': 'Boss', 'company_id': self.cx.id})
        profile = self.make_profile('res.partner', ['function'])
        self.env['data.quality.field.config'].sudo().create(
            {'field_id': self.fld('res.partner', 'function').id, 'status': 'excluded'})
        profile.action_start()
        self.drain_job(profile)
        profile.invalidate_recordset()
        self.assertEqual(profile.state, 'failed')
        self.assertFalse(profile.sudo().line_ids)
        self.assertIn('now excluded or sensitive', profile.error_message)
        self.assertIn('Create a new profile without this field', profile.error_message)

    def test_a_model_excluded_after_saving_is_not_profiled(self):
        """FR-DGQ-0053: a model no longer marked supported stops the job."""
        self.isolate('res.partner')
        profile = self.make_profile('res.partner', ['name'])
        self.env['data.quality.model.config'].sudo().search(
            [('model_id', '=', self.model('res.partner').id)]).write({'status': 'excluded'})
        profile.action_start()
        self.drain_job(profile)
        profile.invalidate_recordset()
        self.assertEqual(profile.state, 'failed')
        self.assertFalse(profile.sudo().line_ids)
        self.assertIn('no longer marked as supported', profile.error_message)

    def test_the_default_field_list_is_fixed_at_the_start(self):
        """FR-DGQ-0053: starting with no field chosen stores the list the checkpoint indexes."""
        self.isolate('res.partner')
        profile = self.make_profile('res.partner', [])
        profile.action_start()
        self.assertIn(self.fld('res.partner', 'email'), profile.field_ids)

    def test_starting_a_done_profile_keeps_its_lines(self):
        """FR-DGQ-0053: the state is checked before any line is deleted.

        No assertRaises: it rolls back to a savepoint and would hide a deletion.
        """
        self.isolate('res.partner')
        profile = self.run_profile(self.make_profile('res.partner', ['name']))
        try:
            profile.action_start()
        except UserError:
            pass
        self.assertEqual(len(profile.sudo().line_ids), 1)

    def test_an_unticked_checkbox_is_a_filled_value(self):
        """FR-DGQ-0053: only a missing value is empty for a checkbox."""
        self.isolate('res.partner')
        self.env['res.partner'].create({'name': 'Box', 'employee': False, 'company_id': self.cx.id})
        profile = self.run_profile(self.make_profile('res.partner', ['employee']))
        self.assertEqual(self.line(profile, 'employee').empty_count, 0)
