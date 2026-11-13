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
        with self.assertRaisesRegex(ValidationError, 'Email Thread'):
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

    def test_bank_accounts_link_is_sensitive_by_default(self):
        """SC-DGQ-0075-3 / BR-DGQ-0010: Contact's Bank Accounts is left out until allowed (through
        the form of the mark; the save button belongs to the screen review)."""
        Config = self.env['data.quality.field.config']
        self.assertNotIn('bank_ids', Config._get_usable_field_names('res.partner'))
        with Form(Config) as form:
            form.field_id = self.env['ir.model.fields']._get('res.partner', 'bank_ids')
            form.status = 'allowed'
        self.assertIn('bank_ids', Config._get_usable_field_names('res.partner'))

    def test_bank_account_numbers_are_sensitive_on_any_model(self):
        """BR-DGQ-0010: iban and account number names are sensitive whatever the model."""
        Config = self.env['data.quality.field.config']
        Fields = self.env['ir.model.fields']
        for model, name in (
                ('account.bank.statement.line', 'account_number'),
                ('res.partner.bank', 'acc_number'),
                ('res.partner.bank', 'sanitized_acc_number')):
            field = Fields._get(model, name)
            if field:
                self.assertTrue(Config._is_sensitive_by_default(field), name)
        fake = Fields.new({'name': 'x_qr_iban', 'model': 'res.partner', 'ttype': 'char'})
        self.assertTrue(Config._is_sensitive_by_default(fake))
        self.assertFalse(Config._is_sensitive_by_default(
            Fields.new({'name': 'x_account_name', 'model': 'res.partner', 'ttype': 'char'})))

    def test_field_hidden_from_everyone_is_sensitive_by_default(self):
        """BR-DGQ-0010: a field declared with no group allowed is sensitive although Odoo lists
        no group for it (the ir.model.fields record of such a field has no groups)."""
        Config = self.env['data.quality.field.config']
        hidden = self.env['ir.model.fields']._get('mail.message', 'res_access_read')
        plain = self.env['ir.model.fields']._get('mail.message', 'subject')
        self.assertFalse(hidden.groups)
        self.assertTrue(Config._is_sensitive_by_default(hidden))
        self.assertFalse(Config._is_sensitive_by_default(plain))

    def test_field_mark_refused_on_unmarkable_field(self):
        """FR-DGQ-0075: a non-stored field or a field of a model without table is refused."""
        Config = self.env['data.quality.field.config']
        for model, name in (('res.partner', 'display_name'), ('mail.thread', 'message_ids')):
            field = self.env['ir.model.fields']._get(model, name)
            with self.assertRaises(ValidationError):
                Config.create({'field_id': field.id, 'status': 'excluded'})
