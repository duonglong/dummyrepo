from odoo.tests import HttpCase, tagged

SCREEN_SCRIPT = """
(async () => {
    try {
        const waitFor = async (selector) => {
            for (let i = 0; i < 100; i++) {
                const found = document.querySelector(selector);
                if (found) {
                    return found;
                }
                await new Promise((resolve) => setTimeout(resolve, 100));
            }
            throw new Error("not found: " + selector);
        };
        %(steps)s
        console.log("test successful");
    } catch (error) {
        console.error(error.message || String(error));
    }
})()
"""

KANBAN_STEPS = """
        const switcher = await waitFor(".o_switch_view.o_kanban");
        switcher.click();
        await waitFor(".o_kanban_renderer .o_kanban_group");
        await new Promise((resolve) => setTimeout(resolve, 500));
        const columns = document.querySelectorAll(".o_kanban_renderer .o_kanban_group").length;
        if (columns !== 5) {
            throw new Error("expected one kanban column per status, found " + columns);
        }
"""

EMPTY_ANALYSIS_STEPS = """
        const empty = await waitFor(".o_view_nocontent");
        if (empty.querySelector(".o_view_nocontent_empty_folder, .o_nocontent_help button")
                || /create new document/i.test(empty.textContent)) {
            throw new Error("the empty analysis offers to create a document: " + empty.textContent.trim());
        }
"""

ANALYSIS_STEPS = """
        await waitFor(".o_pivot thead .o_pivot_header_cell_opened");
"""


@tagged('post_install', '-at_install')
class TestIssueScreens(HttpCase):
    """The Issues screen offers a kanban by status and its analysis explains an empty result.

    Requirements: FR-DGQ-0025, FR-DGQ-0029
    Each test opens the action in a headless browser as the administrator. They do not drag a card
    between columns and do not open the analysis filters: only what the first render shows is checked.
    """

    def setUp(self):
        super().setUp()
        rule = self.env['data.quality.rule'].create({
            'name': 'DGQ screen rule',
            'model_id': self.env['ir.model']._get('res.partner').id,
            'rule_type': 'required',
            'field_ids': [(6, 0, [self.env['ir.model.fields']._get('res.partner', 'name').id])],
            'severity': 'warning',
        })
        self.rule = rule

    def _issue(self, state, **vals):
        partner = self.env['res.partner'].create({'name': 'DGQ screen partner'})
        return self.env['data.quality.issue'].create({
            'rule_id': self.rule.id, 'res_model': 'res.partner', 'res_id': partner.id,
            'company_id': self.env.company.id, 'severity': 'warning', 'state': state, **vals})

    def _open(self, action, steps):
        url = '/odoo/action-data_governance_quality_center_app.%s' % action
        self.browser_js(url, SCREEN_SCRIPT % {'steps': steps}, login='admin', timeout=120)

    def test_bug_b_0233_issues_switch_to_a_kanban_with_a_column_per_status(self):
        """Regression test for B-0233: the Issues screen has a kanban view with a column for each status.

        Requirements: FR-DGQ-0025
        """
        self._issue('new')
        self._open('issue_action', KANBAN_STEPS)
