from odoo.exceptions import UserError
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
        with self.assertRaises(UserError):
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

    def test_required_check_treats_false_boolean_and_zero_as_values(self):
        """FR-DGQ-0015: a required char is empty when blank; a boolean or a number never is."""
        rule = self.make_rule('product.template', 'required', ['x_dgq_cost'])
        self.product('Free', x_dgq_cost=0)
        scan = self.run_scan([rule], models=['product.template'])
        self.assertFalse(self.failing(scan, rule))
        blank = self.make_rule('res.partner', 'required', ['ref'])
        self.partner('Blank', ref='   ')
        scan = self.run_scan([blank], models=['res.partner'])
        self.assertTrue(self.failing(scan, blank))
