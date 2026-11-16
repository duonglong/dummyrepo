from odoo import Command

from .acceptance_helpers import MODULE, AcceptanceCase, dump


def entries(results):
    """The per-record entries of a rule test result, whichever list or mapping holds them."""
    if isinstance(results, list):
        return [item for item in results if isinstance(item, dict)]
    if isinstance(results, dict):
        for value in results.values():
            if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
                return value
        return [item for item in results.values() if isinstance(item, dict)]
    return []


def entry_of(results, res_id):
    """The entry of one record: the one that carries its id, as a value or as the key."""
    if isinstance(results, dict) and str(res_id) in results and isinstance(results[str(res_id)], dict):
        return results[str(res_id)]
    for item in entries(results):
        if res_id in item.values():
            return item
    raise AssertionError("No entry for record %s in %r" % (res_id, results))


def words(item):
    return dump(item).replace('_', ' ')


class TestRuleTestSampleAcceptance(AcceptanceCase):
    """Acceptance tests of T-008 (the issue and its language, shown from the scan result here): test a rule on sample records, explain why a record failed.

    The test of an unsaved rule is the computed ``test_results`` of the rule shown in the form
    (design: not stored, computed on the unsaved form) with the picked ``test_res_ids``; the
    form button and the record picker are left to the build. The shape of the result is not
    fixed by the design: it is read for the record's id and for the words pass, fail and not
    applicable.

    Requirements: FR-DGQ-0012, FR-DGQ-0013
    """

    def _unsaved_required_rule(self, field_names, picked=()):
        Rule = self.env['data.quality.rule']
        return Rule.new({
            'name': 'Unsaved',
            'model_id': self.model('res.partner').id,
            'rule_type': 'required',
            'field_ids': [Command.set([self.fld('res.partner', n).id for n in field_names])],
            'severity': 'warning',
            'test_res_ids': list(picked),
        })

    # ---- FR-DGQ-0012 -------------------------------------------------------

    def test_unsaved_rule_on_picked_records(self):
        """SC-DGQ-0012-1: Acme (no e-mail) fails with the field named, Beta passes; nothing is created."""
        acme = self.partner('Acme')
        beta = self.partner('Beta', email='beta@example.com')
        # The issue model belongs to a later task: it is counted once it exists.
        names = [name for name in ('data.quality.issue', 'data.quality.scan', 'data.quality.scan.line',
                                   'data.quality.rule') if name in self.env]
        self.assertIn('data.quality.scan', names)
        before = {name: self.env[name].with_context(active_test=False).search_count([]) for name in names}
        rule = self._unsaved_required_rule(['email'], [acme.id, beta.id])
        results = rule.test_results
        acme_text = words(entry_of(results, acme.id))
        self.assertIn('fail', acme_text)
        self.assertTrue('e-mail' in acme_text or 'email' in acme_text, acme_text)
        beta_text = words(entry_of(results, beta.id))
        self.assertIn('pass', beta_text)
        self.assertNotIn('fail', beta_text)
        after = {name: self.env[name].with_context(active_test=False).search_count([]) for name in names}
        self.assertEqual(before, after)

    def test_rule_test_uses_the_definition_on_screen(self):
        """FR-DGQ-0012: the saved rule says 'reference', the form says 'e-mail': the form is tested."""
        saved = self.make_rule('res.partner', 'required', ['ref'])
        acme = self.partner('Acme', ref='A-1')  # has a reference, no e-mail
        screen = self.env['data.quality.rule'].new({
            'field_ids': [Command.set([self.fld('res.partner', 'email').id])],
            'test_res_ids': [acme.id],
        }, origin=saved)
        self.assertIn('fail', words(entry_of(screen.test_results, acme.id)))

    def test_rule_test_shows_not_applicable(self):
        """FR-DGQ-0012: a record outside the 'applies to' filter is shown as not applicable.

        Odoo 20 computes 'Is a company' from the tax ID, so the policy checks the e-mail of the
        partners that have a website: 'Company' (website, no e-mail) fails, 'Person' (no website)
        is not applicable.
        """
        company = self.partner('Company', website='https://company.example.com')
        person = self.partner('Person')
        rule = self.env['data.quality.rule'].new({
            'name': 'Unsaved', 'model_id': self.model('res.partner').id, 'rule_type': 'condition',
            'severity': 'warning', 'applies_domain': "[('website', '!=', False)]",
            'condition_domain': "[('email', '!=', False)]",
            'test_res_ids': [company.id, person.id],
        })
        results = rule.test_results
        self.assertIn('fail', words(entry_of(results, company.id)))
        self.assertIn('not applicable', words(entry_of(results, person.id)))

    def test_rule_test_without_pick_takes_twenty_records(self):
        """SC-DGQ-0012-2: on a model with 500 records, the test without a pick shows 20 records."""
        self.env['res.partner'].create([
            {'name': 'Sample %s' % n, 'company_id': self.cx.id} for n in range(500)])
        rule = self._unsaved_required_rule(['email'])
        self.assertEqual(len(entries(rule.test_results)), 20)

    # ---- FR-DGQ-0013 -------------------------------------------------------

    def test_explanation_names_value_expectation_and_advice(self):
        """SC-DGQ-0013-1: 'Pen' at cost -5 is explained, with the manager's advice below the explanation."""
        rule = self.make_rule(
            'product.template', 'range', ['x_dgq_cost'], has_min=True, min_value=0, has_max=True,
            max_value=10000, advice='Check the vendor price list')
        pen = self.product('Pen', x_dgq_cost=-5)
        scan = self.run_scan([rule], models=['product.template'])
        line = self.failing(scan, rule)[pen.id]
        explanation = self.text(line)
        self.assertIn('-5', explanation)
        self.assertIn('minimum', explanation.lower())
        self.assertIn('Check the vendor price list', explanation)
        self.assertLess(explanation.lower().index('minimum'),
                        explanation.index('Check the vendor price list'))
        self.assertTrue(line.sudo().explanation_code)
        self.assertTrue(line.sudo().explanation_params)

    def test_explanation_follows_the_language_of_the_reader(self):
        """SC-DGQ-0013-2: with the French translation installed, a French reader gets a French text."""
        self.env['res.lang']._activate_lang('fr_FR')
        self.env['ir.module.module']._load_module_terms([MODULE], ['fr_FR'], overwrite=True)
        rule = self.make_rule('product.template', 'required', ['x_dgq_barcode'])
        desk = self.product('Desk')
        scan = self.run_scan([rule], models=['product.template'])
        line = self.failing(scan, rule)[desk.id]
        english = line.sudo().with_context(lang='en_US').explanation
        french = line.sudo().with_context(lang='fr_FR').explanation
        self.assertTrue(english)
        self.assertTrue(french)
        self.assertNotEqual(english, french)
