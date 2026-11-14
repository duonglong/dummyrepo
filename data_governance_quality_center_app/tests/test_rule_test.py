from odoo import Command
from odoo.tests import Form

from .acceptance_helpers import AcceptanceCase


class TestRuleTest(AcceptanceCase):
    """Own tests of the rule test and of the rendered explanation.

    Requirements: FR-DGQ-0012, FR-DGQ-0013
    """

    def _form(self, user=None):
        Rule = self.env['data.quality.rule']
        return Form(Rule.with_user(user) if user else Rule)

    def test_form_runs_the_test_on_the_unsaved_rule(self):
        """FR-DGQ-0012: a manager fills the form and picks records; nothing is saved.

        The form helper sets the fields as the screen does; the record picker widget and the
        page are left to the screen review.
        """
        acme = self.partner('Acme')
        beta = self.partner('Beta', email='beta@example.com')
        before = self.env['data.quality.rule'].search_count([])
        form = self._form()
        form.name = 'Unsaved'
        form.model_id = self.model('res.partner')
        form.rule_type = 'required'
        form.field_ids.add(self.fld('res.partner', 'email'))
        form.test_res_ids = [acme.id, beta.id]
        report = str(form.test_report)
        self.assertRegex(report, r'Acme.*Fail')
        self.assertRegex(report, r'Beta.*Pass')
        self.assertEqual(self.env['data.quality.rule'].search_count([]), before)

    def test_incomplete_rule_gives_a_reason_and_no_record(self):
        """FR-DGQ-0012: a pattern rule without a pattern is not tested; the reason is shown."""
        rule = self.env['data.quality.rule'].new({
            'model_id': self.model('res.partner').id, 'rule_type': 'pattern', 'name': 'Half',
            'field_ids': [Command.set([self.fld('res.partner', 'email').id])],
            'test_res_ids': [self.partner('Acme').id]})
        results = rule.test_results
        self.assertTrue(results['not_run_reason'])
        self.assertFalse(results['records'])

    def test_only_readable_records_are_tested(self):
        """FR-DGQ-0012: a record outside the user's companies is not offered."""
        mine = self.partner('Mine')
        foreign = self.partner('Foreign', company=self.cy)
        user = self.user('manager', company=self.cx)
        rule = self.env['data.quality.rule'].with_user(user).new({
            'model_id': self.model('res.partner').id, 'rule_type': 'required', 'name': 'R',
            'field_ids': [Command.set([self.fld('res.partner', 'email').id])],
            'test_res_ids': [mine.id, foreign.id]})
        self.assertEqual([entry['res_id'] for entry in rule.test_results['records']], [mine.id])

    def test_failed_entry_carries_the_advice_below_the_explanation(self):
        """FR-DGQ-0013: the rule test shows the advice after the explanation."""
        rule = self.env['data.quality.rule'].new({
            'model_id': self.model('res.partner').id, 'rule_type': 'required', 'name': 'R',
            'advice': 'Ask sales', 'field_ids': [Command.set([self.fld('res.partner', 'email').id])],
            'test_res_ids': [self.partner('Acme').id]})
        text = rule.test_results['records'][0]['explanation']
        self.assertTrue(text.endswith('\nAsk sales'), text)

    def test_report_lists_every_tested_record(self):
        """FR-DGQ-0012: the report names each record and its result."""
        rule = self.env['data.quality.rule'].new({
            'model_id': self.model('res.partner').id, 'rule_type': 'required', 'name': 'R',
            'field_ids': [Command.set([self.fld('res.partner', 'email').id])],
            'test_res_ids': [self.partner('Acme').id]})
        self.assertIn('Acme', rule.test_report)
        self.assertIn('Fail', rule.test_report)
