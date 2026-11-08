import time
from unittest.mock import patch

from odoo import Command
from odoo.tests.common import TransactionCase

from odoo.addons.new_duplicate_record_detector.models.duplicate_matcher import DuplicateMatcher

PERFORMANCE_BOUND_SECONDS = 120


class TestFullCheck(TransactionCase):
    """Full check behaviour beyond the acceptance tests.

    Requirements: FR-DRD-0005, FR-DRD-0006, NFR-DRD-0002
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.P = cls.env['res.partner']
        cls.check = cls.env['duplicate.full.check']

    def _rule(self, conditions):
        return self.env['duplicate.rule'].create({
            'name': 'FC rule', 'model_id': self.env['ir.model']._get('res.partner').id,
            'condition_ids': [Command.create({
                'field_id': self.env['ir.model.fields']._get('res.partner', f).id,
                'match_type': m}) for f, m in conditions]})

    def _sets(self, rule):
        self.env.flush_all()
        return self.env['duplicate.group'].search([('rule_id', '=', rule.id), ('state', '=', 'open')])

    def test_failed_state_records_the_error(self):
        """Requirements: FR-DRD-0005. A matcher error closes the check as failed with its message."""
        self.P.create([{'name': 'F1', 'email': 'f@fail-fc.com'}, {'name': 'F2', 'email': 'f@fail-fc.com'}])
        rule = self._rule([('email', 'exact')])
        rule.check_state = 'queued'
        with patch.object(DuplicateMatcher, '_values_match', side_effect=ValueError('matcher broke')):
            rule._run_full_check()
        self.assertEqual(rule.check_state, 'failed')
        self.assertEqual(rule.last_check_error, 'matcher broke')

    def test_failed_check_keeps_earlier_open_sets(self):
        """Requirements: FR-DRD-0006. A failing run rolls back its replacement of the sets."""
        rule = self._rule([('email', 'exact')])
        self.P.create([{'name': 'K1', 'email': 'k@keep-fc.com'}, {'name': 'K2', 'email': 'k@keep-fc.com'}])
        before = self._sets(rule)
        self.assertEqual(len(before), 1)
        with patch.object(DuplicateMatcher, '_values_match', side_effect=ValueError('boom')):
            rule._run_full_check()
        self.assertEqual(self._sets(rule), before)

    def test_partial_condition_uses_contains_with_three_characters(self):
        """Requirements: FR-DRD-0005, BR-DRD-0002. 'Acme' and 'Acme Corp' match, 'Ac' never does."""
        a = self.P.create({'name': 'Acme'})
        b = self.P.create({'name': 'Acme Corp'})
        self.P.create([{'name': 'Ac'}, {'name': 'Ac Ltd'}, {'name': 'Zeta'}])
        rule = self._rule([('name', 'partial')])
        self.check.replace_open_sets(rule)
        sets = self._sets(rule)
        found = [set(s.line_ids.mapped('res_id')) for s in sets if {a.id, b.id} & set(s.line_ids.mapped('res_id'))]
        self.assertEqual(found, [{a.id, b.id}])

    def test_shared_record_joins_the_set_of_each_company(self):
        """Requirements: FR-DRD-0005, BR-DRD-0004. Two companies never share a set, a shared record joins both."""
        co_a = self.env['res.company'].create({'name': 'FC Co A'})
        co_b = self.env['res.company'].create({'name': 'FC Co B'})
        a = self.P.create({'name': 'Twin FC', 'company_id': co_a.id})
        b = self.P.create({'name': 'Twin FC', 'company_id': co_b.id})
        shared = self.P.create({'name': 'Twin FC'})
        rule = self._rule([('name', 'exact')])
        self.check.replace_open_sets(rule)
        members = {s.company_id: set(s.line_ids.mapped('res_id')) for s in self._sets(rule)}
        self.assertEqual(members, {co_a: {a.id, shared.id}, co_b: {b.id, shared.id}})

    def test_second_condition_must_match_too(self):
        """Requirements: FR-DRD-0005, BR-DRD-0003. Every condition must match."""
        self.P.create([
            {'name': 'Cond FC', 'email': 'c@cond-fc.com'},
            {'name': 'Cond FC', 'email': 'd@cond-fc.com'}])
        rule = self._rule([('name', 'exact'), ('email', 'exact')])
        self.assertEqual(self.check.replace_open_sets(rule), 0)

    def test_run_on_5000_contacts_is_fast_enough(self):
        """Requirements: NFR-DRD-0002. 5,000 contacts with 1,000 pairs finish under a generous bound."""
        self.P.create([{'name': f'Perf {i}', 'email': f'p{i % 4000}@perf-fc.com'} for i in range(5000)])
        rule = self._rule([('email', 'exact')])
        start = time.monotonic()
        count = self.check.replace_open_sets(rule)
        self.assertGreaterEqual(count, 1000)
        self.assertLess(time.monotonic() - start, PERFORMANCE_BOUND_SECONDS)

    def test_bug_b_0144_partial_check_compares_only_containers(self):
        """Regression test for B-0144: common 3-character pieces must not make every record a candidate.

        Requirements: NFR-DRD-0002, BR-DRD-0002
        """
        count = 1500
        self.P.create([{'name': f'Company {i} Ltd'} for i in range(count)])
        rule = self._rule([('name', 'partial')])
        start = time.monotonic()
        with patch.object(
                DuplicateMatcher, '_values_match', autospec=True,
                side_effect=DuplicateMatcher._values_match) as compared:
            found = self.check.replace_open_sets(rule)
        self.assertLess(time.monotonic() - start, PERFORMANCE_BOUND_SECONDS)
        self.assertEqual(found, 0)
        self.assertLess(compared.call_count, 10 * count)

    def test_bug_b_0144_partial_pairs_found_from_either_side(self):
        """Regression test for B-0144: with two partial conditions each side may be the container.

        Requirements: FR-DRD-0005, BR-DRD-0002, BR-DRD-0003
        """
        a = self.P.create({'name': 'Acme', 'city': 'Lyon Centre'})
        b = self.P.create({'name': 'Acme Trading', 'city': 'Lyon'})
        rule = self._rule([('name', 'partial'), ('city', 'partial')])
        self.assertEqual(self.check.replace_open_sets(rule), 1)
        self.assertEqual(set(self._sets(rule).line_ids.mapped('res_id')), {a.id, b.id})

    def test_bug_b_0144_equal_partial_values_form_one_pair(self):
        """Regression test for B-0144: equal values contain each other and still give one set.

        Requirements: FR-DRD-0005, BR-DRD-0005
        """
        a, b = self.P.create([{'name': 'Twin Partial'}, {'name': 'twin partial '}])
        rule = self._rule([('name', 'partial')])
        self.assertEqual(self.check.replace_open_sets(rule), 1)
        self.assertEqual(set(self._sets(rule).line_ids.mapped('res_id')), {a.id, b.id})
