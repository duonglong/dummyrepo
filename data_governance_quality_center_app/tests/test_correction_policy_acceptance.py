from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form

from .acceptance_m2b_helpers import APPROVE, M2BCase, REAL_RUN, call_first, choice


class TestCorrectionPolicyAcceptance(M2BCase):
    """Acceptance tests of T-019: correction policy, dry run and approval.

    The correction form is driven with ``Form`` (model, field, rule, action) as a manager; the dry
    run, approval and real run are the correction's buttons, called under the usual names of their
    actions (the design does not name the methods). A dry run's result is the run it leaves in
    ``last_dry_run_id`` with its log lines (``would_change``, ``skipped``) and counts.

    Requirements: BR-DGQ-0006, FR-DGQ-0046, FR-DGQ-0047, FR-DGQ-0048
    """

    def setUp(self):
        super().setUp()
        self.email_rule = self.make_rule(
            'res.partner', 'pattern', ['email'], name='E-mail format',
            pattern=r'^[a-z0-9._-]+@[a-z0-9.-]+\.[a-z]+$')
        self.critical_rule = self.make_rule(
            'res.partner', 'pattern', ['email'], name='Critical e-mail', severity='critical',
            pattern=r'^[a-z0-9._-]+@[a-z0-9.-]+\.[a-z]+$')
        self.padded = self.partner('Padded', email=' ACME@Mail.COM ')
        self.fine = self.partner('Fine', email='beta@b.com')
        self.broken = self.partner('Broken', email='not an e-mail')
        self.ids = (self.padded | self.fine | self.broken).ids

    def email_fix(self, **vals):
        return self.correction('res.partner', 'email', ['email'], domain_ids=self.ids, **vals)

    # ---- FR-DGQ-0046 -------------------------------------------------------

    def test_manager_saves_a_correction_in_draft(self):
        """SC-DGQ-0046-1: normalize e-mail on Contact E-mail targeting the rule 'E-mail format' is stored in draft."""
        Correction = self.env['data.quality.correction'].with_user(self.manager_a)
        with Form(Correction) as form:
            form.name = 'Normalize contact e-mails'
            form.model_id = self.model('res.partner')
            form.field_id = self.fld('res.partner', 'email')
            form.rule_id = self.email_rule
            form.action = choice(self.env, 'data.quality.correction', 'action', ['email'])
        correction = form.record
        self.assertEqual(correction.state, 'draft')
        self.assertEqual(correction.rule_id, self.email_rule)
        self.assertEqual(correction.author_id, self.manager_a)

    def test_trimming_a_number_field_is_refused(self):
        """SC-DGQ-0046-2: trim spaces on a number field is refused: trimming applies to text fields only."""
        with self.assertRaises(ValidationError) as caught:
            self.correction('res.partner', 'color', ['trim'])
        self.assertIn('text', str(caught.exception).lower())

    def test_replacing_a_link_on_a_text_field_is_refused(self):
        """FR-DGQ-0046: replace a link on a text field is refused: it needs a link field."""
        with self.assertRaises(ValidationError):
            self.correction('res.partner', 'name', ['link'])

    def test_a_field_that_is_not_stored_is_refused(self):
        """FR-DGQ-0046: a computed field that is not stored cannot be corrected."""
        with self.assertRaises(ValidationError):
            self.correction('res.partner', 'display_name', ['trim'])

    def test_every_action_of_the_policy_is_offered(self):
        """FR-DGQ-0046: default value, trim, e-mail, phone, text case, country and state, replace link are offered."""
        selection = self.env['data.quality.correction']._fields['action']._description_selection(self.env)
        text = ' '.join('%s %s' % (key, label) for key, label in selection).lower()
        for word in ('default', 'trim', 'mail', 'phone', 'case', 'country', 'link'):
            self.assertIn(word, text)

    # ---- FR-DGQ-0047 -------------------------------------------------------

    def test_dry_run_shows_before_and_after_and_changes_nothing(self):
        """SC-DGQ-0047-1: ' ACME@Mail.COM ' becomes 'acme@mail.com', 'beta@b.com' is unchanged, 'not an e-mail' is skipped, nothing is written."""
        correction = self.email_fix()
        run = self.dry_run(correction)
        self.assertTrue(run)
        self.assertEqual(run.kind, 'dry_run')
        change = self.line_of(run, self.padded, 'would_change')
        self.assertEqual(len(change), 1)
        self.assertEqual(change.value_before, ' ACME@Mail.COM ')
        self.assertEqual(change.value_after, 'acme@mail.com')
        self.assertFalse(self.line_of(run, self.fine, 'would_change'))
        self.assertEqual(run.count_unchanged, 1)
        skipped = self.line_of(run, self.broken, 'skipped')
        self.assertEqual(len(skipped), 1)
        self.assertRegex(skipped.reason or '', r'(?i)normali|e-mail|email')
        self.assertEqual(run.count_skipped, 1)
        for partner, email in ((self.padded, ' ACME@Mail.COM '), (self.fine, 'beta@b.com'),
                               (self.broken, 'not an e-mail')):
            partner.invalidate_recordset()
            self.assertEqual(partner.email, email)

    def test_the_first_real_run_needs_a_dry_run(self):
        """FR-DGQ-0047: a correction never dry-run cannot run for real."""
        correction = self.email_fix(user=self.manager_a)
        with self.assertRaises(UserError) as caught:
            call_first(correction, REAL_RUN)
        self.assertIn('dry run', str(caught.exception).lower())
        self.padded.invalidate_recordset()
        self.assertEqual(self.padded.email, ' ACME@Mail.COM ')

    # ---- BR-DGQ-0006 -------------------------------------------------------

    def test_critical_sensitive_or_protected_corrections_need_approval(self):
        """BR-DGQ-0006: a critical rule or a sensitive field needs approval; a warning rule on an ordinary field does not."""
        ordinary = self.email_fix(rule=self.email_rule)
        self.assertFalse(ordinary.needs_approval)
        critical = self.email_fix(rule=self.critical_rule)
        self.assertTrue(critical.needs_approval)
        self.env['data.quality.field.config'].create({
            'field_id': self.fld('res.partner', 'comment').id, 'status': 'sensitive'})
        sensitive = self.correction('res.partner', 'comment', ['trim'], domain_ids=self.ids)
        self.assertTrue(sensitive.needs_approval)

    def test_a_correction_that_may_change_a_protected_record_needs_approval(self):
        """BR-DGQ-0006: a correction of a restricted model (its records are protected) needs approval."""
        self.env['data.quality.model.config'].search([('model_name', '=', 'res.partner')]).write(
            {'status': 'restricted'})
        correction = self.email_fix(rule=self.email_rule)
        self.assertTrue(correction.needs_approval)

    # ---- FR-DGQ-0048 -------------------------------------------------------

    def critical_fix(self):
        correction = self.email_fix(rule=self.critical_rule)
        self.dry_run(correction)
        return correction

    def test_author_cannot_run_a_critical_correction_before_approval(self):
        """SC-DGQ-0048-1: Ann runs her dry-run critical correction for real: refused, it waits for approval."""
        correction = self.critical_fix()
        with self.assertRaises(UserError) as caught:
            call_first(correction, REAL_RUN)
        self.assertIn('approv', str(caught.exception).lower())
        self.padded.invalidate_recordset()
        self.assertEqual(self.padded.email, ' ACME@Mail.COM ')

    def test_author_cannot_approve_their_own_correction(self):
        """BR-DGQ-0006: a manager cannot approve the correction they wrote."""
        correction = self.critical_fix()
        with self.assertRaises(UserError):
            call_first(correction.with_user(self.manager_a), APPROVE)
        correction.invalidate_recordset()
        self.assertNotEqual(correction.state, 'approved')

    def test_another_manager_approves_after_the_dry_run(self):
        """FR-DGQ-0048: Bob approves; the approver, date and the dry run he saw are kept."""
        correction = self.critical_fix()
        self.approve(correction, self.manager_b)
        self.assertEqual(correction.state, 'approved')
        self.assertEqual(correction.approver_id, self.manager_b)
        self.assertTrue(correction.approval_date)
        self.assertEqual(correction.approved_dry_run_id, correction.last_dry_run_id)

    def test_approval_needs_a_dry_run_first(self):
        """FR-DGQ-0048: a correction that was never dry-run cannot be approved."""
        correction = self.email_fix(rule=self.critical_rule)
        with self.assertRaises(UserError):
            call_first(correction.with_user(self.manager_b), APPROVE)
        self.assertNotEqual(correction.state, 'approved')

    def test_an_administrator_may_approve_their_own_correction(self):
        """BR-DGQ-0006: an administrator approves a correction they wrote themselves."""
        correction = self.email_fix(user=self.administrator, rule=self.critical_rule)
        self.dry_run(correction)
        self.approve(correction, self.administrator)
        self.assertEqual(correction.state, 'approved')

    def test_an_approved_correction_runs_for_real(self):
        """FR-DGQ-0048: once Bob has approved, Ann's correction runs and writes the normalized e-mail."""
        correction = self.critical_fix()
        self.approve(correction, self.manager_b)
        self.real_run(correction)
        self.padded.invalidate_recordset()
        self.assertEqual(self.padded.email, 'acme@mail.com')

    def test_changing_the_action_sends_the_correction_back_to_draft(self):
        """SC-DGQ-0048-2: Ann changes the action of the correction Bob approved: it is in draft and needs a new approval."""
        correction = self.critical_fix()
        self.approve(correction, self.manager_b)
        correction.with_user(self.manager_a).write(
            {'action': choice(self.env, 'data.quality.correction', 'action', ['trim'])})
        correction.invalidate_recordset()
        self.assertEqual(correction.state, 'draft')
        self.assertFalse(correction.approver_id)
        self.assertFalse(correction.approved_dry_run_id)
        with self.assertRaises(UserError):
            call_first(correction, REAL_RUN)

    def test_changing_the_field_or_the_rule_removes_the_approval(self):
        """FR-DGQ-0048: a change of the field or of the rule is a new definition: back to draft."""
        for change in ({'field_id': self.fld('res.partner', 'name').id},
                       {'rule_id': self.email_rule.id}):
            correction = self.critical_fix()
            self.approve(correction, self.manager_b)
            correction.with_user(self.manager_a).write(change)
            correction.invalidate_recordset()
            self.assertEqual(correction.state, 'draft', change)
            self.assertFalse(correction.approver_id, change)

    def test_a_warning_correction_on_an_ordinary_field_runs_without_approval(self):
        """SC-DGQ-0048-3: its author runs it for real after a dry run: no approval is asked."""
        correction = self.email_fix(rule=self.email_rule)
        self.assertFalse(correction.needs_approval)
        self.dry_run(correction)
        run = self.real_run(correction)
        self.assertEqual(run.kind, 'run')
        self.padded.invalidate_recordset()
        self.assertEqual(self.padded.email, 'acme@mail.com')
