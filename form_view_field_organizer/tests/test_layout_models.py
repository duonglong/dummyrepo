from unittest.mock import patch

from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import Form

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
                ('partner_latitude', 'gt', 'nan'),
                ('is_company', 'is', 'TRUE'),
                ('is_company', 'is', 'tRue'),
                ('color', 'gt', '--5'),
                ('type', 'is', ' contact')]:
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
                ('partner_latitude', 'le', '1.5', {}),
                ('color', 'ge', '-5', {}),
                ('is_company', 'is_not', 'False', {})]:
            with self.subTest(field=field_name):
                line.write(self._condition_vals(field_name, operator, value, **extra))
        self.assertEqual(len(line.condition_ids), 7)

    def test_hiding_required_field_is_refused_in_editor(self):
        """FR-FVF-0016: choosing a group for a required field is refused beside the line and puts the groups back."""
        line = self.env['fvf.layout.line'].new({
            'layout_id': self.layout.id, 'field_name': 'name',
            'hidden_group_ids': [Command.set(self.env.ref('base.group_user').ids)]})
        self.assertIsNone(line._onchange_hidden_group_ids())
        self.assertEqual(line.refusal_notice, 'A required field cannot be hidden.')
        self.assertFalse(line.hidden_group_ids)

    def test_condition_on_required_field_is_refused_in_editor(self):
        """FR-FVF-0016: adding a condition to a required field is refused beside the line and drops the condition."""
        line = self.env['fvf.layout.line'].new({
            'layout_id': self.layout.id, 'field_name': 'name',
            'condition_ids': [Command.create({'field_name': 'vat', 'operator': 'set'})]})
        self.assertIsNone(line._onchange_condition_ids())
        self.assertEqual(line.refusal_notice, 'A required field cannot be hidden.')
        self.assertFalse(line.condition_ids)

    def test_condition_on_own_field_is_refused_in_editor(self):
        """FR-FVF-0017: choosing the line's own field for a condition is refused beside the line and clears it."""
        line = self.layout.line_ids.filtered(lambda l: l.field_name == 'country_id')
        condition = self.env['fvf.layout.condition'].new(
            {'line_id': line.id, 'field_name': 'country_id', 'operator': 'set'})
        self.assertIsNone(condition._onchange_field_name())
        self.assertIn('own value', condition.refusal_notice)
        self.assertFalse(condition.field_name)

    def _field(self, name):
        return self.env['ir.model.fields']._get('res.partner', name)

    def test_changing_condition_field_clears_the_chosen_record(self):
        """FR-FVF-0005: choosing another field drops the record chosen for the previous one.

        The line dialog is opened with Form; its buttons stay for the screen review.
        """
        line = self.layout.line_ids.filtered(lambda l: l.field_name == 'vat')
        belgium = self.env.ref('base.be')
        form = Form(line, view='form_view_field_organizer.fvf_layout_line_view_form')
        with form.condition_ids.new() as condition:
            condition.field_id = self._field('country_id')
            condition.operator = 'is'
            condition.value_res_id = belgium.id
            self.assertEqual(condition.value, str(belgium.id))
            condition.field_id = self._field('state_id')
            self.assertFalse(condition.value)
            self.assertFalse(condition.value_res_id)
            self.assertEqual(condition.operator, 'is')
            condition.field_id = self._field('create_date')
            self.assertFalse(condition.operator)
            condition.operator = 'set'
        form.save()
        self.assertEqual(line.condition_ids.field_name, 'create_date')
        self.assertFalse(line.condition_ids.value_res_id)

    def test_chosen_record_is_the_value_of_a_single_link(self):
        """FR-FVF-0005: a single link's value is the chosen record, shown by its name."""
        line = self.layout.line_ids.filtered(lambda l: l.field_name == 'vat')
        belgium = self.env.ref('base.be')
        line.write(self._condition_vals('country_id', 'is', 'Belgium', value_res_id=belgium.id))
        self.assertEqual(line.condition_ids.value, str(belgium.id))
        self.assertEqual(line.condition_summary, "Country is %s" % belgium.display_name)

    def test_value_disagreeing_with_chosen_record_refused(self):
        """FR-FVF-0005: a typed value that differs from the chosen record is refused."""
        line = self.layout.line_ids.filtered(lambda l: l.field_name == 'vat')
        line.write(self._condition_vals('country_id', 'is', value_res_id=self.env.ref('base.be').id))
        with self.assertRaisesRegex(ValidationError, 'Enter a value for this comparison'):
            with self.cr.savepoint():
                line.condition_ids.write({'value': str(self.env.ref('base.fr').id)})

    def test_field_record_naming_another_field_refused(self):
        """FR-FVF-0005: the field record and the field name must name the same field of the form."""
        self._refused('Choose a field of this form',
                      self._condition_vals('color', 'set', field_id=self._field('country_id').id))
        user_field = self.env['ir.model.fields']._get('res.users', 'color')
        self._refused('Choose a field of this form',
                      self._condition_vals('color', 'set', field_id=user_field.id))

    def test_unreadable_form_does_not_block_the_layouts_list(self):
        """FR-FVF-0001: a form that cannot be read marks its lines missing instead of failing the list."""
        View = type(self.env['ir.ui.view'])
        with patch.object(View, '_get_combined_arch', side_effect=ValueError("broken")):
            self.layout.invalidate_recordset(['missing_count'])
            self.layout.line_ids.invalidate_recordset(['missing', 'is_required'])
            self.assertEqual(self.layout.missing_count, len(self.layout.line_ids))
