from odoo import SUPERUSER_ID, Command

from .acceptance_m3_helpers import M3Case


class TestAutomationRequester(M3Case):
    """The work an automation starts belongs to the automation's responsible user, not to the scheduler.

    One row per place that starts a scan or a correction run for an automation: the scheduled start,
    the event start, the retry of a failed scan and the approved correction. The scheduled action
    runs as the superuser, so a requester of OdooBot is the symptom.

    Requirements: FR-DGQ-0072, FR-DGQ-0063, FR-DGQ-0064, FR-DGQ-0068
    """

    def audit_users(self, event, record):
        return self.env['data.quality.audit'].sudo().search([
            ('event', '=', event), ('res_model', '=', record._name), ('res_id', '=', record.id)]).user_id

    def test_bug_b_0223_a_scheduled_scan_is_requested_by_the_responsible_user(self):
        """Regression test for B-0223: the scan of a due automation names the user who set it up.

        Requirements: FR-DGQ-0072, FR-DGQ-0063
        """
        rule = self.make_rule('res.partner', 'required', ['x_dgq_born_on'])
        self.partner('Born unknown')
        automation = self.make_automation('Nightly', user=self.manager_a, rules=[rule])
        self.assertEqual(automation.user_id, self.manager_a)
        self.make_due(automation)
        self.advance(3)
        scan = automation.sudo().run_ids.scan_id
        self.assertEqual(scan.sudo().user_id, self.manager_a)
        self.assertEqual(self.audit_users('scan_started', scan), self.manager_a)
        self.assertEqual(self.audit_users('scan_ended', scan), self.manager_a)

    def test_bug_b_0223_an_event_scan_is_requested_by_the_responsible_user(self):
        """Regression test for B-0223: the scan an event starts names the automation's user, not the saver's.

        Requirements: FR-DGQ-0072, FR-DGQ-0064
        """
        rule = self.vendor_rule()
        automation = self.make_automation(
            'On creation', user=self.manager_a, rules=[rule], trigger='event', event='create')
        self.vendors(1)
        self.env['data.quality.event.queue']._dgq_process()
        scan = automation.sudo().run_ids.scan_id
        self.assertTrue(scan)
        self.assertEqual(scan.sudo().user_id, self.manager_a)
        self.assertEqual(self.audit_users('scan_started', scan), self.manager_a)

    def test_bug_b_0223_a_retried_scan_keeps_the_responsible_user(self):
        """Regression test for B-0223: the retry of a failed scan does not hand it to the scheduler.

        Requirements: FR-DGQ-0072, FR-DGQ-0069
        """
        rule = self.make_rule('res.partner', 'required', ['x_dgq_born_on'])
        self.partner('Born unknown')
        automation = self.make_automation('Nightly', user=self.manager_a, rules=[rule])
        self.make_due(automation)
        scheduler = automation.with_user(SUPERUSER_ID)
        scheduler._dgq_run_due()
        run = scheduler.run_ids
        run.scan_id.write({'state': 'failed'})
        scheduler._dgq_retry(run)
        scan = run.scan_id
        self.assertEqual(scan.state, 'queued')
        self.assertEqual(scan.user_id, self.manager_a)
        self.assertEqual(self.audit_users('scan_started', scan), self.manager_a)

    def test_bug_b_0223_an_automatic_correction_is_run_by_the_responsible_user(self):
        """Regression test for B-0223: the correction run an automation starts, and its entries, name the user.

        Requirements: FR-DGQ-0072, FR-DGQ-0068
        """
        rule = self.make_rule('res.partner', 'pattern', ['name'], pattern=r'^\S(.*\S)?$', name='Trimmed names')
        self.env['res.partner'].create({'name': '  Padded  ', 'company_id': self.cx.id})
        correction = self.correction('res.partner', 'name', ['trim'], rule=rule, domain="[('id', '>', 0)]",
                                     company_ids=[Command.clear()])
        self.dry_run(correction)
        correction.with_user(self.manager_b).write({'auto_approved': True})
        automation = self.make_automation(
            'Nightly trim', user=self.manager_a, rules=[rule], correction_ids=[correction])
        self.make_due(automation)
        self.advance(3)
        run = correction.sudo().run_ids.filtered(lambda r: r.kind == 'run')
        self.assertTrue(run)
        self.assertEqual(run.user_id, self.manager_a)
        entries = self.env['data.quality.audit'].sudo().search([
            ('event', '=', 'correction_run'), ('automation_id', '=', automation.id)])
        self.assertTrue(entries)
        self.assertEqual(entries.user_id, self.manager_a)
