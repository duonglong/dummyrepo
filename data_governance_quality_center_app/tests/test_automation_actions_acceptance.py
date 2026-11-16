from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import ValidationError

from .acceptance_m3_helpers import M3Case

NOTIFY_PARAMETER = 'data_quality.notify_email'


class TestAutomationActionsAcceptance(M3Case):
    """Acceptance tests of T-026: e-mails and activities, escalation, approved corrections, run history.

    As in T-025 an automation is filled in on its form, made due by its ``next_run`` and the scheduled
    actions of the app are run as the scheduler does, followed by the job runners. The escalation check,
    the corrections and the retries are steps of those scheduled actions: the design names no method for
    them. A correction is approved for automatic use by a manager writing ``auto_approved``. The
    figure of SC-DGQ-0069-2 (4 issues created, 5 corrected) is tested as the run reflecting its scan:
    the issues of the corrected records are counted by the scan as created.

    Requirements: FR-DGQ-0066, FR-DGQ-0067, FR-DGQ-0068, FR-DGQ-0069
    """

    def setUp(self):
        super().setUp()
        self.Scan = self.env['data.quality.scan'].sudo()
        self.ann = self.user('operator', login='dgq_m3_act_ann', company=self.cx)
        self.lee = self.user('manager', login='dgq_m3_act_lee', company=self.cx)
        for user in (self.ann, self.lee):
            user.write({'email': '%s@example.com' % user.login, 'notification_type': 'inbox'})
        self.team = self.make_team('Purchasing', leader=self.lee, members=[self.ann, self.lee],
                                   assignments=[('res.partner', self.ann)])

    def run_once(self, automation, nights=1):
        for _ in range(nights):
            self.make_due(automation)
            self.advance(3)

    # ---- FR-DGQ-0066 -------------------------------------------------------

    def run_with_follow_up(self, **vals):
        self.env['ir.config_parameter'].sudo().set_bool(NOTIFY_PARAMETER, False)
        rule = self.vendor_rule()
        vendors = self.vendors(4)
        automation = self.make_automation(
            'Follow up', rules=[rule], team_id=self.team, deadline_days=5, **vals)
        self.run_once(automation)
        return rule, vendors

    def test_one_e_mail_and_one_activity_per_issue(self):
        """SC-DGQ-0066-1: Ann gets one e-mail listing her 4 new issues and a to-do on each, due in 5 days."""
        base = self.baseline()
        rule, vendors = self.run_with_follow_up(send_email=True, create_activity=True)
        issues = self.issues_of(rule, vendors)
        self.assertEqual(len(issues), 4)
        self.assertEqual(issues.mapped('user_id'), self.ann)
        names = vendors.mapped('name')
        summaries = self.mails_to(self.ann, base).filtered(
            lambda mail: all(name in (mail.body_html or '') for name in names))
        self.assertEqual(len(summaries), 1, 'one e-mail lists the four issues')
        deadline = fields.Date.add(fields.Date.today(), days=5)
        activities = self.env['mail.activity'].sudo().search([
            ('res_model', '=', 'data.quality.issue'), ('res_id', 'in', issues.ids)])
        self.assertEqual(len(activities), 4)
        self.assertEqual(activities.mapped('user_id'), self.ann)
        self.assertEqual(set(activities.mapped('date_deadline')), {deadline})
        self.assertEqual(activities.mapped('activity_type_id'), self.env.ref('mail.mail_activity_data_todo'))
        self.assertEqual(set(issues.mapped('deadline')), {deadline})

    def test_nothing_is_sent_when_the_automation_does_not_send(self):
        """FR-DGQ-0066: with e-mails and activities off, neither is created."""
        base = self.baseline()
        rule, vendors = self.run_with_follow_up(send_email=False, create_activity=False)
        issues = self.issues_of(rule, vendors)
        self.assertEqual(len(issues), 4)
        names = vendors.mapped('name')
        self.assertFalse(self.mails_to(self.ann, base).filtered(
            lambda mail: any(name in (mail.body_html or '') for name in names)))
        self.assertFalse(self.env['mail.activity'].sudo().search_count([
            ('res_model', '=', 'data.quality.issue'), ('res_id', 'in', issues.ids)]))

    # ---- FR-DGQ-0067 -------------------------------------------------------

    def overdue_issue(self, days_late=2, delay=1):
        rule = self.vendor_rule()
        vendor = self.vendors(1)
        self.make_automation(
            'Escalating', rules=[rule], team_id=self.team, escalation_days=delay)
        issue = self.issue_for(rule, vendor)
        issue.write({
            'user_id': self.ann.id, 'team_id': self.team.id,
            'deadline': fields.Date.add(fields.Date.today(), days=-days_late)})
        self.assertEqual((issue.state, issue.priority), ('assigned', 'normal'))
        return issue

    def escalation_messages(self, issue):
        self.env.flush_all()
        issue.invalidate_recordset()
        return issue.message_ids.filtered(lambda message: 'escalat' in (message.body or '').lower())

    def test_an_overdue_issue_is_escalated(self):
        """SC-DGQ-0067-1: 2 days past its deadline with a delay of 1 day: the leader is told, priority high."""
        issue = self.overdue_issue()
        base = self.baseline()
        self.advance(2)
        self.assertGreater(self.notices(self.lee, base), 0, 'the team leader is notified')
        self.assertEqual(issue.priority, 'high')
        self.assertTrue(self.escalation_messages(issue), 'the issue shows the escalation')
        self.assertEqual(issue.escalated_deadline, issue.deadline)

    def test_an_issue_is_escalated_once_per_deadline(self):
        """SC-DGQ-0067-2: the next check sends nothing new and does not raise the priority again."""
        issue = self.overdue_issue()
        self.advance(2)
        notices = self.notices(self.lee)
        messages = len(self.escalation_messages(issue))
        self.advance(2)
        self.assertEqual(self.notices(self.lee), notices)
        self.assertEqual(len(self.escalation_messages(issue)), messages)
        issue.invalidate_recordset()
        self.assertEqual(issue.priority, 'high')

    def test_a_new_deadline_can_be_escalated_again(self):
        """FR-DGQ-0067: when the deadline is replaced by a later one that is also late, the issue is escalated again."""
        issue = self.overdue_issue(days_late=3)
        self.advance(2)
        issue.write({'deadline': fields.Date.add(fields.Date.today(), days=-2)})
        self.advance(2)
        issue.invalidate_recordset()
        self.assertEqual(issue.priority, 'urgent')

    def test_an_issue_not_late_enough_is_not_escalated(self):
        """FR-DGQ-0067 (just below the delay): a deadline of today is not escalated with a delay of 1 day."""
        issue = self.overdue_issue(days_late=0)
        base = self.baseline()
        self.advance(2)
        issue.invalidate_recordset()
        self.assertEqual(issue.priority, 'normal')
        self.assertFalse(self.escalation_messages(issue))
        self.assertEqual(self.notices(self.lee, base), 0)

    # ---- FR-DGQ-0068 -------------------------------------------------------

    def trim_setup(self, approved=True, count=5, severity='warning'):
        rule = self.make_rule(
            'res.partner', 'pattern', ['name'], name='Name without outer spaces',
            pattern=r'^\S(.*\S)?$', severity=severity)
        partners = self.env['res.partner'].create([
            {'name': '  Trim %s  ' % n, 'company_id': self.cx.id} for n in range(count)])
        self.assertTrue(all(partner.name != partner.name.strip() for partner in partners), 'names keep their spaces')
        correction = self.correction('res.partner', 'name', ['trim'], rule=rule)
        self.dry_run(correction)
        if approved:
            correction.with_user(self.manager_b).write({'auto_approved': True})
        automation = self.make_automation(
            'Nightly trim', rules=[rule], team_id=self.team,
            correction_ids=[correction] if approved else None)
        return rule, partners, correction, automation

    def test_an_approved_correction_runs_in_the_automation(self):
        """SC-DGQ-0068-1: the approved 'trim names' correction trims the 5 failing contacts, logs and resolves their issues."""
        rule, partners, correction, automation = self.trim_setup()
        self.run_once(automation)
        for partner in partners:
            partner.invalidate_recordset()
            self.assertEqual(partner.name, partner.name.strip())
            self.assertTrue(partner.name.startswith('Trim'))
        runs = correction.sudo().run_ids.filtered(lambda run: run.kind == 'run')
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs.count_changed, 5)
        self.assertEqual(len(runs.log_ids.filtered(lambda line: line.state == 'changed')), 5)
        self.assertEqual(runs.automation_run_id, automation.sudo().run_ids)
        issues = self.issues_of(rule, partners)
        self.assertEqual(len(issues), 5)
        self.assertEqual(set(issues.mapped('state')), {'resolved'})

    def test_a_correction_not_approved_for_automatic_use_does_not_run(self):
        """SC-DGQ-0068-2: a correction linked to the rule but not approved for automatic use never runs; the issues are created."""
        rule, partners, correction, automation = self.trim_setup(approved=False)
        self.assertFalse(correction.auto_approved)
        self.run_once(automation)
        self.assertFalse(correction.sudo().run_ids.filtered(lambda run: run.kind == 'run'))
        for partner in partners:
            partner.invalidate_recordset()
            self.assertTrue(partner.name.startswith('  Trim'))
        issues = self.issues_of(rule, partners)
        self.assertEqual(len(issues), 5)
        self.assertNotIn('resolved', issues.mapped('state'))

    def test_only_approved_corrections_can_be_attached_to_an_automation(self):
        """FR-DGQ-0068: an automation refuses a correction that is not approved for automatic use."""
        rule, partners, correction, automation = self.trim_setup(approved=False)
        with self.assertRaises(ValidationError):
            automation.sudo().write({'correction_ids': [Command.link(correction.id)]})

    def test_a_change_of_the_correction_withdraws_the_approval(self):
        """FR-DGQ-0068: the approval for automatic use is withdrawn when the correction changes."""
        rule, partners, correction, automation = self.trim_setup()
        self.assertTrue(correction.auto_approved)
        correction.with_user(self.manager_a).write({'domain': "[('id', '>', 0)]"})
        correction.invalidate_recordset()
        self.assertFalse(correction.auto_approved)

    # ---- FR-DGQ-0069 -------------------------------------------------------

    def test_a_run_fails_for_good_after_its_retries(self):
        """SC-DGQ-0069-1: a scan that fails on 3 nights is retried; when it fails a fourth time the run is failed and administrators are told."""
        rule = self.vendor_rule()
        self.vendors(2)
        automation = self.make_automation('Nightly contacts', rules=[rule])
        Scan = type(self.env['data.quality.scan'])

        def boom(this):
            raise RuntimeError('simulated failure')

        with patch.object(Scan, '_dgq_run_batch', boom):
            for _night in range(3):
                self.run_once(automation)
            runs = automation.sudo().run_ids
            self.assertTrue(runs)
            self.assertNotIn('failed', runs.mapped('state'), 'three failures are retried, not final')
            base = self.baseline()
            self.run_once(automation)
        runs = automation.sudo().run_ids
        self.assertIn('failed', runs.mapped('state'))
        self.assertGreater(self.notices(self.administrator, base), 0, 'the administrators are notified')

    def test_the_history_shows_what_a_run_did(self):
        """SC-DGQ-0069-2: an administrator sees the run's times, state done, its scan, its issues and its corrections."""
        rule, partners, correction, automation = self.trim_setup()
        other_rule = self.vendor_rule(name='Vendor reference')
        self.vendors(4)
        automation.sudo().write({'rule_ids': [Command.link(other_rule.id)]})
        self.run_once(automation)
        run = automation.with_user(self.administrator).run_ids
        self.assertEqual(len(run), 1)
        self.assertEqual(run.state, 'done')
        self.assertTrue(run.date_start and run.date_end)
        self.assertTrue(run.scan_id)
        self.assertEqual(run.issues_created, run.scan_id.sudo().issues_created)
        self.assertGreaterEqual(run.issues_created, 4)
        self.assertEqual(run.corrections_applied, 5)
