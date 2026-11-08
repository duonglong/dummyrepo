import time
from datetime import timedelta
from unittest.mock import patch

from odoo import Command, fields
from odoo.tests.common import TransactionCase

from odoo.addons.new_duplicate_record_detector.models import duplicate_rule as duplicate_rule_module
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


    def test_bug_b_0147_company_and_its_own_contact_are_not_a_set(self):
        """Regression test for B-0147: a company and its own contact share a VAT yet make no set.

        Requirements: FR-DRD-0005, BR-DRD-0004
        """
        company = self.P.create({'name': 'Acme B0147', 'is_company': True, 'vat': 'BE0123456749'})
        contact = self.P.create({'name': 'John B0147', 'parent_id': company.id, 'vat': 'BE0123456749'})
        sibling = self.P.create({'name': 'Jane B0147', 'parent_id': company.id, 'vat': 'BE0123456749'})
        rule = self._rule([('vat', 'exact')])
        self.check.replace_open_sets(rule)
        sets = [set(s.line_ids.mapped('res_id')) for s in self._sets(rule)]
        self.assertEqual(sets, [{contact.id, sibling.id}])


class TestFullCheckJob(TransactionCase):
    """The background job runs one rule at a time and tells the asker how it ended.

    Requirements: FR-DRD-0005, NFR-DRD-0002
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Rule = cls.env['duplicate.rule']

    def _queued_rule(self, name):
        rule = self.Rule.create({
            'name': name, 'model_id': self.env['ir.model']._get('res.partner').id,
            'condition_ids': [Command.create({
                'field_id': self.env['ir.model.fields']._get('res.partner', 'email').id,
                'match_type': 'exact'})]})
        rule.write({'check_state': 'queued', 'check_user_id': self.env.user.id})
        return rule

    def _messages_since(self, mark):
        self.env.cr.precommit.run()
        return self.env['bus.bus'].sudo().search([('id', '>', mark)]).mapped('message')

    def _last_bus_id(self):
        return self.env['bus.bus'].sudo().search([], order='id desc', limit=1).id or 0

    def test_bug_b_0145_each_rule_is_committed_running_then_with_its_result(self):
        """Regression test for B-0145: a rule is committed as running before it runs, then with its result.

        Requirements: FR-DRD-0005, NFR-DRD-0002
        """
        first, second = self._queued_rule('Job one'), self._queued_rule('Job two')
        self.env.flush_all()
        committed = []

        def record_states():
            self.env.flush_all()
            self.env.cr.execute(
                "SELECT check_state FROM duplicate_rule WHERE id IN %s ORDER BY id",
                [(first.id, second.id)])
            committed.append(tuple(state for (state,) in self.env.cr.fetchall()))

        with patch.object(self.env.cr, 'commit', side_effect=record_states), \
                patch.object(duplicate_rule_module.odoo_module, 'current_test', None):
            self.Rule._cron_run_full_checks()
        self.assertEqual(committed, [
            ('running', 'queued'), ('done', 'queued'), ('done', 'running'), ('done', 'done')])

    def test_bug_b_0145_rule_running_for_over_an_hour_is_reset_to_failed(self):
        """Regression test for B-0145: a rule left running by a dead worker is failed, not stuck.

        Requirements: FR-DRD-0005, NFR-DRD-0002
        """
        stuck, busy = self._queued_rule('Stuck'), self._queued_rule('Busy')
        (stuck | busy).write({'check_state': 'running'})
        self.env.flush_all()
        self.env.cr.execute(
            "UPDATE duplicate_rule SET write_date = %s WHERE id = %s",
            [fields.Datetime.now() - timedelta(hours=2), stuck.id])
        self.env.cr.execute(
            "UPDATE duplicate_rule SET write_date = %s WHERE id = %s",
            [fields.Datetime.now() - timedelta(minutes=30), busy.id])
        self.env.invalidate_all()
        mark = self._last_bus_id()
        self.Rule._cron_run_full_checks()
        self.assertEqual(stuck.check_state, 'failed')
        self.assertTrue(stuck.last_check_error)
        self.assertEqual(busy.check_state, 'running')
        self.assertTrue(any('danger' in m and 'Stuck' in m for m in self._messages_since(mark)))

    def test_bug_b_0145_failed_check_notifies_the_asker_with_the_reason(self):
        """Regression test for B-0145: a failing check sends a danger notice naming the reason.

        Requirements: FR-DRD-0005
        """
        rule = self._queued_rule('Failing job')
        mark = self._last_bus_id()
        with patch.object(
                type(self.env['duplicate.full.check']), 'replace_open_sets',
                side_effect=ValueError('matcher broke')):
            self.Rule._cron_run_full_checks()
        self.assertEqual(rule.check_state, 'failed')
        self.assertTrue(any(
            'danger' in m and 'matcher broke' in m and 'Failing job' in m
            for m in self._messages_since(mark)))
