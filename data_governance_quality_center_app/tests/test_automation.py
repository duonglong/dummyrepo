from datetime import datetime
from unittest.mock import patch

from odoo.exceptions import AccessError

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

    # ---- review round: other workers, scope of an event, bounded work, companies ----

    def test_saving_an_event_automation_tells_the_other_workers(self):
        """FR-DGQ-0064: creating, changing or deleting an event automation flags the registry for every worker."""
        rule = self.vendor_rule()
        base = 'odoo.orm.environments.Transaction.will_change_registry'
        with patch(base) as flagged:
            automation = self.make_automation('On creation', rules=[rule], trigger='event', event='create')
            self.assertTrue(flagged.called, 'a new event automation')
        with patch(base) as flagged:
            automation.sudo().active = False
            self.assertTrue(flagged.called, 'switching it off')
        with patch(base) as flagged:
            automation.sudo().name = 'Renamed'
            self.assertFalse(flagged.called, 'a change that leaves the watched models alone')
        with patch(base) as flagged:
            automation.sudo().unlink()
            self.assertTrue(flagged.called, 'deleting it')

    def test_an_event_does_not_run_rules_the_automation_did_not_choose(self):
        """FR-DGQ-0064: a saved product of an automation whose only rule is about contacts starts no scan."""
        rule = self.vendor_rule()
        self.make_automation(
            'Two models', rules=[rule], models=('res.partner', 'product.template'), trigger='event', event='create')
        product = self.env['product.template'].create({'name': 'Gadget'})
        Queue = self.env['data.quality.event.queue'].sudo()
        self.assertTrue(Queue.search([('res_model', '=', 'product.template'), ('res_id', '=', product.id)]))
        scans_before = self.env['data.quality.scan'].sudo().search_count([('trigger', '=', 'event')])
        Queue._dgq_process()
        self.assertEqual(self.env['data.quality.scan'].sudo().search_count([('trigger', '=', 'event')]), scans_before)
        self.assertFalse(Queue.search([('res_model', '=', 'product.template')]), 'the waiting row is dropped')

    def test_a_model_removed_from_the_automation_drops_its_waiting_rows(self):
        """FR-DGQ-0064: rows of a model the automation no longer covers start no scan on unrelated records."""
        rule = self.vendor_rule()
        automation = self.make_automation('On creation', rules=[rule], trigger='event', event='create')
        Queue = self.env['data.quality.event.queue'].sudo()
        Queue.create({'automation_id': automation.id, 'res_model': 'res.users', 'res_id': self.operator.id})
        scans_before = self.env['data.quality.scan'].sudo().search_count([('trigger', '=', 'event')])
        Queue._dgq_process()
        self.assertEqual(self.env['data.quality.scan'].sudo().search_count([('trigger', '=', 'event')]), scans_before)
        self.assertFalse(Queue.search([('res_model', '=', 'res.users')]))

    def test_the_queue_is_read_one_batch_at_a_time(self):
        """NFR-DGQ-0003: a run takes at most one batch of a group; the other rows wait for the next run."""
        rule = self.vendor_rule()
        self.make_automation('On creation', rules=[rule], trigger='event', event='create')
        vendors = self.vendors(5)
        Queue = self.env['data.quality.event.queue'].sudo()
        with patch('odoo.addons.data_governance_quality_center_app.models.event_queue.JOB_BATCH_SIZE', 2):
            Queue._dgq_process()
        waiting = Queue.search([('res_id', 'in', vendors.ids)])
        self.assertEqual(len(waiting), 3)
        scan = self.env['data.quality.scan'].sudo().search([('trigger', '=', 'event')], order='id desc', limit=1)
        self.assertEqual(len(scan.res_ids), 2)
        self.assertFalse(set(scan.res_ids) & set(waiting.mapped('res_id')))

    def test_assignment_follows_each_batch_of_the_scan(self):
        """FR-DGQ-0065: the issues of every batch go to the team as the batch creates them."""
        ann = self.user('operator', login='dgq_m3_batch_ann', company=self.cx)
        team = self.make_team('Purchasing', members=[ann], assignments=[('res.partner', ann)])
        rule = self.vendor_rule()
        vendors = self.vendors(5)
        automation = self.make_automation('Nightly vendors', rules=[rule], team_id=team)
        self.make_due(automation)
        with patch('odoo.addons.data_governance_quality_center_app.models.scan.JOB_BATCH_SIZE', 2):
            self.advance(6)
        issues = self.issues_of(rule, vendors)
        self.assertEqual(len(issues), 5)
        self.assertEqual(issues.team_id, team)
        self.assertEqual(issues.user_id, ann)
        self.assertEqual(automation.sudo().run_ids.issues_assigned, 5)

    def test_a_viewer_of_another_company_does_not_read_the_assignments_of_a_team(self):
        """FR-DGQ-0071: the per-model assignments of a company's team are hidden from another company's viewer."""
        ann = self.user('operator', login='dgq_m3_company_ann', company=self.cx)
        team = self.make_team('Purchasing', members=[ann], assignments=[('res.partner', ann)])
        stranger = self.user('viewer', login='dgq_m3_company_stranger', company=self.cy)
        Assignment = self.env['data.quality.team.assignment']
        self.assertFalse(Assignment.with_user(stranger).search([('team_id', '=', team.id)]))
        with self.assertRaises(AccessError):
            team.assignment_ids.with_user(stranger).user_id
