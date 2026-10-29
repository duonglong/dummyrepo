import os
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tools.translate import code_translations

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

    def test_pot_contains_field_label(self):
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

    def test_error_message_translatable(self):
        """Requirements: NFR-USE-001 - the error message goes through _()."""
        msgid = 'The Weekend Multiplier must be greater than 0.'
        translations = {msgid: 'TRANSLATED ' + msgid}
        self.env['res.lang']._activate_lang('fr_FR')
        program = self.env['loyalty.program'].create({'name': 'Translated'})
        with patch.object(
            type(code_translations), 'get_python_translations',
            lambda _self, module, lang: translations
            if (module, lang) == ('loyalty_weekend_multiplier', 'fr_FR') else {},
        ), self.assertRaisesRegex(ValidationError, 'TRANSLATED'):
            program.with_context(lang='fr_FR').weekend_multiplier = 0
