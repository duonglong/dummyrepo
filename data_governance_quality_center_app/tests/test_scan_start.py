from unittest.mock import patch

from odoo import Command
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

        Test cursors share one transaction, so two snapshots cannot be held: this checks that the
        rule update comes before the search of queued or running scans.
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
        search = next(i for i, text in enumerate(statements)
                      if 'FROM "data_quality_scan"' in text and '"data_quality_scan"."state" IN' in text)
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

    def test_two_drafts_of_the_same_scope_started_together_are_refused(self):
        """FR-DGQ-0018: two scans of one rule and company started in one call: refused, neither queued."""
        rule = self.make_rule('res.partner', 'required', ['ref'])
        first, second = self.make_scan([rule]), self.make_scan([rule])
        with self.assertRaisesRegex(UserError, r"%s.*%s.*%s.*one after the other" % (
                first.name, second.name, rule.name)):
            (first | second).action_start()
        self.assertEqual((first | second).mapped('state'), ['draft', 'draft'])

    def test_two_drafts_of_other_rules_started_together_are_queued(self):
        """FR-DGQ-0018: scans of different rules started in one call are both queued."""
        vat = self.make_rule('res.partner', 'required', ['vat'])
        ref = self.make_rule('res.partner', 'required', ['ref'])
        scans = self.make_scan([vat]) | self.make_scan([ref])
        scans.action_start()
        self.assertEqual(scans.mapped('state'), ['queued', 'queued'])

    def test_checkpoints_follow_the_company_of_their_scan(self):
        """FR-DGQ-0071: a viewer of company Y does not read the checkpoints of a scan of company X."""
        rule = self.make_rule('res.partner', 'required', ['ref'])
        self.partner('Acme', ref=False)
        scan = self.run_scan([rule], models=['res.partner'])
        self.assertTrue(scan.checkpoint_ids)
        Checkpoint = self.env['data.quality.scan.checkpoint']
        outsider = self.user('viewer', login='dgq_cp_outsider', company=self.cy)
        insider = self.user('viewer', login='dgq_cp_insider', company=self.cx)
        self.assertFalse(Checkpoint.with_user(outsider).search([('scan_id', '=', scan.id)]))
        self.assertTrue(Checkpoint.with_user(insider).search([('scan_id', '=', scan.id)]))
        with self.assertRaises(AccessError):
            scan.checkpoint_ids.with_user(outsider).read(['last_id'])

    def test_operator_cannot_change_the_scope_of_a_queued_scan(self):
        """FR-DGQ-0018, FR-DGQ-0020: a plain save of a queued scan's rules is refused."""
        operator = self.user('operator')
        vat = self.make_rule('res.partner', 'required', ['vat'])
        ref = self.make_rule('res.partner', 'required', ['ref'])
        scan = self.make_scan([vat], user=operator)
        scan.action_start()
        with self.assertRaisesRegex(UserError, "only be changed while it is a draft"):
            scan.write({'rule_ids': [Command.link(ref.id)]})
        self.assertEqual(scan.rule_ids, vat)

    def test_operator_cannot_set_the_state_or_counts_by_hand(self):
        """FR-DGQ-0070: state, counts and requester are written by the app, on create and on save."""
        operator = self.user('operator')
        Scan = self.env['data.quality.scan'].with_user(operator)
        for vals in ({'state': 'queued'}, {'records_scanned': 5}, {'user_id': operator.id}):
            with self.subTest(vals=vals), self.assertRaisesRegex(UserError, "set by the app"):
                Scan.create(vals)
        draft = Scan.create({})
        with self.assertRaisesRegex(UserError, "set by the app"):
            draft.write({'state': 'done'})
        self.assertEqual(draft.state, 'draft')

    def test_context_default_does_not_set_the_state(self):
        """FR-DGQ-0070: a default state passed in the context is ignored for a user."""
        operator = self.user('operator')
        scan = self.env['data.quality.scan'].with_user(operator).with_context(
            default_state='done', default_records_scanned=9).create({})
        self.assertEqual((scan.state, scan.records_scanned), ('draft', 0))

    def test_operator_still_cancels_his_queued_scan(self):
        """FR-DGQ-0015: Cancel works for the operator although he cannot write the state by hand."""
        operator = self.user('operator')
        rule = self.make_rule('res.partner', 'required', ['ref'])
        scan = self.make_scan([rule], user=operator)
        scan.action_start()
        scan.action_cancel()
        self.assertEqual(scan.state, 'cancelled')

    def test_overlap_counts_a_company_not_active_in_the_switcher(self):
        """FR-DGQ-0018: a company of the scan that is not active for the requester still blocks an overlap."""
        rule = self.make_rule('res.partner', 'required', ['ref'], company=False)
        self.make_scan([rule], companies=[self.cy]).action_start()
        operator = self.user('operator', login='dgq_ovl_switch', company=self.cx)
        operator.write({'company_ids': [Command.link(self.cy.id)]})
        scan = self.make_scan([rule], companies=[self.cx, self.cy], user=operator)
        with self.assertRaisesRegex(UserError, r"%s.*%s.*cancel it" % (rule.name, self.cy.name)):
            scan.with_context(allowed_company_ids=[self.cx.id]).action_start()
        self.assertEqual(scan.sudo().state, 'draft')  # sudo: the operator cannot read company Y's scope

    def test_overlap_counts_a_company_outside_the_requester_companies(self):
        """FR-DGQ-0018: an operator of company Y alone who scans busy company X is refused."""
        rule = self.make_rule('res.partner', 'required', ['ref'], company=False)
        self.make_scan([rule], companies=[self.cx]).action_start()
        operator = self.user('operator', login='dgq_ovl_outside', company=self.cy)
        operator.write({'company_ids': [Command.set([self.cy.id])]})
        scan = self.make_scan([rule], companies=[self.cx], user=operator)
        with self.assertRaisesRegex(UserError, r"%s.*%s.*cancel it" % (rule.name, self.cx.name)):
            scan.action_start()

    def test_two_drafts_on_a_company_not_active_are_refused(self):
        """FR-DGQ-0018: two drafts started together that share a company not active for the requester are refused."""
        rule = self.make_rule('res.partner', 'required', ['ref'], company=False)
        operator = self.user('operator', login='dgq_ovl_sibling', company=self.cx)
        operator.write({'company_ids': [Command.link(self.cy.id)]})
        first = self.make_scan([rule], companies=[self.cy], user=operator)
        second = self.make_scan([rule], companies=[self.cy], user=operator)
        with self.assertRaisesRegex(UserError, "one after the other"):
            (first | second).with_context(allowed_company_ids=[self.cx.id]).action_start()

    def test_hand_change_refusal_names_the_field_labels(self):
        """FR-DGQ-0070: the refusal of a hand change names the fields as the form shows them."""
        Scan = self.env['data.quality.scan'].with_user(self.user('operator'))
        with self.assertRaisesRegex(UserError, "Records Scanned") as caught:
            Scan.create({'records_scanned': 5})
        self.assertNotIn('records_scanned', str(caught.exception))
