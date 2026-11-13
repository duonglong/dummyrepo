from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import Form

from .acceptance_helpers import AcceptanceCase


class TestScanStart(AcceptanceCase):
    """Scope resolution, scan form and rules-not-run text of a scan.

    Form fills the fields and saves; it presses no button, so the Start button stays for the screen review.

    Requirements: FR-DGQ-0015, FR-DGQ-0018, FR-DGQ-0020
    """

    def test_start_stores_the_resolved_scope(self):
        """FR-DGQ-0015: an empty scope is stored as the real rules, models and companies."""
        rule = self.make_rule('res.partner', 'required', ['ref'])
        scan = self.make_scan([], companies=[self.cx])
        scan.action_start()
        self.assertIn(rule, scan.rule_ids)
        self.assertIn(self.model('res.partner'), scan.model_ids)
        self.assertEqual(scan.company_ids, self.cx)

    def test_scan_without_any_rule_is_refused(self):
        """FR-DGQ-0015: nothing to scan is refused instead of queued."""
        scan = self.make_scan([], companies=[self.cx], models=['res.partner'])
        scan.rule_ids = False
        self.env['data.quality.rule'].search([]).write({'active': False})
        with self.assertRaisesRegex(UserError, "no active rule to scan"):
            scan.action_start()

    def test_scan_form_is_saved_as_a_draft_with_a_reference(self):
        """FR-DGQ-0015: the operator fills the scan form; saving gives a draft with its reference."""
        rule = self.make_rule('res.partner', 'required', ['ref'])
        operator = self.user('operator')
        form = Form(self.env['data.quality.scan'].with_user(operator))
        form.rule_ids.add(rule)
        scan = form.save()
        self.assertEqual(scan.state, 'draft')
        self.assertTrue(scan.name.startswith('SCAN/'))

    def test_rules_not_run_text_names_the_rule_and_the_reason(self):
        """FR-DGQ-0020: the scan form's box lists each rule not run with its reason."""
        rule = self.make_rule('res.partner', 'required', ['x_dgq_born_on'])
        self.env['data.quality.field.config'].create(
            {'field_id': self.fld('res.partner', 'x_dgq_born_on').id, 'status': 'excluded'})
        scan = self.run_scan([rule], models=['res.partner'])
        self.assertIn(rule.name, scan.rules_not_run_text)
        self.assertIn('excluded', scan.rules_not_run_text)

    def test_required_check_treats_zero_number_as_a_value(self):
        """FR-DGQ-0015: a required number holding 0 is filled."""
        rule = self.make_rule('product.template', 'required', ['x_dgq_cost'])
        self.product('Free', x_dgq_cost=0)
        scan = self.run_scan([rule], models=['product.template'])
        self.assertFalse(self.failing(scan, rule))

    def test_required_check_treats_blank_text_as_empty(self):
        """FR-DGQ-0015: a required text of spaces is empty."""
        blank = self.make_rule('res.partner', 'required', ['ref'])
        self.partner('Blank', ref='   ')
        scan = self.run_scan([blank], models=['res.partner'])
        self.assertTrue(self.failing(scan, blank))

    def test_filter_field_marked_excluded_later_stops_the_rule(self):
        """FR-DGQ-0020: a rule whose Applies To filter reads a field excluded after the rule was saved is not run."""
        rule = self.make_rule(
            'res.partner', 'required', ['ref'], applies_domain="[('x_dgq_born_on', '!=', False)]")
        self.partner('Born', ref=False, x_dgq_born_on=self.day(-3000))
        self.env['data.quality.field.config'].create(
            {'field_id': self.fld('res.partner', 'x_dgq_born_on').id, 'status': 'excluded'})
        scan = self.run_scan([rule], models=['res.partner'])
        self.assertIn('excluded', scan.rules_not_run_text)
        self.assertFalse(self.failing(scan, rule))

    def test_overlap_refusal_names_rule_company_and_way_out(self):
        """FR-DGQ-0018: the refusal names the shared rule, company and running scan, and says what to do."""
        rule = self.make_rule('res.partner', 'required', ['ref'])
        first = self.make_scan([rule])
        first.action_start()
        with self.assertRaisesRegex(UserError, r"%s.*%s.*%s.*cancel it" % (
                rule.name, self.cx.name, first.name)):
            self.make_scan([rule]).action_start()

    def test_start_takes_the_scope_rules_before_the_overlap_search(self):
        """FR-DGQ-0018: the start updates the scope's rule rows first, so a concurrent start conflicts and is retried.

        One test transaction cannot hold two snapshots, so this checks the order of the statements.
        """
        rule = self.make_rule('res.partner', 'required', ['ref'])
        scan = self.make_scan([rule])
        statements = []
        original = self.env.cr.execute

        def spy(query, *args, **kwargs):
            statements.append(str(getattr(query, 'code', query)))
            return original(query, *args, **kwargs)

        with patch.object(type(self.env.cr), 'execute', autospec=True,
                          side_effect=lambda cr, *a, **k: spy(*a, **k)):
            scan.action_start()
        lock = next(i for i, text in enumerate(statements) if 'UPDATE data_quality_rule SET write_date' in text)
        search = next(i for i, text in enumerate(statements) if 'FROM "data_quality_scan"' in text and i > lock)
        self.assertLess(lock, search)

    def test_scans_are_separated_by_company(self):
        """FR-DGQ-0071: a viewer or operator of company Y does not see, nor cancel, a scan of company X."""
        rule = self.make_rule('res.partner', 'required', ['ref'])
        scan = self.make_scan([rule])
        scan.action_start()
        other = self.env['res.company'].create({'name': 'DGQ other company'})
        operator = self.user('operator', login='dgq_sep_operator', company=other)
        for user in (operator, self.user('viewer', login='dgq_sep_viewer', company=other)):
            self.assertFalse(self.env['data.quality.scan'].with_user(user).search([('id', '=', scan.id)]))
        with self.assertRaises(AccessError):
            scan.with_user(operator).action_cancel()
