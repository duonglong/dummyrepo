from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import ValidationError

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
