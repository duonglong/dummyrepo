from odoo import Command
from odoo.tests.common import TransactionCase

from ..models.duplicate_matcher import normalize


class TestMatching(TransactionCase):
    """Tests for BR-DRD-0001, BR-DRD-0002, BR-DRD-0003, BR-DRD-0004."""

    def _rule(self, conditions, name='T002 match rule'):
        return self.env['duplicate.rule'].create({
            'name': name,
            'model_id': self.env['ir.model']._get('res.partner').id,
            'condition_ids': [Command.create({
                'field_id': self.env['ir.model.fields']._get('res.partner', f).id,
                'match_type': m}) for f, m in conditions],
        })

    def _matches(self, rule, record):
        return self.env['duplicate.matcher'].find_matches(record, rule)

    def test_normalize(self):
        """Requirements: BR-DRD-0001. Case and outer spaces are ignored, empty stays empty."""
        self.assertEqual(normalize('  ACME Ltd '), 'acme ltd')
        self.assertEqual(normalize(False), '')
        self.assertEqual(normalize('   '), '')

    def test_bug_b_0136_identical_values_found_by_prefilter(self):
        """Regression test for B-0136: the SQL prefilter keeps what the Python comparison accepts.

        Requirements: BR-DRD-0001, FR-DRD-0003, FR-DRD-0004
        """
        rule = self._rule([('name', 'exact')])
        P = self.env['res.partner']
        for stored, saved in (('Straße GmbH T136', 'Straße GmbH T136'),
                              ('acme t136\t', 'ACME T136'),
                              ('acme nbsp t136\u00a0', 'ACME NBSP T136')):
            with self.subTest(stored=stored):
                existing = P.create({'name': stored})
                new = P.create({'name': saved})
                self.assertEqual(self._matches(rule, new), existing)

    def test_partial_length_boundary(self):
        """Requirements: BR-DRD-0002. 2 characters never match partly, 3 do."""
        rule = self._rule([('name', 'partial')])
        P = self.env['res.partner']
        long_one = P.create({'name': 'Quuxlab Trading'})
        two = P.create({'name': 'Qu'})
        three = P.create({'name': 'Quu'})
        self.assertFalse(self._matches(rule, two))
        self.assertEqual(self._matches(rule, three), long_one)

    def test_inactive_rule_matches_nothing(self):
        """Requirements: BR-DRD-0003. A switched-off rule finds nothing."""
        rule = self._rule([('name', 'exact')])
        rule.active = False
        a = self.env['res.partner'].create({'name': 'Offline Co T002'})
        self.env['res.partner'].create({'name': 'offline co t002'})
        self.assertFalse(self._matches(rule, a))

    def test_rule_without_condition_matches_nothing(self):
        """Requirements: BR-DRD-0003. A rule left without condition (field uninstalled) is skipped."""
        rule = self._rule([('name', 'exact')])
        self.env.cr.execute("DELETE FROM duplicate_rule_condition WHERE rule_id = %s", [rule.id])
        rule.invalidate_recordset()
        a = self.env['res.partner'].create({'name': 'Condless T002'})
        self.env['res.partner'].create({'name': 'Condless T002'})
        self.assertFalse(self._matches(rule, a))
