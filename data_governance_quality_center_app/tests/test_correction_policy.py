from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError

from .acceptance_m2b_helpers import M2BCase, REAL_RUN, call_first, choice


class TestCorrectionPolicy(M2BCase):
    """Unit tests of the correction policy engine beyond the acceptance scenarios.

    Requirements: BR-DGQ-0006, FR-DGQ-0047, FR-DGQ-0048
    """

    def setUp(self):
        super().setUp()
        self.mark_comment_sensitive = lambda: self.env['data.quality.field.config'].create({
            'field_id': self.fld('res.partner', 'comment').id, 'status': 'sensitive'})
        self.critical_rule = self.make_rule(
            'res.partner', 'pattern', ['email'], severity='critical', pattern=r'^[a-z]+@[a-z]+\.com$')
        self.padded = self.partner('Padded', email=' ACME@Mail.COM ')

    def test_a_mapping_change_sends_an_approved_correction_back_to_draft(self):
        """FR-DGQ-0048: the mappings are part of what a correction does."""
        correction = self.correction(
            'res.partner', 'email', ['email'], rule=self.critical_rule, domain_ids=self.padded.ids)
        self.dry_run(correction)
        self.approve(correction, self.manager_b)
        self.assertEqual(correction.state, 'approved')
        correction.write({'mapping_ids': [Command.create({'source_text': 'UK'})]})
        correction.invalidate_recordset()
        self.assertEqual(correction.state, 'draft')
        self.assertFalse(correction.last_dry_run_id)

    def test_a_mark_set_after_saving_still_asks_for_approval(self):
        """BR-DGQ-0006: the need for approval is read when it matters, not when it was saved."""
        correction = self.correction('res.partner', 'email', ['email'], domain_ids=self.padded.ids)
        self.assertFalse(correction.needs_approval)
        self.env['data.quality.model.config'].search([('model_name', '=', 'res.partner')]).write(
            {'status': 'restricted'})
        correction.invalidate_recordset()
        self.assertTrue(correction.needs_approval)

    def test_a_dry_run_of_protected_records_skips_them(self):
        """FR-DGQ-0047: records of a restricted model are skipped as protected and nothing is written."""
        self.env['data.quality.model.config'].search([('model_name', '=', 'res.partner')]).write(
            {'status': 'restricted'})
        correction = self.correction('res.partner', 'email', ['email'], domain_ids=self.padded.ids)
        run = self.dry_run(correction)
        self.assertEqual(run.count_skipped, 1)
        self.assertIn('protected', run.log_ids.reason.lower())
        self.padded.invalidate_recordset()
        self.assertEqual(self.padded.email, ' ACME@Mail.COM ')

    def test_only_an_administrator_authorizes_protected_records(self):
        """BR-DGQ-0006: a manager is refused the authorization, an administrator is accepted."""
        correction = self.correction('res.partner', 'email', ['trim'], domain_ids=self.padded.ids)
        with self.assertRaises(UserError):
            correction.with_user(self.manager_a).write({'protected_authorized': True})
        correction.with_user(self.administrator).write({'protected_authorized': True})
        self.assertTrue(correction.protected_authorized)

    def test_approval_fields_cannot_be_written_by_a_manager(self):
        """BR-DGQ-0048: the state and approval fields are set by the buttons only; the author is the creator."""
        correction = self.correction('res.partner', 'email', ['trim'], domain_ids=self.padded.ids)
        self.assertEqual(correction.author_id, self.manager_a)
        for vals in ({'state': 'approved'}, {'approver_id': self.manager_b.id},
                     {'author_id': self.manager_b.id}):
            with self.assertRaises(UserError):
                correction.with_user(self.manager_a).write(vals)
        with self.assertRaises(UserError):
            self.correction(
                'res.partner', 'email', ['trim'], domain_ids=self.padded.ids,
                state='approved')
        with self.assertRaises(UserError):
            self.correction(
                'res.partner', 'email', ['trim'], domain_ids=self.padded.ids,
                author_id=self.manager_b.id)

    def test_a_ready_correction_that_comes_to_need_approval_can_be_approved(self):
        """FR-DGQ-0048: a mark set after the dry run sends the next dry run to approval, then it runs."""
        correction = self.correction('res.partner', 'comment', ['trim'], domain_ids=self.padded.ids)
        self.dry_run(correction)
        self.assertEqual(correction.state, 'ready')
        self.mark_comment_sensitive()
        correction.invalidate_recordset()
        self.dry_run(correction)
        self.assertEqual(correction.state, 'to_approve')
        self.approve(correction, self.manager_b)
        self.real_run(correction)

    def test_a_real_run_cannot_be_started_through_the_run_record(self):
        """BR-DGQ-0006: creating and starting a run of a correction not ready for it is refused."""
        correction = self.correction('res.partner', 'email', ['trim'], rule=self.critical_rule,
                                     domain_ids=self.padded.ids)
        run = self.env['data.quality.correction.run'].with_user(self.manager_a).create(
            {'correction_id': correction.id, 'kind': 'run'})
        with self.assertRaises(UserError):
            run.action_start()
        self.dry_run(correction)
        with self.assertRaises(UserError):
            run.action_start()

    def test_a_change_in_the_middle_of_a_real_run_stops_it(self):
        """FR-DGQ-0048: a definition change while a run is queued stops it without writing."""
        correction = self.correction('res.partner', 'email', ['trim'], domain_ids=self.padded.ids)
        self.dry_run(correction)
        call_first(correction, REAL_RUN)
        run = correction.run_ids.filtered(lambda r: r.kind == 'run')
        correction.write({'action': self.correction_action(['case'])})
        self.drain_job(run)
        self.assertEqual(run.state, 'failed')
        self.assertFalse(run.log_ids)
        self.padded.invalidate_recordset()
        self.assertEqual(self.padded.email, ' ACME@Mail.COM ')

    def test_two_real_runs_cannot_be_queued_at_once(self):
        """FR-DGQ-0047: a second real run waits until the first one ended."""
        correction = self.correction('res.partner', 'email', ['trim'], domain_ids=self.padded.ids)
        self.dry_run(correction)
        call_first(correction, REAL_RUN)
        with self.assertRaises(UserError) as caught:
            call_first(correction, REAL_RUN)
        self.assertIn('already running', str(caught.exception))

    def test_a_correction_that_ran_is_archived_not_deleted(self):
        """BR-DGQ-0013: the logs of a real run are kept: the correction cannot be deleted."""
        correction = self.correction('res.partner', 'email', ['trim'], domain_ids=self.padded.ids)
        self.dry_run(correction)
        self.real_run(correction)
        with self.assertRaises(UserError) as caught:
            correction.with_user(self.manager_a).unlink()
        self.assertIn('archive', str(caught.exception))
        self.assertTrue(correction.run_ids.log_ids)

    def test_a_filter_that_reaches_a_sensitive_field_is_refused(self):
        """BR-DGQ-0010: a filter is plain text on fields a scan may read, never run as code."""
        self.mark_comment_sensitive()
        for text in ("[('comment', '=', 'x')]", "[('name', '=', __import__('os'))]", "[('nope', '=', 1)]"):
            with self.assertRaises(ValidationError, msg=text):
                self.correction('res.partner', 'email', ['trim'], domain=text)

    def test_the_values_of_a_sensitive_field_are_not_kept_in_the_log(self):
        """BR-DGQ-0010: the log of a sensitive field says what happened, never the values."""
        self.env['data.quality.field.config'].create({
            'field_id': self.fld('res.partner', 'email').id, 'status': 'sensitive'})
        correction = self.correction('res.partner', 'email', ['trim'], domain_ids=self.padded.ids)
        run = self.dry_run(correction)
        line = run.log_ids
        self.assertEqual(line.state, 'would_change')
        self.assertFalse(line.value_before)
        self.assertFalse(line.value_after)

    def test_settings_that_cannot_work_are_refused_when_saved(self):
        """FR-DGQ-0046: a link to a contact is no country, a default must be a choice, links must exist."""
        with self.assertRaises(ValidationError):
            self.correction('res.partner', 'parent_id', ['country'])
        with self.assertRaises(ValidationError):
            self.correction('res.partner', 'type', ['default'], default_value='nonsense')
        with self.assertRaises(ValidationError):
            self.correction('res.partner', 'parent_id', ['replace', 'link'], link_from_id=0, link_to_id=-5)

    def test_a_manager_of_another_company_cannot_touch_a_correction(self):
        """FR-DGQ-0071: write, delete and runs of a correction of company X are closed to company Y."""
        correction = self.correction('res.partner', 'email', ['trim'], domain_ids=self.padded.ids)
        self.dry_run(correction)
        outsider = self.user('manager', login='dgq_m2b_outsider', company=self.cy)
        outsider.write({'company_ids': [Command.set([self.cy.id])], 'company_id': self.cy.id})
        with self.assertRaises(AccessError):
            correction.with_user(outsider).write({'name': 'mine'})
        with self.assertRaises(AccessError):
            correction.with_user(outsider).unlink()
        self.assertFalse(
            self.env['data.quality.correction.run'].with_user(outsider).search(
                [('correction_id', '=', correction.id)]))

    def test_a_stale_dry_run_does_not_allow_a_real_run(self):
        """FR-DGQ-0047: a change of the definition removes the dry run: a new one is needed."""
        correction = self.correction('res.partner', 'email', ['email'], domain_ids=self.padded.ids)
        self.dry_run(correction)
        correction.write({'action': self.correction_action(['trim'])})
        with self.assertRaises(UserError) as caught:
            call_first(correction, REAL_RUN)
        self.assertIn('dry run', str(caught.exception).lower())

    def correction_action(self, words):
        return choice(self.env, 'data.quality.correction', 'action', words)
