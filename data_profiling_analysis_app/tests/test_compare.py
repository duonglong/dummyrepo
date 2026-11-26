from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCompareLines(TransactionCase):
    """Requirements: FR-DPA-0023, FR-DPA-0025."""

    def test_line_values_without_older_has_no_change(self):
        """A field only in the newer snapshot carries no change (FR-DPA-0025)."""
        snapshot = self.env['dpa.snapshot'].create({
            'name': 's', 'model_name': 'res.partner', 'run_date': '2026-11-01 09:00:00',
            'field_line_ids': [(0, 0, {'field_name': 'email', 'field_label': 'Email', 'completeness': 5.0})],
        })
        values = self.env['dpa.snapshot.compare.line']._line_values(1, False, snapshot.field_line_ids)
        self.assertFalse(values['in_older'])
        self.assertEqual(values['newer_completeness'], 5.0)
        self.assertNotIn('completeness_change', values)
