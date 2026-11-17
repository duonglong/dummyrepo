from psycopg2 import IntegrityError

from odoo.exceptions import ValidationError
from odoo.tools import mute_logger

from .acceptance_m3_helpers import M3Case


class TestAutomationAcceptance(M3Case):
    """Acceptance tests of T-025: scheduled scans, scans after business events, assignment to a team.

    An automation is filled in on its form and saved. A schedule is made due by writing its ``next_run``
    and the scheduled actions of the app are run the way the scheduler runs them (their server action),
    then the job runner works; the design names no method for either. The hook of an event automation
    queues the record in ``data.quality.event.queue`` (after the commit: the test runs the post-commit
    callbacks), and the background step turns the queue into an event scan.

    Requirements: FR-DGQ-0063, FR-DGQ-0064, FR-DGQ-0065
    """

    def setUp(self):
        super().setUp()
        self.Queue = self.env['data.quality.event.queue'].sudo()
        self.Scan = self.env['data.quality.scan'].sudo()

    def queued(self, partner):
        self.env.cr.postcommit.run()
        self.env.flush_all()
        return self.Queue.search([('res_model', '=', 'res.partner'), ('res_id', '=', partner.id)])

    # ---- FR-DGQ-0063 -------------------------------------------------------

    def test_a_due_schedule_starts_a_scan_and_a_run(self):
        """SC-DGQ-0063-1: the active 'Nightly contacts' (daily at 02:00) starts a scan of its rules for company A."""
        rule = self.vendor_rule()
        self.vendors(2)
        automation = self.make_automation(rules=[rule])
        self.make_due(automation)
        self.advance(1)
        scans = self.Scan.search([('trigger', '=', 'automation'), ('rule_ids', 'in', rule.id)])
        self.assertEqual(len(scans), 1)
        self.assertEqual(scans.company_ids, self.cx)
        self.assertEqual(scans.rule_ids, rule)
        runs = automation.sudo().run_ids
        self.assertEqual(len(runs), 1, 'the run appears in the automation history')
        self.assertEqual(runs.scan_id, scans)
        self.assertEqual(scans.automation_run_id, runs)
        self.assertTrue(runs.date_start)

    def test_a_schedule_does_not_start_twice_for_one_due_time(self):
        """FR-DGQ-0063: once the run has started, the next due time is later: another firing starts nothing."""
        rule = self.vendor_rule()
        self.vendors(1)
        automation = self.make_automation(rules=[rule])
        self.make_due(automation)
        self.advance(3)
        self.assertEqual(len(automation.sudo().run_ids), 1)

    def test_a_switched_off_automation_starts_nothing(self):
        """SC-DGQ-0063-2: 'Nightly contacts' switched off: nothing starts when 02:00 comes."""
        rule = self.vendor_rule()
        automation = self.make_automation(rules=[rule])
        self.make_due(automation)
        automation.sudo().active = False
        self.advance(2)
        self.assertFalse(self.Scan.search([('rule_ids', 'in', rule.id)]))
        self.assertFalse(automation.sudo().with_context(active_test=False).run_ids)

    def test_a_schedule_not_yet_due_starts_nothing(self):
        """FR-DGQ-0063 (just before the due time): a next run in the future starts nothing."""
        rule = self.vendor_rule()
        automation = self.make_automation(rules=[rule])
        self.make_not_due(automation)
        self.advance(2)
        self.assertFalse(self.Scan.search([('rule_ids', 'in', rule.id)]))

    def test_the_hour_is_between_0_and_23(self):
        """FR-DGQ-0063: hour 23 is accepted, hour 24 is refused."""
        Automation = self.env['data.quality.automation'].sudo()
        values = {'name': 'Hours', 'trigger': 'schedule', 'interval': 'daily'}
        Automation.create(dict(values, hour=23))
        with self.assertRaises(ValidationError):
            Automation.create(dict(values, hour=24))

    # ---- FR-DGQ-0064 -------------------------------------------------------

    def test_a_save_is_queued_and_checked_afterwards(self):
        """SC-DGQ-0064-1: creating the vendor 'Gamma' without VAT succeeds at once; shortly after it has an issue."""
        rule = self.make_rule(
            'res.partner', 'condition', applies_domain="[('function', '=', 'DGQVEND')]",
            condition_domain="[('vat', '!=', False)]", severity='critical', name='Vendor needs VAT')
        self.make_automation('On creation', rules=[rule], trigger='event', event='create')
        gamma = self.env['res.partner'].create(
            {'name': 'Gamma', 'function': 'DGQVEND', 'company_id': self.cx.id})
        self.assertTrue(gamma.id, 'the save succeeded')
        self.assertFalse(self.issues_of(rule, gamma), 'the check does not run inside the save')
        self.assertEqual(len(self.queued(gamma)), 1)
        self.advance(2)
        issues = self.issues_of(rule, gamma)
        self.assertEqual(len(issues), 1)
        event_scans = self.Scan.search([('trigger', '=', 'event'), ('rule_ids', 'in', rule.id)])
        self.assertTrue(event_scans)
        self.assertIn(gamma.id, [res_id for scan in event_scans for res_id in (scan.res_ids or [])])

    def test_many_saves_of_one_record_give_one_waiting_line(self):
        """FR-DGQ-0064: several saves of the same record in a short time queue one check."""
        rule = self.vendor_rule()
        self.make_automation(
            'On change', rules=[rule], trigger='event', event='write',
            watched_field_ids=[self.fld('res.partner', 'email')])
        partner = self.vendors(1)
        partner.email = 'first@example.com'
        partner.email = 'second@example.com'
        partner.email = 'third@example.com'
        self.assertEqual(len(self.queued(partner)), 1)

    def test_only_watched_fields_queue_a_check(self):
        """SC-DGQ-0064-2: an automation watching the e-mail queues nothing when the phone changes, and queues when the e-mail does."""
        rule = self.vendor_rule()
        self.make_automation(
            'On e-mail', rules=[rule], trigger='event', event='write',
            watched_field_ids=[self.fld('res.partner', 'email')])
        partner = self.vendors(1)
        self.queued(partner)  # the creation is not an event of this automation
        self.Queue.search([('res_id', '=', partner.id)]).unlink()
        partner.phone = '+32 2 555 01 01'
        self.assertFalse(self.queued(partner))
        partner.email = 'changed@example.com'
        self.assertEqual(len(self.queued(partner)), 1)

    # ---- FR-DGQ-0065 -------------------------------------------------------

    def run_vendor_automation(self, team, count=4):
        rule = self.vendor_rule()
        vendors = self.vendors(count)
        automation = self.make_automation('Nightly vendors', rules=[rule], team_id=team)
        self.make_due(automation)
        self.advance(3)
        return automation, rule, vendors

    def test_new_issues_go_to_the_team_and_its_chosen_member(self):
        """SC-DGQ-0065-1: the run of 'Nightly vendors' (team Purchasing, Ann chosen for Contact) assigns its 4 issues to Ann."""
        ann = self.user('operator', login='dgq_m3_team_ann', company=self.cx)
        team = self.make_team('Purchasing', members=[ann], assignments=[('res.partner', ann)])
        automation, rule, vendors = self.run_vendor_automation(team)
        issues = self.issues_of(rule, vendors)
        self.assertEqual(len(issues), 4)
        for issue in issues:
            self.assertEqual(issue.team_id, team)
            self.assertEqual(issue.user_id, ann)
            self.assertEqual(issue.state, 'assigned')
        self.assertEqual(automation.sudo().run_ids.issues_assigned, 4)

    def test_without_a_chosen_member_the_issues_stay_new_for_the_team(self):
        """FR-DGQ-0065: a team with nobody chosen for the model gets the issues, which stay New."""
        team = self.make_team('Purchasing', members=[self.operator])
        automation, rule, vendors = self.run_vendor_automation(team, count=2)
        issues = self.issues_of(rule, vendors)
        self.assertEqual(len(issues), 2)
        for issue in issues:
            self.assertEqual(issue.team_id, team)
            self.assertFalse(issue.user_id)
            self.assertEqual(issue.state, 'new')

    def test_a_team_has_one_chosen_member_per_model(self):
        """FR-DGQ-0065: a team names one person per kind of record."""
        ann = self.user('operator', login='dgq_m3_dup_ann', company=self.cx)
        team = self.make_team('Dup', members=[ann, self.operator], assignments=[('res.partner', ann)])
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.cr.savepoint():
            self.env['data.quality.team.assignment'].sudo().create({
                'team_id': team.id, 'model_id': self.model('res.partner').id, 'user_id': self.operator.id})
