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


class TestCorrectionScopeAfterResolve(M2BCase):
    """Regression tests for B-0218: a correction with a rule keeps the rule's issues as its scope once they are closed.

    Reached through the dry run and real run buttons and the background runner. The screens are client side.

    Requirements: FR-DGQ-0046, FR-DGQ-0047, FR-DGQ-0049
    """

    def setUp(self):
        super().setUp()
        self.rule = self.make_rule(
            'res.partner', 'pattern', ['name'], name='Name has outer spaces', pattern=r'^\S(.*\S)?$')
        self.closed = self.partner(' closed ')
        self.untouched = self.partner(' never failed ')
        self.issue = self.issue_for(self.rule, self.closed)

    def _upper(self):
        return self.correction(
            'res.partner', 'name', ['case'], rule=self.rule, text_case='upper',
            domain_ids=(self.closed | self.untouched).ids)

    def _close(self, state, **vals):
        self.issue.write(dict({'state': state}, **vals))

    def _assert_nothing_targeted(self):
        correction = self._upper()
        run = self.dry_run(correction)
        self.assertFalse(self.lines(run), "the dry run targeted records that do not fail the rule")
        self.real_run(correction)
        (self.closed | self.untouched).invalidate_recordset()
        self.assertEqual((self.closed.name, self.untouched.name), (' closed ', ' never failed '))

    def test_b_0218_resolved_issues_leave_no_target(self):
        """B-0218: the only issue of the rule is resolved; a run changes neither record."""
        self._close('resolved')
        self._assert_nothing_targeted()

    def test_b_0218_ignored_issues_leave_no_target(self):
        """B-0218: the only issue of the rule is ignored; a run changes neither record."""
        self._close('ignored', ignore_reason='accepted')
        self._assert_nothing_targeted()

    def test_b_0218_a_rule_that_never_had_an_issue_keeps_the_filter(self):
        """B-0218: with no issue at all (no scan yet) the filter alone is the scope, as before."""
        self.issue.unlink()
        run = self.dry_run(self._upper())
        self.assertTrue(self.line_of(run, self.closed, 'would_change'))
        self.assertTrue(self.line_of(run, self.untouched, 'would_change'))
