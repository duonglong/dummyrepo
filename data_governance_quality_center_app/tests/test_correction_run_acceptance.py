from unittest.mock import patch

from odoo.exceptions import UserError, ValidationError

from .acceptance_m2b_helpers import M2BCase, ROLLBACK, call_first


class TestCorrectionRunAcceptance(M2BCase):
    """Acceptance tests of T-020: bulk correction run, protected records, rollback.

    A real run is the correction's button; the background runner works on the run
    (``data.quality.correction.run``) in batches; the log is ``data.quality.correction.log``. The
    rollback is the run's button, called as a manager. Accounting models are not installed with
    this module, so the protected records of the scenarios are the contacts of a model marked
    restricted (BR-DGQ-0005). The country and state standardization (FR-DGQ-0052) has no test here:
    the scenario does not say which text field holds the country text nor what a dry run shows.

    Requirements: BR-DGQ-0005, BR-DGQ-0007, FR-DGQ-0049, FR-DGQ-0050, FR-DGQ-0051
    """

    def setUp(self):
        super().setUp()
        self.rule = self.make_rule(
            'res.partner', 'pattern', ['email'], name='E-mail format',
            pattern=r'^[a-z0-9._-]+@[a-z0-9.-]+\.[a-z]+$')
        self.a = self.partner('Alpha', email=' ACME@Mail.COM ')
        self.b = self.partner('Bravo', email='Beta@B.com ')
        self.c = self.partner('Charlie', email=' gamma@c.com')
        self.fine = self.partner('Fine', email='fine@ok.com')
        self.failing = self.a | self.b | self.c

    def fix(self, **vals):
        return self.correction('res.partner', 'email', ['email'], **vals)

    def run_it(self, correction):
        self.dry_run(correction)
        return self.real_run(correction)

    # ---- FR-DGQ-0049 -------------------------------------------------------

    def test_run_changes_the_values_logs_each_and_resolves_the_issues(self):
        """SC-DGQ-0049-1: 3 e-mails changed, 3 log lines with before, after, user and time, the 3 issues Resolved."""
        issues = self.env['data.quality.issue']
        for partner in self.failing:
            issues |= self.issue_for(self.rule, partner)
        run = self.run_it(self.fix(rule=self.rule))
        self.assertEqual(run.state, 'done')
        for partner, email in ((self.a, 'acme@mail.com'), (self.b, 'beta@b.com'), (self.c, 'gamma@c.com')):
            partner.invalidate_recordset()
            self.assertEqual(partner.email, email)
        changed = self.lines(run, 'changed')
        self.assertEqual(len(changed), 3)
        line = self.line_of(run, self.a, 'changed')
        self.assertEqual(line.field_name, 'email')
        self.assertEqual(line.value_before, ' ACME@Mail.COM ')
        self.assertEqual(line.value_after, 'acme@mail.com')
        self.assertEqual(line.user_id, self.manager_a)
        self.assertTrue(line.date)
        self.assertEqual(run.count_changed, 3)
        issues.invalidate_recordset()
        self.assertEqual(set(issues.mapped('state')), {'resolved'})
        self.fine.invalidate_recordset()
        self.assertEqual(self.fine.email, 'fine@ok.com')

    def test_the_run_shows_changed_unchanged_skipped_and_failed_counts(self):
        """FR-DGQ-0049: 3 changed, 1 unchanged, 1 skipped (cannot be normalized) and 0 failed."""
        broken = self.partner('Broken', email='not an e-mail')
        ids = (self.failing | self.fine | broken).ids
        run = self.run_it(self.fix(domain_ids=ids))
        self.assertEqual(
            (run.count_changed, run.count_unchanged, run.count_skipped, run.count_failed), (3, 1, 1, 0))
        self.assertTrue(self.line_of(run, broken, 'skipped'))

    def test_a_record_that_fails_to_save_is_logged_failed_and_the_run_goes_on(self):
        """SC-DGQ-0049-2: the locked record is failed with the error; the other records are corrected."""
        Partner = type(self.env['res.partner'])
        original = Partner.write
        locked_id = self.b.id

        def locked_write(this, vals):
            if locked_id in this.ids and 'email' in vals:
                raise ValidationError('Locked for the test')
            return original(this, vals)

        correction = self.fix(domain_ids=self.failing.ids)
        self.dry_run(correction)
        with patch.object(Partner, 'write', locked_write):
            run = self.real_run(correction)
        for partner in (self.a, self.b, self.c):
            partner.invalidate_recordset()
        self.assertEqual(self.a.email, 'acme@mail.com')
        self.assertEqual(self.c.email, 'gamma@c.com')
        self.assertEqual(self.b.email, 'Beta@B.com ')
        failed = self.line_of(run, self.b, 'failed')
        self.assertEqual(len(failed), 1)
        self.assertIn('Locked for the test', failed.reason or '')
        self.assertEqual(run.count_failed, 1)
        self.assertEqual(run.count_changed, 2)

    # ---- FR-DGQ-0050 / BR-DGQ-0007 -----------------------------------------

    def test_rollback_restores_untouched_values_and_lists_changed_ones(self):
        """SC-DGQ-0050-1: A is back to its value before; B, edited by hand since, keeps the edit and is listed as not restored."""
        run = self.run_it(self.fix(domain_ids=(self.a | self.b).ids))
        self.b.sudo().write({'email': 'manual@b.com'})
        call_first(run.with_user(self.manager_a), ROLLBACK)
        rollback = self.env['data.quality.correction.run'].sudo().search([('rollback_of_id', '=', run.id)])
        self.assertEqual(len(rollback), 1)
        self.assertEqual(rollback.kind, 'rollback')
        self.drain_job(rollback)
        for partner in (self.a, self.b):
            partner.invalidate_recordset()
        self.assertEqual(self.a.email, ' ACME@Mail.COM ')
        self.assertEqual(self.b.email, 'manual@b.com')
        restored = self.line_of(rollback, self.a, 'restored')
        self.assertEqual(len(restored), 1)
        not_restored = self.line_of(rollback, self.b, 'not_restored')
        self.assertEqual(len(not_restored), 1)
        self.assertRegex(not_restored.reason or '', r'(?i)chang')
        self.assertEqual((rollback.count_restored, rollback.count_not_restored), (1, 1))
        run.invalidate_recordset()
        self.assertTrue(run.rolled_back)

    def test_rollback_writes_back_a_value_still_equal_to_the_one_written(self):
        """BR-DGQ-0007: ' ACME ' trimmed to 'ACME' is restored to ' ACME ' while the field still reads 'ACME'."""
        padded = self.partner(' ACME ')
        correction = self.correction('res.partner', 'name', ['trim'], domain_ids=padded.ids)
        run = self.run_it(correction)
        padded.invalidate_recordset()
        self.assertEqual(padded.name, 'ACME')
        call_first(run.with_user(self.manager_a), ROLLBACK)
        rollback = self.env['data.quality.correction.run'].sudo().search([('rollback_of_id', '=', run.id)])
        self.drain_job(rollback)
        padded.invalidate_recordset()
        self.assertEqual(padded.name, ' ACME ')

    # ---- FR-DGQ-0051 / BR-DGQ-0005 -----------------------------------------

    def restrict_contacts(self):
        self.env['data.quality.model.config'].search([('model_name', '=', 'res.partner')]).write(
            {'status': 'restricted'})

    def approved_run(self, correction):
        self.dry_run(correction)
        if correction.needs_approval:
            self.approve(correction, self.manager_b)
        return self.real_run(correction)

    def test_protected_records_are_skipped_without_authorization(self):
        """SC-DGQ-0051-1: a trim of the reference of protected records changes none and logs each as skipped because protected."""
        self.restrict_contacts()
        one = self.partner('Ref One', ref=' R1 ')
        two = self.partner('Ref Two', ref=' R2 ')
        correction = self.correction('res.partner', 'ref', ['trim'], domain_ids=(one | two).ids)
        run = self.approved_run(correction)
        for partner, ref in ((one, ' R1 '), (two, ' R2 ')):
            partner.invalidate_recordset()
            self.assertEqual(partner.ref, ref)
        skipped = self.lines(run, 'skipped')
        self.assertEqual(set(skipped.mapped('res_id')), {one.id, two.id})
        for line in skipped:
            self.assertIn('protected', (line.reason or '').lower())
        self.assertFalse(self.lines(run, 'changed'))
        self.assertEqual(run.count_skipped, 2)

    def test_authorized_correction_changes_protected_records_after_approval(self):
        """FR-DGQ-0051: with the administrator's authorization the correction changes protected records; it always needs approval."""
        self.restrict_contacts()
        one = self.partner('Ref One', ref=' R1 ')
        correction = self.correction(
            'res.partner', 'ref', ['trim'], user=self.administrator, domain_ids=one.ids,
            protected_authorized=True)
        self.assertTrue(correction.needs_approval)
        self.dry_run(correction)
        self.approve(correction, self.administrator)
        self.real_run(correction)
        one.invalidate_recordset()
        self.assertEqual(one.ref, 'R1')

    def test_a_manager_cannot_authorize_a_correction_for_protected_records(self):
        """SC-DGQ-0051-2: a manager who is not an administrator is refused when authorizing protected records."""
        correction = self.fix()
        with self.assertRaises(UserError):
            correction.with_user(self.manager_a).write({'protected_authorized': True})
        correction.invalidate_recordset()
        self.assertFalse(correction.protected_authorized)

    def test_an_administrator_can_authorize_a_correction_for_protected_records(self):
        """FR-DGQ-0051: the same authorization by an administrator is saved."""
        correction = self.fix()
        correction.with_user(self.administrator).write({'protected_authorized': True})
        correction.invalidate_recordset()
        self.assertTrue(correction.protected_authorized)
