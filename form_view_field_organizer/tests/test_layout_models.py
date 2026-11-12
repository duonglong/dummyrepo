from odoo import Command
from odoo.exceptions import ValidationError

from .common import FvfCommon


class TestLayoutModels(FvfCommon):
    """Editor model behaviour beyond the acceptance tests.

    Requirements: FR-FVF-0001, FR-FVF-0016, FR-FVF-0017
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.layout = cls.env['fvf.layout'].create({
            'model_id': cls.env['ir.model']._get('res.partner').id,
            'view_id': cls.env.ref('base.view_partner_form').id,
        })

    def test_default_name_names_model_and_scope(self):
        """FR-FVF-0001: the name defaults to the model and the company or Default."""
        self.assertEqual(self.layout.name, "Contact – Default")

    def test_refresh_adds_no_duplicate_line(self):
        """FR-FVF-0001: refreshing the fields keeps one line per field."""
        count = len(self.layout.line_ids)
        self.layout.action_refresh_fields()
        self.assertEqual(len(self.layout.line_ids), count)

    def test_add_section_creates_open_section_after_last(self):
        """FR-FVF-0003: a new section lands after the last one."""
        first = self.layout.action_add_section('A')
        second = self.layout.action_add_section('B')
        self.assertGreater(second.sequence, first.sequence)
        self.assertEqual(first.start_state, 'open')

    def test_condition_on_date_field_only_offers_is_set(self):
        """FR-FVF-0005: a date and time field offers only is set / is not set."""
        line = self.layout.line_ids.filtered(lambda l: l.field_name == 'vat')
        with self.assertRaisesRegex(ValidationError, 'cannot be used with this kind of field'):
            with self.cr.savepoint():
                line.write({'condition_ids': [Command.create({
                    'field_name': 'create_date', 'operator': 'is', 'value': '2020-01-01'})]})

    def _condition_vals(self, field_name, operator, value=False, **extra):
        return {'condition_ids': [Command.create(
            {'field_name': field_name, 'operator': operator, 'value': value, **extra})]}

    def _refused(self, message, vals):
        line = self.layout.line_ids.filtered(lambda l: l.field_name == 'vat')
        with self.assertRaisesRegex(ValidationError, message):
            with self.cr.savepoint():
                line.write(vals)

    def test_model_choice_offers_models_with_a_main_form_including_transient(self):
        """FR-FVF-0001: any model with a main form view is offered, wizards too; others are not."""
        domain = self.env['fvf.layout']._fields['model_id']._description_domain(self.env)
        offered = self.env['ir.model'].search(domain).mapped('model')
        self.assertIn('res.partner', offered)
        self.assertIn('base.language.install', offered)
        self.assertTrue(self.env['ir.model'].search([('model', '=', 'ir.qweb')]))
        self.assertNotIn('ir.qweb', offered)

    def test_condition_on_unknown_field_refused(self):
        """FR-FVF-0005: a field name the form's model does not have is refused when saved."""
        self._refused('Choose a field of this form', self._condition_vals('countri_id', 'set'))

    def test_condition_on_non_stored_field_refused(self):
        """FR-FVF-0005: a field the database does not store is refused."""
        self._refused('Choose a field of this form', self._condition_vals('contact_address', 'set'))

    def test_condition_value_must_fit_the_field_kind(self):
        """FR-FVF-0005: a value that cannot be read for the kind of field is refused."""
        for field_name, operator, value in [
                ('country_id', 'is', 'Belgium'),
                ('country_id', 'is', '999999999'),
                ('color', 'gt', 'ten'),
                ('type', 'is', 'maybe'),
                ('is_company', 'is', 'maybe'),
                ('partner_latitude', 'gt', 'nan')]:
            with self.subTest(field=field_name, value=value):
                self._refused('Enter a value for this comparison',
                              self._condition_vals(field_name, operator, value))

    def test_condition_value_fitting_the_field_kind_accepted(self):
        """FR-FVF-0005: a readable value for each kind of field is saved."""
        line = self.layout.line_ids.filtered(lambda l: l.field_name == 'vat')
        belgium = self.env.ref('base.be')
        for field_name, operator, value, extra in [
                ('country_id', 'is', False, {'value_res_id': belgium.id}),
                ('color', 'gt', '10', {}),
                ('type', 'is', 'contact', {}),
                ('is_company', 'is', 'true', {}),
                ('partner_latitude', 'le', '1.5', {})]:
            with self.subTest(field=field_name):
                line.write(self._condition_vals(field_name, operator, value, **extra))
        self.assertEqual(len(line.condition_ids), 5)

    def test_hiding_required_field_warns_in_editor(self):
        """FR-FVF-0016: choosing a group for a required field warns and puts the groups back."""
        line = self.env['fvf.layout.line'].new({
            'layout_id': self.layout.id, 'field_name': 'name',
            'hidden_group_ids': [Command.set(self.env.ref('base.group_user').ids)]})
        result = line._onchange_hidden_group_ids()
        self.assertIn('A required field cannot be hidden.', result['warning']['message'])
        self.assertFalse(line.hidden_group_ids)

    def test_condition_on_required_field_warns_in_editor(self):
        """FR-FVF-0016: adding a condition to a required field warns and drops the condition."""
        line = self.env['fvf.layout.line'].new({
            'layout_id': self.layout.id, 'field_name': 'name',
            'condition_ids': [Command.create({'field_name': 'vat', 'operator': 'set'})]})
        result = line._onchange_condition_ids()
        self.assertIn('A required field cannot be hidden.', result['warning']['message'])
        self.assertFalse(line.condition_ids)

    def test_condition_on_own_field_warns_in_editor(self):
        """FR-FVF-0017: choosing the line's own field for a condition warns and clears it."""
        line = self.layout.line_ids.filtered(lambda l: l.field_name == 'country_id')
        condition = self.env['fvf.layout.condition'].new(
            {'line_id': line.id, 'field_name': 'country_id', 'operator': 'set'})
        result = condition._onchange_field_name()
        self.assertIn('own value', result['warning']['message'])
        self.assertFalse(condition.field_name)
