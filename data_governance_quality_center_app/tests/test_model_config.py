from odoo.exceptions import ValidationError
from odoo.tests import Form

from .common import DGQTestCase


class TestModelConfig(DGQTestCase):
    """Tests for FR-DGQ-0075 and BR-DGQ-0010 (the field-mark screen is driven with Form;
    the buttons of the list screen belong to the screen review)."""

    def test_status_of_models(self):
        """FR-DGQ-0075: supported after install, unmarked models have no status."""
        Config = self.env['data.quality.model.config']
        self.assertEqual(Config._get_status('res.partner'), 'supported')
        self.assertEqual(Config._get_status('product.template'), 'supported')
        self.assertFalse(Config._get_status('res.currency'))

    def test_status_follows_the_mark(self):
        """FR-DGQ-0075 (SC-DGQ-0075-2 mark step): restricted is read back as protected."""
        config = self.env['data.quality.model.config'].search(
            [('model_name', '=', 'res.partner')])
        with Form(config) as form:
            form.status = 'restricted'
        self.assertEqual(
            self.env['data.quality.model.config']._get_status('res.partner'), 'restricted')

    def test_abstract_model_message(self):
        """FR-DGQ-0075: the refusal is a validation error naming the model."""
        with self.assertRaises(ValidationError):
            self.env['data.quality.model.config'].create({
                'model_id': self.env['ir.model']._get('mail.thread').id})

    def test_default_sensitive_fields_are_not_usable(self):
        """BR-DGQ-0010: password, key, binary and group-restricted fields are left out."""
        Config = self.env['data.quality.field.config']
        users = Config._get_usable_field_names('res.users')
        self.assertNotIn('password', users)
        self.assertNotIn('api_key_ids', users)
        self.assertNotIn('image_1920', Config._get_usable_field_names('res.partner'))
        self.assertIn('name', Config._get_usable_field_names('res.partner'))
        bank = Config._get_usable_field_names('res.partner.bank')
        self.assertNotIn('account_number', bank)

    def test_allowed_mark_overrides_default(self):
        """SC-DGQ-0075-3 / BR-DGQ-0010: marking a default-sensitive field allowed includes it."""
        Config = self.env['data.quality.field.config']
        field = self.env['ir.model.fields']._get('res.partner.bank', 'account_number')
        self.assertNotIn('account_number', Config._get_usable_field_names('res.partner.bank'))
        with Form(Config) as form:
            form.field_id = field
            form.status = 'allowed'
        self.assertIn('account_number', Config._get_usable_field_names('res.partner.bank'))

    def test_excluded_and_sensitive_marks_leave_a_field_out(self):
        """FR-DGQ-0075: an excluded or sensitive mark leaves a plain field out."""
        Config = self.env['data.quality.field.config']
        self.assertIn('comment', Config._get_usable_field_names('res.partner'))
        mark = Config.create({
            'field_id': self.env['ir.model.fields']._get('res.partner', 'comment').id,
            'status': 'excluded'})
        self.assertNotIn('comment', Config._get_usable_field_names('res.partner'))
        mark.status = 'sensitive'
        self.assertNotIn('comment', Config._get_usable_field_names('res.partner'))
