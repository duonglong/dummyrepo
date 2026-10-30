from lxml import etree

from odoo.tests.common import TransactionCase


class TestWeekendMultiplierForm(TransactionCase):
    """Acceptance tests of T-002: the field is shown on the form for Loyalty Cards only."""

    def _field_nodes(self):
        view = self.env['loyalty.program'].get_view(view_type='form')
        arch = etree.fromstring(view['arch'])
        return arch.xpath("//field[@name='weekend_multiplier']")

    def test_form_field_visible_for_loyalty(self):
        """Requirements: FR-LAP-001 - the form carries Weekend Multiplier for type loyalty."""
        nodes = self._field_nodes()
        self.assertTrue(nodes, "Weekend Multiplier is missing on the program form")
        invisible = nodes[0].get('invisible') or ''
        self.assertIn("program_type", invisible)
        self.assertIn("loyalty", invisible)

    def test_form_field_hidden_for_gift_card(self):
        """Requirements: FR-LAP-001 - SC-LAP-010-3: a gift card program shows no Weekend Multiplier."""
        nodes = self._field_nodes()
        self.assertTrue(nodes, "Weekend Multiplier is missing on the program form")
        invisible = nodes[0].get('invisible') or ''
        self.assertEqual(
            invisible.replace('"', "'").replace(' ', ''),
            "program_type!='loyalty'",
        )
