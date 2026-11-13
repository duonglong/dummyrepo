from odoo.exceptions import UserError, ValidationError

from .acceptance_helpers import AcceptanceCase


class TestRule(AcceptanceCase):
    """Unit tests of the rule model beyond the acceptance tests.

    Requirements: FR-DGQ-0001, FR-DGQ-0011
    """

    def test_copy_starts_again_at_version_one(self):
        """FR-DGQ-0011: a copy is named '(copy)', at version 1, without versions."""
        rule = self.make_rule('res.partner', 'required', ['vat'], name='Vendor')
        rule.severity = 'critical'
        copy = rule.copy()
        self.assertEqual(copy.name, 'Vendor (copy)')
        self.assertEqual(copy.version, 1)
        self.assertFalse(copy.version_ids)

    def test_same_value_write_makes_no_version(self):
        """FR-DGQ-0011: writing the value a field already has is not a change."""
        rule = self.make_rule('res.partner', 'required', ['vat'])
        rule.severity = 'warning'
        self.assertEqual(rule.version, 1)

    def test_domain_text_is_checked_not_run(self):
        """FR-DGQ-0001: a filter that is not a domain is refused."""
        with self.assertRaises(ValidationError):
            self.make_rule('res.partner', 'condition', condition_domain="[('x_nope', '=', 1)]")

    def test_pattern_must_compile(self):
        """FR-DGQ-0001: a pattern that is not a regular expression is refused."""
        with self.assertRaises(ValidationError):
            self.make_rule('res.partner', 'pattern', ['vat'], pattern='([a-z')

    def test_version_cannot_be_deleted(self):
        """FR-DGQ-0011: a kept version is read-only."""
        rule = self.make_rule('res.partner', 'required', ['vat'])
        rule.severity = 'info'
        with self.assertRaises(UserError):
            rule.version_ids.unlink()

    def test_copy_of_several_rules(self):
        """FR-DGQ-0011: duplicating several rules at once names each copy after its own rule."""
        first = self.make_rule('res.partner', 'required', ['vat'], name='First')
        second = self.make_rule('res.partner', 'required', ['vat'], name='Second')
        copies = (first | second).copy()
        self.assertEqual(copies.mapped('name'), ['First (copy)', 'Second (copy)'])

    def _mark_comment_sensitive(self):
        self.env['data.quality.field.config'].create({
            'field_id': self.fld('res.partner', 'comment').id,
            'status': 'sensitive',
        })

    def test_condition_filter_cannot_read_a_sensitive_field(self):
        """BR-DGQ-0010: a filter on a sensitive field would make pass/fail an oracle on its value."""
        self._mark_comment_sensitive()
        with self.assertRaises(ValidationError):
            self.make_rule('res.partner', 'condition', condition_domain="[('comment', 'ilike', 'x')]")

    def test_compare_field_cannot_be_a_sensitive_field(self):
        """BR-DGQ-0010: the field compared with is held to the same marks as the checked fields."""
        self._mark_comment_sensitive()
        with self.assertRaises(ValidationError):
            self.make_rule(
                'res.partner', 'cross_field', ['name'],
                compare_field_id=self.fld('res.partner', 'comment').id, compare_operator='=')

    def test_version_cannot_be_set_by_hand(self):
        """FR-DGQ-0011: the version number only moves with a change of the definition."""
        rule = self.make_rule('res.partner', 'required', ['vat'])
        with self.assertRaises(UserError):
            rule.write({'version': 5})

    def test_domain_code_is_refused_not_run(self):
        """BR-DGQ-0009: an expression in a filter is refused without being evaluated."""
        with self.assertRaises(ValidationError):
            self.make_rule('res.partner', 'condition', condition_domain="__import__('os').getcwd()")
