from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestDuplicateRule(TransactionCase):
    """Own tests of T-001. Requirements: FR-DRD-0001, FR-DRD-0002."""

    def _field(self, model, name):
        return self.env['ir.model.fields']._get(model, name).id

    def _rule(self, field_name, model='res.partner'):
        return self.env['duplicate.rule'].create({
            'name': 'Rule',
            'model_id': self.env['ir.model']._get(model).id,
            'condition_ids': [Command.create({'field_id': self._field(model, field_name)})],
        })

    def test_field_of_another_model_refused(self):
        """Requirements: FR-DRD-0002. A product field cannot be used in a contact rule."""
        with self.assertRaisesRegex(ValidationError, r'(?i)cannot be used'):
            self.env['duplicate.rule'].create({
                'name': 'Rule',
                'model_id': self.env['ir.model']._get('res.partner').id,
                'condition_ids': [Command.create({
                    'field_id': self._field('product.template', 'default_code')})],
            })

    def test_bookkeeping_field_refused(self):
        """Requirements: FR-DRD-0002. Bookkeeping fields are not offered."""
        for name in ('create_uid', 'write_uid'):
            with self.assertRaises(ValidationError, msg=name):
                self._rule(name)

    def test_changing_record_type_revalidates_conditions(self):
        """Requirements: FR-DRD-0002. Switching the record type orphans the conditions."""
        rule = self._rule('email')
        with self.assertRaises(ValidationError):
            rule.model_id = self.env['ir.model']._get('product.template')
