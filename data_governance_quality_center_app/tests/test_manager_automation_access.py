from odoo import Command
from odoo.exceptions import AccessError

from .acceptance_m3_helpers import M3Case

MODULE = 'data_governance_quality_center_app'


class TestManagerAutomationAccess(M3Case):
    """Regression tests for B-0217: a Manager manages automations, teams and the run history.

    The access rows and the menus are what the server checks; the screens themselves are client
    side, so a menu is judged by the groups it is shown to.

    Requirements: FR-DGQ-0070, FR-DGQ-0065, FR-DGQ-0069
    """

    def setUp(self):
        super().setUp()
        self.manager = self.user('manager', login='dgq_b0217_manager', company=self.cx)

    def test_bug_b_0217_manager_creates_changes_and_deletes_an_automation(self):
        """B-0217: a manager of company X creates, changes and deletes an automation of X."""
        automation = self.make_automation(user=self.manager)
        automation.with_user(self.manager).write({'name': 'Renamed'})
        self.assertEqual(automation.name, 'Renamed')
        automation.with_user(self.manager).unlink()
        self.assertFalse(automation.exists())

    def test_bug_b_0217_manager_creates_a_team_with_its_assignments(self):
        """B-0217: a manager creates and changes a team and the member chosen for a model."""
        team = self.env['data.quality.team'].with_user(self.manager).create({
            'name': 'Purchasing', 'company_id': self.cx.id,
            'assignment_ids': [Command.create({
                'model_id': self.model('res.partner').id, 'user_id': self.operator.id})],
        })
        team.write({'name': 'Purchasing 2'})
        team.assignment_ids.write({'user_id': self.manager.id})
        team.unlink()
        self.assertFalse(team.exists())

    def test_bug_b_0217_manager_reads_the_run_history_but_cannot_write_it(self):
        """B-0217: the run history of an automation is readable by a manager and stays read-only."""
        automation = self.make_automation()
        run = self.env['data.quality.automation.run'].sudo().create({'automation_id': automation.id})
        as_manager = run.with_user(self.manager)
        self.env.invalidate_all()
        self.assertEqual(as_manager.read(['automation_id'])[0]['automation_id'][0], automation.id)
        with self.assertRaises(AccessError):
            as_manager.write({'message': 'x'})
        with self.assertRaises(AccessError):
            as_manager.unlink()

    def test_bug_b_0217_menus_of_automations_teams_and_runs_are_shown_to_managers(self):
        """B-0217: the Automations, Teams and Automation History menus are open to the manager level."""
        manager_group = self.env.ref('%s.group_dgq_manager' % MODULE)
        for xml_id in ('menu_dgq_automation', 'menu_dgq_team', 'menu_dgq_automation_run'):
            menu = self.env.ref('%s.%s' % (MODULE, xml_id))
            self.assertIn(manager_group, menu.group_ids, xml_id)
            self.assertIn(menu, self.env['ir.ui.menu'].with_user(self.manager).search([]), xml_id)
