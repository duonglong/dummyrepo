from .acceptance_m2b_helpers import M2BCase


class TestCorrectionScope(M2BCase):
    """Regression tests for B-0211: a correction's filter narrows the failing records of its rule.

    The dry run and the real run are reached through the buttons of the correction and the
    background runner, as the manager reaches them. The screens themselves are client side.

    Requirements: FR-DGQ-0046, FR-DGQ-0047, FR-DGQ-0049
    """

    def setUp(self):
        super().setUp()
        self.rule = self.make_rule(
            'res.partner', 'pattern', ['name'], name='Name has outer spaces', pattern=r'^\S(.*\S)?$')
        self.failing = self.partner(' padded ')
        self.passing = self.partner('clean')
        self.outside = self.partner(' other ')
        self.issue_for(self.rule, self.failing)
        self.issue_for(self.rule, self.outside)

    def _upper(self, **vals):
        return self.correction(
            'res.partner', 'name', ['case'], rule=self.rule, text_case='upper',
            domain_ids=(self.failing | self.passing).ids, **vals)

    def test_b_0211_dry_run_leaves_a_passing_record_of_the_filter_out(self):
        """B-0211: the dry run lists the record that fails the rule and is in the filter, not the passing one."""
        run = self.dry_run(self._upper())
        self.assertTrue(self.line_of(run, self.failing, 'would_change'))
        self.assertFalse(self.line_of(run, self.passing), "the passing record of the filter was targeted")
        self.assertFalse(self.line_of(run, self.outside), "a failing record outside the filter was targeted")

    def test_b_0211_real_run_changes_only_failing_records_inside_the_filter(self):
        """B-0211: the real run upper-cases the failing record of the filter and nothing else."""
        correction = self._upper()
        self.dry_run(correction)
        self.real_run(correction)
        (self.failing | self.passing | self.outside).invalidate_recordset()
        self.assertEqual(
            (self.failing.name, self.passing.name, self.outside.name), (' PADDED ', 'clean', ' other '))

    def test_b_0211_a_rule_with_no_open_issue_leaves_the_filter_with_no_target(self):
        """B-0211: with the rule's issues all closed, the filter alone does not make every record a target."""
        self.env['data.quality.issue'].search([('rule_id', '=', self.rule.id)]).write({'state': 'resolved'})
        run = self.dry_run(self._upper())
        self.assertFalse(self.lines(run))
