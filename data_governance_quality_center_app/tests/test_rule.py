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
