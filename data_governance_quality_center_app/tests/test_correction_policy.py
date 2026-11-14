from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError

from .acceptance_m2b_helpers import M2BCase, APPROVE, DRY_RUN, REAL_RUN, call_first, choice


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
        """BR-DGQ-0006, FR-DGQ-0048: the state and approval fields are set by the buttons only; the author is the creator."""
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

    def test_context_defaults_cannot_fill_the_approval_fields(self):
        """BR-DGQ-0006, FR-DGQ-0048: a default_<field> in the context does not set state or author."""
        correction = self.correction(
            'res.partner', 'email', ['trim'], domain_ids=self.padded.ids,
            rule=self.critical_rule)
        mine = self.env['data.quality.correction'].with_user(self.manager_a).with_context(
            default_state='approved', default_author_id=self.manager_b.id,
            default_approver_id=self.manager_b.id).create({
                'name': 'Mine', 'model_id': correction.model_id.id, 'field_id': correction.field_id.id,
                'action': correction.action, 'rule_id': self.critical_rule.id})
        self.assertEqual(mine.state, 'draft')
        self.assertEqual(mine.author_id, self.manager_a)
        self.assertFalse(mine.approver_id)

    def test_a_manager_cannot_write_the_fields_a_run_is_trusted_for(self):
        """BR-DGQ-0006, FR-DGQ-0071: targets, requesting user and state of a run are set by the app."""
        correction = self.correction('res.partner', 'email', ['trim'], domain_ids=self.padded.ids)
        Run = self.env['data.quality.correction.run'].with_user(self.manager_a)
        with self.assertRaises(UserError):
            Run.create({'correction_id': correction.id, 'kind': 'run', 'res_ids': [self.padded.id]})
        run = Run.create({'correction_id': correction.id, 'kind': 'dry_run'})
        for vals in ({'user_id': self.administrator.id}, {'state': 'done'}, {'res_ids': [1]}, {'last_id': 5}):
            with self.assertRaises(UserError):
                run.write(vals)
        run.action_start()
        self.assertEqual(run.state, 'queued')
        self.assertEqual(run.user_id, self.manager_a)
        run.action_cancel()
        self.assertEqual(run.state, 'cancelled')

    def test_a_filter_is_checked_again_when_a_field_is_marked_after_saving(self):
        """BR-DGQ-0010: a mark set after the filter was saved stops the dry run, no log line is kept."""
        correction = self.correction(
            'res.partner', 'email', ['trim'], domain="[('comment', '=', 'x')]")
        self.mark_comment_sensitive()
        with self.assertRaises(ValidationError):
            call_first(correction, DRY_RUN)
        self.assertFalse(correction.run_ids.log_ids)

    def test_a_user_without_write_on_runs_cannot_start_resume_or_cancel_them(self):
        """BR-DGQ-0006, FR-DGQ-0048, FR-DGQ-0071: viewers and managers of another company are refused."""
        correction = self.correction('res.partner', 'email', ['trim'], domain_ids=self.padded.ids)
        self.dry_run(correction)
        run = self.env['data.quality.correction.run'].with_user(self.manager_a).create(
            {'correction_id': correction.id, 'kind': 'run'})
        viewer = self.user('viewer', login='dgq_m2b_run_viewer', company=self.cx)
        outsider = self.user('manager', login='dgq_m2b_run_outsider', company=self.cy)
        outsider.write({'company_ids': [Command.set([self.cy.id])], 'company_id': self.cy.id})
        with self.assertRaises(AccessError):
            run.with_user(viewer).action_start()
        with self.assertRaises(AccessError):
            run.with_user(viewer).action_cancel()
        with self.assertRaises(AccessError):
            run.with_user(outsider).action_cancel()
        run.sudo().state = 'failed'  # sudo: run fields no user writes
        with self.assertRaises(AccessError):
            run.with_user(viewer).action_resume()
        self.assertEqual(run.sudo().state, 'failed')  # sudo: run fields no user writes

    def test_a_viewer_cannot_press_the_buttons_of_a_correction(self):
        """BR-DGQ-0006, FR-DGQ-0048: a viewer who is not the author still cannot dry-run, approve or run."""
        correction = self.correction('res.partner', 'email', ['trim'], rule=self.critical_rule,
                                     domain_ids=self.padded.ids)
        viewer = self.user('viewer', login='dgq_m2b_button_viewer', company=self.cx)
        with self.assertRaises(AccessError):
            call_first(correction.with_user(viewer), DRY_RUN)
        self.dry_run(correction)
        self.assertEqual(correction.state, 'to_approve')
        for names in (APPROVE, REAL_RUN):
            with self.assertRaises(AccessError):
                call_first(correction.with_user(viewer), names)
        self.assertEqual(correction.state, 'to_approve')

    def test_a_run_keeps_its_kind_and_correction(self):
        """BR-DGQ-0006, FR-DGQ-0048: a run that ran for real stays a real run of its correction."""
        correction = self.correction('res.partner', 'email', ['trim'], domain_ids=self.padded.ids)
        other = self.correction('res.partner', 'email', ['trim'], domain_ids=self.padded.ids)
        dry = self.dry_run(correction)
        run = self.real_run(correction)
        for record, vals in ((run, {'kind': 'dry_run'}), (dry, {'kind': 'run'}),
                             (dry, {'correction_id': other.id})):
            with self.assertRaises(UserError):
                record.with_user(self.manager_a).write(vals)
        with self.assertRaises(UserError):
            correction.with_user(self.manager_a).unlink()

    def test_context_defaults_cannot_fill_the_engine_fields_of_a_run(self):
        """BR-DGQ-0006, FR-DGQ-0048: context defaults give a run nothing but its correction and kind."""
        correction = self.correction('res.partner', 'email', ['trim'], domain_ids=self.padded.ids)
        self.dry_run(correction)
        run = self.env['data.quality.correction.run'].with_user(self.manager_a).with_context(
            default_state='queued', default_user_id=self.administrator.id, default_last_id=1,
            default_res_ids=[1], default_count_changed=7,
        ).create({'correction_id': correction.id, 'kind': 'run'})
        self.assertEqual(run.state, 'draft')
        self.assertFalse(run.user_id)
        self.assertFalse(run.last_id)
        self.assertFalse(run.res_ids)
        self.assertFalse(run.count_changed)

    def test_context_defaults_cannot_authorize_protected_records(self):
        """BR-DGQ-0006: only an administrator authorizes protected records, also through a context default."""
        mine = self.correction(
            'res.partner', 'email', ['trim'], domain_ids=self.padded.ids
        ).with_user(self.manager_a).with_context(default_protected_authorized=True).copy(
            {'name': 'Mine'})
        self.assertFalse(mine.protected_authorized)
        Correction = self.env['data.quality.correction'].with_user(self.manager_a).with_context(
            default_protected_authorized=True)
        fresh = Correction.create({
            'name': 'Fresh', 'model_id': mine.model_id.id, 'field_id': mine.field_id.id,
            'action': mine.action})
        self.assertFalse(fresh.protected_authorized)

    def test_an_old_real_run_cannot_go_on_after_the_correction_changed(self):
        """FR-DGQ-0048: a real run is bound to the dry run it started after; a newer one refuses it."""
        other = self.partner('Other', email=' other@b.com ')
        correction = self.correction('res.partner', 'email', ['trim'], domain_ids=self.padded.ids)
        self.dry_run(correction)
        call_first(correction, REAL_RUN)
        run = correction.run_ids.filtered(lambda r: r.kind == 'run')
        run.sudo().write({'state': 'failed', 'last_id': self.padded.id - 1})  # sudo: run fields no user writes
        correction.write({'domain': str([('id', 'in', other.ids)])})
        self.dry_run(correction)
        self.assertEqual(correction.state, 'ready')
        for method in ('action_resume', 'action_start'):
            with self.assertRaises(UserError, msg=method) as caught:
                getattr(run, method)()
            self.assertIn('start a new run', str(caught.exception))
            run.sudo().state = 'cancelled'  # sudo: run fields no user writes
        run.sudo().state = 'queued'  # sudo: run fields no user writes
        self.drain_job(run)
        self.assertEqual(run.sudo().state, 'failed')  # sudo: run fields no user writes
        self.padded.invalidate_recordset()
        self.assertEqual(self.padded.email, ' ACME@Mail.COM ')

    def test_the_first_batch_takes_the_targets_from_the_correction(self):
        """FR-DGQ-0047: targets stored on a run before it started are replaced by the filter's records."""
        other = self.partner('Other', email=' other@b.com ')
        correction = self.correction('res.partner', 'email', ['trim'], domain_ids=self.padded.ids)
        run = self.env['data.quality.correction.run'].with_user(self.manager_a).create(
            {'correction_id': correction.id, 'kind': 'dry_run'})
        run.sudo().res_ids = [self.padded.id, other.id]  # sudo: run fields no user writes
        run.action_start()
        self.drain_job(run)
        self.assertEqual(run.sudo().log_ids.mapped('res_id'), [self.padded.id])  # sudo: run fields no user writes

    def test_a_refused_write_on_a_sensitive_field_keeps_its_reason(self):
        """BR-DGQ-0010: the values of a sensitive field are hidden, the reason a write failed is kept."""
        grand = self.partner('Grand')
        self.padded.parent_id = grand
        child = self.partner('Child', parent_id=self.padded.id)
        self.env['data.quality.field.config'].create({
            'field_id': self.fld('res.partner', 'parent_id').id, 'status': 'sensitive'})
        correction = self.correction(
            'res.partner', 'parent_id', ['replace', 'link'], domain_ids=self.padded.ids,
            link_from_id=grand.id, link_to_id=child.id)
        self.dry_run(correction)
        self.approve(correction, self.manager_b)
        line = self.real_run(correction).sudo().log_ids  # sudo: run fields no user writes
        self.assertEqual(line.state, 'failed')
        self.assertIn('ValidationError', line.reason)
        self.assertFalse(line.value_before)

    def test_a_rule_used_by_a_correction_cannot_be_deleted(self):
        """BR-DGQ-0006, FR-DGQ-0048: deleting the rule must not widen an approved correction to the whole model."""
        correction = self.correction('res.partner', 'email', ['trim'], rule=self.critical_rule)
        self.dry_run(correction)
        self.approve(correction, self.manager_b)
        with self.assertRaises(Exception), self.cr.savepoint():
            self.critical_rule.unlink()
        self.assertEqual(correction.rule_id, self.critical_rule)
        self.assertEqual(correction.state, 'approved')

    def test_a_default_for_a_selection_with_computed_choices_is_accepted(self):
        """FR-DGQ-0046: the choices are read from the field, so a time zone is a valid default."""
        correction = self.correction('res.partner', 'tz', ['default'], default_value='Europe/Paris')
        self.assertEqual(correction.state, 'draft')
        with self.assertRaises(ValidationError):
            self.correction('res.partner', 'tz', ['default'], default_value='Not/AZone')

    def correction_action(self, words):
        return choice(self.env, 'data.quality.correction', 'action', words)
