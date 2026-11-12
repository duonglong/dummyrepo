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

    def test_condition_on_date_field_only_offers_is_set(self):
        """FR-FVF-0005: a date and time field offers only is set / is not set."""
        line = self.layout.line_ids.filtered(lambda l: l.field_name == 'vat')
        with self.assertRaisesRegex(ValidationError, 'cannot be used with this kind of field'):
            with self.cr.savepoint():
                line.write({'condition_ids': [Command.create({
                    'field_name': 'create_date', 'operator': 'is', 'value': '2020-01-01'})]})
