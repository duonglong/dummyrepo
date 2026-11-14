from .acceptance_m3_helpers import M3Case


class TestScoreSnapshotAcceptance(M3Case):
    """Acceptance tests of T-014: the dated score snapshot a finished scan stores.

    The dashboard figures, the company and date filters (FR-DGQ-0032, FR-DGQ-0033) are read through
    a service whose method and answer shape the design does not name; they are reported as unclear
    and have no test here. What is tested is what the dashboard reads: the snapshot rows.

    Requirements: BR-DGQ-0001, BR-DGQ-0002, FR-DGQ-0031
    """

    def snapshot(self, scan, rule=None, model_name='res.partner', company=None):
        return self.env['data.quality.score.snapshot'].sudo().search([
            ('scan_id', '=', scan.id), ('model_id', '=', self.model(model_name).id),
            ('company_id', '=', (company or self.cx).id), ('rule_id', '=', rule.id if rule else False)])

    def contacts(self, count):
        self.isolate('res.partner')
        return self.env['res.partner'].create([
            {'name': 'Score %s' % n, 'ref': 'R%s' % n, 'function': 'F%s' % n, 'website': 'w%s.example.com' % n,
             'company_id': self.cx.id} for n in range(count)])

    def two_rules(self):
        critical = self.make_rule('res.partner', 'required', ['ref'], severity='critical')
        warning = self.make_rule('res.partner', 'required', ['function'], severity='warning')
        return critical, warning

    def test_snapshot_of_the_scenario_weights_and_score(self):
        """SC-DGQ-0031-1: applicable weight 130, failed weight 16 and score 87.7 for Contact of company A."""
        critical, warning = self.two_rules()
        people = self.contacts(10)
        people[0].ref = False  # fails the critical rule
        people[1].function = False  # and two others fail the warning rule
        people[2].function = False
        scan = self.run_scan([critical, warning], models=['res.partner'])
        self.assertEqual(scan.state, 'done')
        snap = self.snapshot(scan)
        self.assertEqual(len(snap), 1, 'one snapshot of the model for the company and the scan')
        self.assertEqual(snap.applicable_weight, 130)
        self.assertEqual(snap.failed_weight, 16)
        self.assertTrue(snap.has_score)
        self.assertEqual(round(snap.score, 1), 87.7)
        self.assertEqual(snap.records_scanned, 10)
        self.assertEqual(snap.records_failing, 3)
        self.assertEqual(snap.records_error, 1)
        self.assertEqual(snap.records_warning, 2)
        self.assertTrue(snap.date)

    def test_snapshot_of_each_rule(self):
        """FR-DGQ-0031: each rule of the model has its own snapshot with its weights."""
        critical, warning = self.two_rules()
        people = self.contacts(10)
        people[0].ref = False
        people[1].function = False
        people[2].function = False
        scan = self.run_scan([critical, warning], models=['res.partner'])
        snap_critical = self.snapshot(scan, critical)
        snap_warning = self.snapshot(scan, warning)
        self.assertEqual((snap_critical.applicable_weight, snap_critical.failed_weight), (100, 10))
        self.assertEqual(round(snap_critical.score, 1), 90.0)
        self.assertEqual((snap_warning.applicable_weight, snap_warning.failed_weight), (30, 6))
        self.assertEqual(round(snap_warning.score, 1), 80.0)
        self.assertEqual(snap_critical.scan_id, scan)

    def test_no_applicable_rule_shows_no_score(self):
        """SC-DGQ-0031-2: where no rule applies to any record the snapshot has no score, not 100."""
        rule = self.make_rule(
            'res.partner', 'condition', applies_domain="[('name', '=', 'Nobody has this name')]",
            condition_domain="[('ref', '!=', False)]")
        self.contacts(5)
        scan = self.run_scan([rule], models=['res.partner'])
        snap = self.snapshot(scan)
        self.assertEqual(len(snap), 1, 'the model is covered, so it has a snapshot')
        self.assertEqual(snap.applicable_weight, 0)
        self.assertFalse(snap.has_score)
        self.assertNotEqual(snap.score, 100.0)

    def test_a_record_failing_three_fields_adds_the_weight_once(self):
        """SC-DGQ-0031-3: a required rule on three fields and a contact missing all three: failed weight 3, once."""
        rule = self.make_rule('res.partner', 'required', ['ref', 'function', 'website'], severity='warning')
        self.isolate('res.partner')
        self.env['res.partner'].create({'name': 'Empty one', 'company_id': self.cx.id})
        scan = self.run_scan([rule], models=['res.partner'])
        snap = self.snapshot(scan)
        self.assertEqual(snap.failed_weight, 3)
        self.assertEqual(snap.applicable_weight, 3)
        self.assertEqual(snap.records_failing, 1)

    def test_inapplicable_records_stay_out_of_both_weights(self):
        """BR-DGQ-0002: a rule that does not apply to a record adds nothing to either weight."""
        rule = self.make_rule(
            'res.partner', 'condition', applies_domain="[('function', '=', 'DGQVEND')]",
            condition_domain="[('ref', '!=', False)]", severity='warning')
        self.isolate('res.partner')
        self.env['res.partner'].create([
            {'name': 'Vendor A', 'function': 'DGQVEND', 'company_id': self.cx.id},
            {'name': 'Vendor B', 'function': 'DGQVEND', 'ref': 'ok', 'company_id': self.cx.id},
            {'name': 'Customer', 'function': 'Buyer', 'company_id': self.cx.id}])
        scan = self.run_scan([rule], models=['res.partner'])
        snap = self.snapshot(scan)
        self.assertEqual(snap.applicable_weight, 6)  # two vendors of weight 3, the customer is left out
        self.assertEqual(snap.failed_weight, 3)
        self.assertEqual(round(snap.score, 1), 50.0)
