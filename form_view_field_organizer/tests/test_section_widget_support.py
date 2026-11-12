from odoo.exceptions import ValidationError

from .common import FvfCommon


class TestSectionWidgetSupport(FvfCommon):
    """Server side of dragging a row in the field lines widget.

    The widget writes section_id and sequence on a line; dragging itself is browser
    behaviour (Hoot tests of the build).

    Requirements: FR-FVF-0006, FR-FVF-0007
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.layout = cls.env['fvf.layout'].create({
            'model_id': cls.env['ir.model']._get('res.partner').id,
            'view_id': cls.env.ref('base.view_partner_form').id,
        })
        cls.section = cls.layout.action_add_section('Accounting')
        cls.line = cls.layout.line_ids.filtered(lambda line: line.field_name == 'vat')

    def test_moving_a_line_sets_section_and_sequence(self):
        """SC-FVF-0007-1 (FR-FVF-0007): a drop into a section writes section_id and sequence."""
        self.line.write({'section_id': self.section.id, 'sequence': 3})
        self.assertEqual(self.line.section_id, self.section)
        self.assertEqual(self.line.sequence, 3)

    def test_dropping_into_original_place_clears_the_section(self):
        """SC-FVF-0007-2 (FR-FVF-0007): a drop into the original-place block empties section_id."""
        self.line.section_id = self.section
        self.line.write({'section_id': False, 'sequence': 1})
        self.assertFalse(self.line.section_id)

    def test_section_of_another_layout_is_refused(self):
        """FR-FVF-0006: a line cannot be moved to a section of another layout."""
        other = self.env['fvf.layout'].create({
            'model_id': self.env['ir.model']._get('res.partner').id,
            'view_id': self.env.ref('base.view_partner_form').id,
            'company_id': self.company_be.id,
        })
        foreign = other.action_add_section('Other')
        with self.assertRaisesRegex(ValidationError, 'belongs to another layout'):
            self.line.section_id = foreign
