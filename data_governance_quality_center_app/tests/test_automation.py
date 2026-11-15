from datetime import datetime

from .acceptance_m3_helpers import M3Case


class TestAutomation(M3Case):
    """Own tests of T-025: due times, assignment fallbacks, busy scopes.

    Requirements: FR-DGQ-0063, FR-DGQ-0064, FR-DGQ-0065
    """

    def automation(self, **vals):
        return self.env['data.quality.automation'].sudo().create(dict(
            {'name': 'Auto', 'trigger': 'schedule', 'interval': 'daily', 'hour': 2}, **vals))

    def test_next_run_of_each_interval(self):
        """FR-DGQ-0063: the next due time follows the interval and the chosen hour."""
        after = datetime(2026, 11, 15, 10, 30)  # a Sunday
        hourly = self.automation(interval='hourly')
        self.assertEqual(hourly._dgq_next_run(after), datetime(2026, 11, 15, 11, 0))
        daily = self.automation(interval='daily', hour=2)
        self.assertEqual(daily._dgq_next_run(after), datetime(2026, 11, 16, 2, 0))
        self.assertEqual(daily._dgq_next_run(datetime(2026, 11, 15, 1, 0)), datetime(2026, 11, 15, 2, 0))
        weekly = self.automation(interval='weekly', weekday='2', hour=6)
        self.assertEqual(weekly._dgq_next_run(after), datetime(2026, 11, 18, 6, 0))
        monthly = self.automation(interval='monthly', monthday=10, hour=3)
        self.assertEqual(monthly._dgq_next_run(after), datetime(2026, 12, 10, 3, 0))
        self.assertEqual(monthly._dgq_next_run(datetime(2026, 12, 20, 0, 0)), datetime(2027, 1, 10, 3, 0))

    def test_a_busy_scope_skips_the_scheduled_run(self):
        """FR-DGQ-0063 (SC-DGQ-0018-3): a run whose scope is busy is recorded as skipped, with the reason."""
        rule = self.vendor_rule()
        self.vendors(1)
        automation = self.make_automation(rules=[rule])
        busy = self.env['data.quality.scan'].sudo().create({
            'rule_ids': [(6, 0, rule.ids)], 'company_ids': [(6, 0, self.cx.ids)]})
        busy.action_start()
        self.make_due(automation)
        automation.sudo()._dgq_run_due()
        run = automation.sudo().run_ids
        self.assertEqual(run.state, 'skipped')
        self.assertTrue(run.skip_reason)
        self.assertFalse(run.scan_id)

    def test_event_rows_stay_queued_while_the_scope_is_busy(self):
        """FR-DGQ-0064: an event scan of a busy scope waits: its rows stay in the queue."""
        rule = self.vendor_rule()
        self.make_automation('On creation', rules=[rule], trigger='event', event='create')
        busy = self.env['data.quality.scan'].sudo().create({
            'rule_ids': [(6, 0, rule.ids)], 'company_ids': [(6, 0, self.cx.ids)]})
        busy.action_start()
        partner = self.vendors(1)
        self.env['data.quality.event.queue']._dgq_process()
        queue = self.env['data.quality.event.queue'].sudo().search([('res_id', '=', partner.id)])
        self.assertEqual(len(queue), 1)

    def test_the_leader_gets_the_issues_without_a_chosen_member(self):
        """FR-DGQ-0065: with no member chosen for the model, the leader is assigned."""
        team = self.make_team('Purchasing', leader=self.operator, members=[self.operator])
        rule = self.vendor_rule()
        vendors = self.vendors(2)
        automation = self.make_automation('Vendors', rules=[rule], team_id=team)
        self.make_due(automation)
        self.advance(3)
        issues = self.issues_of(rule, vendors)
        self.assertEqual(issues.user_id, self.operator)
        self.assertEqual(set(issues.mapped('state')), {'assigned'})

    def test_a_user_without_operator_access_is_not_assigned(self):
        """FR-DGQ-0065: the chosen person cannot take the issues: they stay New for the team."""
        team = self.make_team('Purchasing', members=[self.viewer], assignments=[('res.partner', self.viewer)])
        rule = self.vendor_rule()
        vendors = self.vendors(1)
        automation = self.make_automation('Vendors', rules=[rule], team_id=team)
        self.make_due(automation)
        self.advance(3)
        issue = self.issues_of(rule, vendors)
        self.assertEqual(issue.team_id, team)
        self.assertFalse(issue.user_id)
        self.assertEqual(issue.state, 'new')

    def test_a_model_without_event_automation_queues_nothing(self):
        """FR-DGQ-0064: a save on a model nobody watches does not touch the queue."""
        rule = self.vendor_rule()
        self.make_automation('On users', rules=[rule], models=('res.users',), trigger='event', event='create')
        partner = self.vendors(1)
        self.assertFalse(self.env['data.quality.event.queue'].sudo().search([('res_id', '=', partner.id)]))
