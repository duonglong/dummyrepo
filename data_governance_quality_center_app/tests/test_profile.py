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
