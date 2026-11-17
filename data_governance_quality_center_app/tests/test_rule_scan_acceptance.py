from .acceptance_helpers import AcceptanceCase


class TestRuleScanAcceptance(AcceptanceCase):
    """Scan parts of the rule scenarios, split out of test_rule_acceptance.py.

    They need the scan results (T-010); the rule model of T-005 cannot run a scan.
    Requirements: FR-DGQ-0001, FR-DGQ-0010, FR-DGQ-0011, FR-DGQ-0014
    """

    def test_new_rule_is_checked_by_the_next_scan(self):
        """SC-DGQ-0001-1 (scan part): the next scan of Contact checks the saved rule."""
        rule = self.make_rule('res.partner', 'required', ['vat'], severity='warning')
        acme = self.partner('Acme DGQ')
        scan = self.run_scan(models=['res.partner'])
        self.assertIn(acme.id, self.failing(scan, rule))

    def test_switch_on_checks_the_rule_again(self):
        """SC-DGQ-0010-2: a rule switched on again is checked by the next scan."""
        rule = self.make_rule('res.partner', 'required', ['vat'], active=False)
        acme = self.partner('Acme DGQ')
        off = self.run_scan(models=['res.partner'])
        self.assertFalse(self.failing(off, rule))
        rule.active = True
        on = self.run_scan(models=['res.partner'])
        self.assertIn(acme.id, self.failing(on, rule))

    def test_scan_records_the_new_version_on_its_results(self):
        """SC-DGQ-0011-1 (scan part): after raising the maximum, the next scan records version 2."""
        rule = self.make_rule(
            'product.template', 'range', ['x_dgq_cost'], name='Cost 0 to 10000',
            has_min=True, min_value=0, has_max=True, max_value=10000)
        rule.max_value = 20000
        self.assertEqual(rule.version, 2)
        pricey = self.product('Pricey', x_dgq_cost=25000)
        scan = self.run_scan(models=['product.template'])
        self.assertEqual(self.failing(scan, rule)[pricey.id].rule_version, 2)

    def test_rule_of_one_company_checks_that_company_only(self):
        """SC-DGQ-0014-1: a rule for company X leaves the records of company Y out."""
        rule = self.make_rule('res.partner', 'required', ['ref'], company=self.cx)
        acme = self.partner('Acme', self.cx)
        beta = self.partner('Beta', self.cy)
        scan = self.run_scan(companies=[self.cx, self.cy], models=['res.partner'])
        failed = self.failing(scan, rule)
        self.assertIn(acme.id, failed)
        self.assertNotIn(beta.id, failed)

    def test_rule_without_company_checks_every_company_of_the_scan(self):
        """SC-DGQ-0014-2: a rule with no company checks the records of both companies."""
        rule = self.make_rule('res.partner', 'required', ['ref'], company=False)
        acme = self.partner('Acme', self.cx)
        beta = self.partner('Beta', self.cy)
        scan = self.run_scan(companies=[self.cx, self.cy], models=['res.partner'])
        failed = self.failing(scan, rule)
        self.assertIn(acme.id, failed)
        self.assertIn(beta.id, failed)
