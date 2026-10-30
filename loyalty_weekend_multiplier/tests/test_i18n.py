import os

from odoo.modules.module import get_module_path
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestI18n(TransactionCase):
    """The module ships an English translation template.

    Requirements: NFR-USE-001
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = os.path.join(
            get_module_path('loyalty_weekend_multiplier'),
            'i18n', 'loyalty_weekend_multiplier.pot')
        cls.pot_exists = os.path.isfile(path)
        cls.pot = ''
        if cls.pot_exists:
            with open(path, encoding='utf-8') as pot_file:
                cls.pot = pot_file.read()

    def test_pot_file_exists(self):
        """Requirements: NFR-USE-001 - a .pot template is shipped."""
        self.assertTrue(self.pot_exists)

    def test_pot_holds_field_label_help_and_error(self):
        """Requirements: NFR-USE-001 - label, help and error are exported."""
        for text in (
            'msgid "Weekend Multiplier"',
            'msgid "The Weekend Multiplier must be greater than 0."',
            'Factor applied to the points a Loyalty Cards program earns',
        ):
            self.assertIn(text, self.pot)

    def test_field_label_is_english_source(self):
        """Requirements: NFR-USE-001 - the source label is English."""
        field = self.env['loyalty.program']._fields['weekend_multiplier']
        self.assertEqual(field.string, 'Weekend Multiplier')
        self.assertTrue(field.help.startswith('Factor applied'))
