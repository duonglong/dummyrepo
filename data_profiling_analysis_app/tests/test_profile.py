from lxml import etree

from odoo.exceptions import ValidationError
from odoo.tests import tagged

from .common import DpaCase


@tagged('post_install', '-at_install')
class TestProfile(DpaCase):
    """Unit tests of the profile model."""

    def _profile(self, **vals):
        return self.env['dpa.profile'].with_user(self.member).create({
            'name': 'P', 'model_id': self.env['ir.model']._get_id('res.partner'), **vals})

    def test_profilable_fields_leave_out_technical_binary_and_one2many(self):
        """Requirements: BR-DPA-0002."""
        names = set(self._profile()._profilable_field_names())
        self.assertIn('name', names)
        self.assertTrue(names.isdisjoint({'id', 'create_uid', 'create_date', 'write_uid', 'write_date',
                                          'child_ids', 'image_1920'}))

    def test_technical_field_refused(self):
        """Requirements: BR-DPA-0002 (a field that cannot be profiled is refused)."""
        profile = self._profile()
        bad = self.env['ir.model.fields']._get('res.partner', 'write_date')
        with self.assertRaises(ValidationError):
            profile.field_ids = bad

    def test_chosen_profilable_field_saved(self):
        """Requirements: BR-DPA-0002."""
        field = self.env['ir.model.fields']._get('res.partner', 'name')
        self.assertEqual(self._profile(field_ids=[(6, 0, field.ids)]).field_ids, field)

    def test_app_models_not_offered(self):
        """Requirements: FR-DPA-0001."""
        allowed = self._profile().with_user(self.member).allowed_model_ids
        self.assertFalse(allowed.filtered(lambda m: m.model.startswith('dpa.')))

    def test_field_of_another_model_refused(self):
        """Requirements: BR-DPA-0002 (a field of another model is refused)."""
        profile = self._profile()
        other = self.env['ir.model.fields']._get('res.users', 'login')
        with self.assertRaises(ValidationError):
            profile.field_ids = other

    def test_unlisted_field_type_not_profilable(self):
        """Requirements: BR-DPA-0002 (only the designed types are offered)."""
        model_id = self.env['ir.model']._get_id('properties.base.definition')
        profile = self.env['dpa.profile'].create({'name': 'P', 'model_id': model_id})
        self.assertEqual(
            self.env['properties.base.definition']._fields['properties_definition'].type, 'properties_definition')
        self.assertNotIn('properties_definition', profile._profilable_field_names())

    def test_switching_to_transient_model_refused(self):
        """Requirements: FR-DPA-0001 (the write path refuses a model that holds no records)."""
        profile = self._profile()
        transient = self.env['ir.model']._get_id('base.language.install')
        with self.assertRaises(ValidationError):
            profile.write({'model_id': transient})


@tagged('post_install', '-at_install')
class TestProfileArchive(DpaCase):
    """Requirements: FR-DPA-0031."""

    def test_bug_b_0302_profile_form_offers_archive(self):
        """Regression test for B-0302: the form menu offers Archive, which needs the active field in the view.

        Walk state: opened the Profile form view as a member. The test does not render the action menu.
        Requirements: FR-DPA-0031
        """
        view = self.env['dpa.profile'].with_user(self.member).get_view(view_type='form')
        names = etree.fromstring(view['arch']).xpath('//field/@name')
        self.assertIn('active', names)
