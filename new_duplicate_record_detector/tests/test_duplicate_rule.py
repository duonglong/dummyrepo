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

    def test_create_without_conditions_key_refused(self):
        """Requirements: FR-DRD-0002. An import or RPC create may leave condition_ids out."""
        with self.assertRaisesRegex(ValidationError, r'(?i)at least one condition'):
            self.env['duplicate.rule'].create({
                'name': 'Rule', 'model_id': self.env['ir.model']._get('res.partner').id})

    def test_unlink_last_condition_refused(self):
        """Requirements: FR-DRD-0002. Deleting the condition record directly is refused."""
        rule = self._rule('email')
        with self.assertRaisesRegex(ValidationError, r'(?i)at least one condition'):
            rule.condition_ids.unlink()

    def test_unlink_one_of_two_conditions_allowed(self):
        """Requirements: FR-DRD-0002. A rule that keeps a condition can lose another."""
        rule = self._rule('email')
        rule.condition_ids = [Command.create({'field_id': self._field('res.partner', 'city')})]
        rule.condition_ids[:1].unlink()
        self.assertEqual(len(rule.condition_ids), 1)

    def test_matchable_fields_leave_out_bookkeeping(self):
        """Requirements: FR-DRD-0002. The picker offers only fields the save accepts."""
        rule = self._rule('email')
        names = rule.matchable_field_ids.mapped('name')
        self.assertIn('email', names)
        self.assertNotIn('create_uid', names)

    def test_settings_administrator_is_duplicate_manager(self):
        """Requirements: FR-DRD-0018. A new settings administrator needs no extra setup."""
        user = self.env['res.users'].create({
            'name': 'Admin2', 'login': 'admin2_drd',
            'group_ids': [Command.link(self.env.ref('base.group_system').id)]})
        self.assertTrue(user.has_group('new_duplicate_record_detector.group_duplicate_manager'))

    def test_replace_only_condition_in_one_save(self):
        """Requirements: FR-DRD-0001, FR-DRD-0002. Delete and create in one write is valid."""
        rule = self._rule('email')
        rule.write({'condition_ids': [
            Command.delete(rule.condition_ids.id),
            Command.create({'field_id': self._field('res.partner', 'city')})]})
        self.assertEqual(rule.condition_ids.field_id.name, 'city')

    def test_change_record_type_with_conditions_replaced(self):
        """Requirements: FR-DRD-0001, FR-DRD-0002. Contacts rule becomes a products rule."""
        rule = self._rule('email')
        rule.write({
            'model_id': self.env['ir.model']._get('product.template').id,
            'condition_ids': [
                Command.delete(rule.condition_ids.id),
                Command.create({'field_id': self._field('product.template', 'default_code')})],
        })
        self.assertEqual(rule.condition_ids.field_id.model, 'product.template')

    def test_write_removing_all_conditions_refused(self):
        """Requirements: FR-DRD-0002. The final state is still checked after a write."""
        rule = self._rule('email')
        with self.assertRaisesRegex(ValidationError, r'(?i)at least one condition'):
            rule.write({'condition_ids': [Command.delete(rule.condition_ids.id)]})

    def test_matchable_fields_per_rule(self):
        """Requirements: FR-DRD-0002. Rules of different types get their own fields."""
        partner_rule = self._rule('email')
        product_rule = self._rule('default_code', 'product.template')
        both = partner_rule | product_rule
        self.assertIn('email', both[0].matchable_field_ids.mapped('name'))
        self.assertNotIn('default_code', partner_rule.matchable_field_ids.mapped('name'))
        self.assertIn('default_code', product_rule.matchable_field_ids.mapped('name'))
