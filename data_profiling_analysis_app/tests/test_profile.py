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

    def test_field_of_another_model_refused(self):
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
