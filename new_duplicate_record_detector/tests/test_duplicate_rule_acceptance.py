from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase


class TestDuplicateRuleAcceptance(TransactionCase):
    """Acceptance tests of T-001.

    Requirements: FR-DRD-0001, FR-DRD-0002, FR-DRD-0018.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.manager_group = cls.env['res.groups'].search(
            [('name', 'ilike', 'duplicate manager')], limit=1)
        group_user = cls.env.ref('base.group_user')
        cls.manager = cls.env['res.users'].create({
            'name': 'Dup Manager', 'login': 'dup_manager_t001',
            'group_ids': [Command.set(group_user.ids + cls.manager_group.ids)],
        })
        cls.sales_user = cls.env['res.users'].create({
            'name': 'Plain User', 'login': 'plain_user_t001',
            'group_ids': [Command.set(group_user.ids)],
        })

    def _field(self, model, name):
        return self.env['ir.model.fields']._get(model, name).id

    def _rule_vals(self, conditions, model='res.partner', **extra):
        vals = {
            'name': 'Rule',
            'model_id': self.env['ir.model']._get(model).id,
            'condition_ids': [Command.create({
                'field_id': self._field(model, fname),
                'match_type': match,
            }) for fname, match in conditions],
        }
        vals.update(extra)
        return vals

    def _create(self, *args, **kw):
        return self.env['duplicate.rule'].with_user(self.manager).create(
            self._rule_vals(*args, **kw))

    # FR-DRD-0001
    def test_sc_0001_1_manager_saves_exact_email_rule(self):
        """Requirements: FR-DRD-0001. Scenario SC-DRD-0001-1: rule stored and active."""
        self.assertTrue(self.manager_group, "a Duplicate Manager group must exist")
        rule = self._create([('email', 'exact')], name='Same e-mail').sudo()
        self.assertTrue(rule.active)
        self.assertEqual(rule.name, 'Same e-mail')
        self.assertEqual(rule.model_name, 'res.partner')
        self.assertEqual(len(rule.condition_ids), 1)
        self.assertEqual(rule.condition_ids.field_id.name, 'email')
        self.assertEqual(rule.condition_ids.match_type, 'exact')

    def test_sc_0001_2_several_conditions_keep_match_types(self):
        """Requirements: FR-DRD-0001. Scenario SC-DRD-0001-2: partial name + exact city."""
        rule = self._create([('name', 'partial'), ('city', 'exact')]).sudo()
        got = {c.field_id.name: c.match_type for c in rule.condition_ids}
        self.assertEqual(got, {'name': 'partial', 'city': 'exact'})

    def test_rule_for_products_is_accepted(self):
        """Requirements: FR-DRD-0001. A rule may watch product templates."""
        rule = self._create([('default_code', 'exact')], model='product.template').sudo()
        self.assertEqual(rule.model_name, 'product.template')

    def test_match_type_defaults_to_exact(self):
        """Requirements: FR-DRD-0001. A condition without match type is exact."""
        vals = self._rule_vals([])
        vals['condition_ids'] = [
            Command.create({'field_id': self._field('res.partner', 'email')})]
        rule = self.env['duplicate.rule'].with_user(self.manager).create(vals).sudo()
        self.assertEqual(rule.condition_ids.match_type, 'exact')

    def test_sc_0001_3_rule_can_be_switched_off_and_on(self):
        """Requirements: FR-DRD-0001. Scenario SC-DRD-0001-3: switched-off rule is kept."""
        rule = self._create([('email', 'exact')])
        rule.write({'active': False})
        self.assertFalse(rule.active)
        Rule = self.env['duplicate.rule'].sudo()
        self.assertEqual(
            Rule.with_context(active_test=False).search([('id', '=', rule.id)]), rule.sudo())
        self.assertFalse(Rule.search([('id', '=', rule.id)]))
        rule.write({'active': True})
        self.assertTrue(rule.active)

    # FR-DRD-0002
    def test_sc_0002_1_rule_without_condition_refused(self):
        """Requirements: FR-DRD-0002. Scenario SC-DRD-0002-1."""
        with self.assertRaisesRegex(ValidationError, r'(?i)at least one condition'):
            self._create([])

    def test_removing_last_condition_refused(self):
        """Requirements: FR-DRD-0002. A saved rule cannot lose its last condition."""
        rule = self._create([('email', 'exact')])
        with self.assertRaisesRegex(ValidationError, r'(?i)at least one condition'):
            rule.write({'condition_ids': [Command.delete(rule.condition_ids.id)]})

    def test_sc_0002_2_partial_on_many2one_refused(self):
        """Requirements: FR-DRD-0002. Scenario SC-DRD-0002-2."""
        with self.assertRaisesRegex(
                ValidationError, r'(?i)partial matching works on text fields only'):
            self._create([('country_id', 'partial')])

    def test_partial_on_selection_refused(self):
        """Requirements: FR-DRD-0002. Partial is for char/text only (selection)."""
        with self.assertRaisesRegex(ValidationError, r'(?i)partial'):
            self._create([('type', 'partial')])

    def test_exact_on_many2one_and_selection_accepted(self):
        """Requirements: FR-DRD-0002. Boundary: exact works on single link and choice fields."""
        rule = self._create([('country_id', 'exact'), ('type', 'exact')]).sudo()
        self.assertEqual(len(rule.condition_ids), 2)

    def test_partial_on_char_accepted(self):
        """Requirements: FR-DRD-0002. Boundary: partial on a char field is allowed."""
        rule = self._create([('city', 'partial')]).sudo()
        self.assertEqual(rule.condition_ids.match_type, 'partial')

    def test_non_stored_field_refused(self):
        """Requirements: FR-DRD-0002. A computed, non-stored field is not a matching field."""
        self.assertFalse(self.env['res.partner']._fields['display_name'].store)
        with self.assertRaises(ValidationError):
            self._create([('display_name', 'exact')])

    def test_list_of_linked_records_refused(self):
        """Requirements: FR-DRD-0002. A one2many field is not a matching field."""
        with self.assertRaises(ValidationError):
            self._create([('child_ids', 'exact')])

    def test_integer_field_refused(self):
        """Requirements: FR-DRD-0002. An integer field is not a matching field."""
        with self.assertRaises(ValidationError):
            self._create([('color', 'exact')])

    def test_other_record_type_refused(self):
        """Requirements: FR-DRD-0001. Only contacts or product templates can be watched."""
        with self.assertRaises(ValidationError):
            self._create([('login', 'exact')], model='res.users')

    # FR-DRD-0018
    def test_sc_0018_1_plain_user_cannot_use_rules(self):
        """Requirements: FR-DRD-0018. Scenario SC-DRD-0018-1: no access to rules."""
        Rule = self.env['duplicate.rule'].with_user(self.sales_user)
        with self.assertRaises(AccessError):
            Rule.search([])
        with self.assertRaises(AccessError):
            Rule.create(self._rule_vals([('email', 'exact')]))
        with self.assertRaises(AccessError):
            self.env['duplicate.rule.condition'].with_user(self.sales_user).search([])

    def test_manager_can_read_change_and_delete_rules(self):
        """Requirements: FR-DRD-0018. A duplicate manager has full access to rules."""
        rule = self._create([('email', 'exact')])
        self.assertEqual(
            self.env['duplicate.rule'].with_user(self.manager).search([('id', '=', rule.id)]),
            rule)
        rule.write({'name': 'Renamed'})
        rule.unlink()
        self.assertFalse(rule.exists())

    def test_administrator_is_a_duplicate_manager(self):
        """Requirements: FR-DRD-0018. Administrators need no extra setup."""
        admin = self.env.ref('base.user_admin')
        rule = self.env['duplicate.rule'].with_user(admin).create(
            self._rule_vals([('email', 'exact')]))
        self.assertTrue(rule.exists())
