from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError

from .acceptance_m3_helpers import M3Case


class TestAutomationActions(M3Case):
    """Unit tests of T-026 beside its acceptance tests: retries, corrections scope, escalation history.

    Requirements: FR-DGQ-0067, FR-DGQ-0068, FR-DGQ-0069
    """

    def setUp(self):
        super().setUp()
        self.lee = self.user('manager', login='dgq_m3_unit_lee', company=self.cx)
        self.ann = self.user('operator', login='dgq_m3_unit_ann', company=self.cx)
        self.team = self.make_team('Purchasing', leader=self.lee, members=[self.ann, self.lee])

    def run_once(self, automation):
        self.make_due(automation)
        self.advance(3)

    def trim_rule(self):
        return self.make_rule(
            'res.partner', 'pattern', ['name'], name='Name without outer spaces', pattern=r'^\S(.*\S)?$')

    def test_a_critical_correction_cannot_be_approved_for_automatic_use(self):
        """FR-DGQ-0068: a correction that needs a person's approval is refused for automatic use."""
        rule = self.make_rule(
            'res.partner', 'pattern', ['name'], name='Strict names', pattern=r'^\S(.*\S)?$', severity='critical')
        correction = self.correction('res.partner', 'name', ['trim'], rule=rule)
        with self.assertRaises(ValidationError):
            correction.with_user(self.manager_a).write({'auto_approved': True})

    def test_a_correction_without_rule_cannot_be_attached(self):
        """FR-DGQ-0068: an automation refuses an approved correction that is linked to no rule."""
        correction = self.correction('res.partner', 'name', ['trim'])
        correction.with_user(self.manager_a).write({'auto_approved': True})
        automation = self.make_automation('Nightly trim', rules=[self.trim_rule()])
        with self.assertRaises(ValidationError):
            automation.sudo().write({'correction_ids': [Command.link(correction.id)]})

    def test_a_correction_changes_only_the_records_the_scan_found(self):
        """FR-DGQ-0068: a correction whose filter reaches other records leaves those out of an automation run."""
        rule = self.trim_rule()
        partners = self.env['res.partner'].create([
            {'name': '  Inside %s  ' % n, 'company_id': self.cx.id} for n in range(2)])
        outside = self.env['res.partner'].create({'name': '  Outside  ', 'company_id': self.cy.id})
        correction = self.correction(
            'res.partner', 'name', ['trim'], rule=rule, domain="[('id', '>', 0)]",
            company_ids=[Command.clear()])
        self.dry_run(correction)
        correction.with_user(self.manager_b).write({'auto_approved': True})
        automation = self.make_automation(
            'Nightly trim', rules=[rule], correction_ids=[correction])
        self.run_once(automation)
        partners.invalidate_recordset()
        outside.invalidate_recordset()
        self.assertEqual(partners.mapped('name'), ['Inside 0', 'Inside 1'])
        self.assertEqual(outside.name, '  Outside  ')

    def test_a_failed_scan_is_resumed_not_started_again(self):
        """FR-DGQ-0069: the next due time resumes the failed scan; its success closes the same run."""
        rule = self.vendor_rule()
        self.vendors(2)
        automation = self.make_automation('Nightly contacts', rules=[rule])
        Scan = type(self.env['data.quality.scan'])

        def boom(this):
            raise RuntimeError('simulated failure')

        with patch.object(Scan, '_dgq_run_batch', boom):
            self.run_once(automation)
        run = automation.sudo().run_ids
        self.assertEqual((run.state, run.retry_count), ('running', 1))
        self.run_once(automation)
        run = automation.sudo().run_ids
        self.assertEqual(len(run), 1, 'the retry uses the same run')
        self.assertEqual(run.state, 'done')
        self.assertEqual(automation.sudo().retry_count, 0)

    def test_the_escalation_is_recorded_in_the_history(self):
        """FR-DGQ-0067, FR-DGQ-0069: the escalation check leaves a run with the number of issues escalated."""
        rule = self.vendor_rule()
        automation = self.make_automation('Escalating', rules=[rule], team_id=self.team, escalation_days=1)
        issue = self.issue_for(rule, self.vendors(1))
        issue.write({
            'team_id': self.team.id, 'deadline': fields.Date.add(fields.Date.today(), days=-1)})
        self.advance(1)
        runs = automation.sudo().run_ids
        self.assertEqual(runs.mapped('issues_escalated'), [1])
        self.assertEqual(runs.state, 'done')

    def boom_scan(self):
        def boom(this):
            raise RuntimeError('simulated failure')
        return patch.object(type(self.env['data.quality.scan']), '_dgq_run_batch', boom)

    def test_a_refused_resume_uses_a_retry_and_the_run_ends_failed(self):
        """FR-DGQ-0069: a failed scan whose rule was switched off is refused at each due time; the run fails with a notice."""
        rule = self.vendor_rule()
        self.vendors(2)
        automation = self.make_automation('Nightly contacts', rules=[rule])
        with self.boom_scan():
            self.run_once(automation)
        rule.sudo().write({'active': False})
        base = self.baseline()
        for _night in range(2):
            self.run_once(automation)
            self.assertEqual(automation.sudo().run_ids.state, 'running')
        self.run_once(automation)
        run = automation.sudo().run_ids
        self.assertEqual((run.state, run.retry_count), ('failed', 3))
        self.assertGreater(self.notices(self.administrator, base), 0, 'the administrators are notified')

    def test_switching_an_automation_off_fails_its_waiting_run_without_a_notice(self):
        """FR-DGQ-0069: a run waiting for a retry of an automation that is switched off is closed."""
        rule = self.vendor_rule()
        self.vendors(2)
        automation = self.make_automation('Nightly contacts', rules=[rule])
        with self.boom_scan():
            self.run_once(automation)
        base = self.baseline()
        automation.sudo().write({'active': False})
        run = automation.sudo().run_ids
        self.assertEqual(run.state, 'failed')
        self.assertEqual(self.notices(self.administrator, base), 0)

    def test_a_cancelled_scan_fails_its_run_without_a_notice(self):
        """FR-DGQ-0069: administrators are not told about a scan a person cancelled."""
        rule = self.vendor_rule()
        automation = self.make_automation('Nightly contacts', rules=[rule])
        stop, _busy = automation.sudo()._dgq_launch()
        self.assertFalse(stop)
        run = automation.sudo().run_ids
        base = self.baseline()
        run.scan_id.action_cancel()
        self.assertEqual(run.state, 'failed')
        self.assertEqual(self.notices(self.administrator, base), 0)

    def test_a_change_of_mappings_withdraws_the_approval_for_automatic_use(self):
        """FR-DGQ-0068: editing, adding or removing a mapping clears the approval for automatic use."""
        rule = self.trim_rule()
        correction = self.correction('res.partner', 'name', ['trim'], rule=rule)
        Mapping = self.env['data.quality.correction.mapping'].sudo()
        mapping = Mapping.create({'correction_id': correction.id, 'source_text': 'Deutschland'})
        for change in (
                lambda: mapping.write({'source_text': 'Allemagne'}),
                lambda: Mapping.create({'correction_id': correction.id, 'source_text': 'Spain'}),
                lambda: mapping.unlink()):
            self.dry_run(correction)
            correction.with_user(self.manager_b).write({'auto_approved': True})
            self.assertTrue(correction.auto_approved)
            change()
            correction.invalidate_recordset()
            self.assertFalse(correction.auto_approved)

    def late_issue(self, team, days_late=2):
        rule = self.vendor_rule()
        issue = self.issue_for(rule, self.vendors(1))
        issue.write({'team_id': team.id, 'deadline': fields.Date.add(fields.Date.today(), days=-days_late)})
        return issue

    def test_a_team_without_automation_is_escalated_with_the_delay_of_the_settings(self):
        """FR-DGQ-0067: an issue of a team that no automation covers is escalated after the settings' delay."""
        issue = self.late_issue(self.team)
        Parameter = self.env['ir.config_parameter'].sudo()
        Parameter.set_int('data_quality.escalation_days', 3)
        self.advance(1)
        issue.invalidate_recordset()
        self.assertFalse(issue.escalated_deadline, 'two days late is within the delay of 3')
        Parameter.set_int('data_quality.escalation_days', 1)
        base = self.baseline()
        self.advance(1)
        issue.invalidate_recordset()
        self.assertEqual(issue.escalated_deadline, issue.deadline)
        self.assertEqual(issue.priority, 'high')
        self.assertGreater(self.notices(self.lee, base), 0, 'the team leader is told')

    def test_the_automation_delay_wins_over_the_settings(self):
        """FR-DGQ-0067: an automation with its own delay escalates its team's issues by it, 0 uses the settings."""
        rule = self.vendor_rule()
        automation = self.make_automation('Escalating', rules=[rule], team_id=self.team, escalation_days=3)
        issue = self.late_issue(self.team)
        self.advance(1)
        issue.invalidate_recordset()
        self.assertFalse(issue.escalated_deadline)
        automation.sudo().write({'escalation_days': 0})
        self.advance(1)
        issue.invalidate_recordset()
        self.assertEqual(issue.escalated_deadline, issue.deadline)

    def test_a_failing_follow_up_keeps_the_run_done(self):
        """FR-DGQ-0066: an e-mail that cannot be built is logged and noted on the run; the run stays done."""
        rule = self.vendor_rule()
        self.vendors(2)
        automation = self.make_automation(
            'Follow up', rules=[rule], team_id=self.team, send_email=True)
        Run = type(self.env['data.quality.automation.run'])

        def broken(this, user, issues):
            raise RuntimeError('mail server down')

        with patch.object(Run, '_dgq_send_summary', broken):
            self.run_once(automation)
        run = automation.sudo().run_ids
        self.assertEqual(run.state, 'done')
        self.assertTrue(run.message)

    def test_a_correction_whose_start_is_refused_is_left_out_with_the_reason(self):
        """FR-DGQ-0068: a refused start of an approved correction is noted on the run, which still ends done."""
        rule = self.trim_rule()
        self.env['res.partner'].create({'name': '  Padded  ', 'company_id': self.cx.id})
        correction = self.correction('res.partner', 'name', ['trim'], rule=rule)
        self.dry_run(correction)
        correction.with_user(self.manager_b).write({'auto_approved': True})
        automation = self.make_automation('Nightly trim', rules=[rule], correction_ids=[correction])
        CorrectionRun = type(self.env['data.quality.correction.run'])

        def refuse(this):
            raise UserError('No finished dry run.')

        with patch.object(CorrectionRun, 'action_start', refuse):
            self.run_once(automation)
        run = automation.sudo().run_ids
        self.assertEqual(run.state, 'done')
        self.assertIn('No finished dry run.', run.message)
