from odoo.tests import HttpCase, tagged

from .acceptance_helpers import AcceptanceCase

FORM_SCRIPT = """
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
        const press = async (name) => {
            const button = await waitFor(".o_form_statusbar button[name=" + name + "]");
            button.click();
        };
        await waitFor(".o_form_view .o_form_statusbar");
        %(steps)s
        console.log("test successful");
    } catch (error) {
        console.error(error.message || String(error));
    }
})()
"""

START_STEPS = """
        await press("action_start");
        await waitFor(".o_form_statusbar button[name=action_resolve]");
        await waitFor(".o_form_statusbar:not(:has(button[name=action_start]))");
"""

RESOLVE_STEPS = """
        await press("action_resolve");
        await waitFor(".o_form_statusbar button[name=action_reopen]");
"""

REOPEN_STEPS = """
        await press("action_reopen");
        await waitFor(".o_form_statusbar button[name=action_resolve]");
"""

IGNORE_STEPS = """
        await press("action_ignore");
        const reason = await waitFor(".modal textarea");
        reason.value = "Accepted by the data owner";
        reason.dispatchEvent(new Event("input", {bubbles: true}));
        reason.dispatchEvent(new Event("change", {bubbles: true}));
        (await waitFor(".modal button[name=action_apply]")).click();
        await waitFor(".modal .o_field_widget[name=result_message]");
"""


@tagged('post_install', '-at_install')
class TestIssueFormStatus(AcceptanceCase, HttpCase):
    """An operator moves an issue through its statuses from the issue form.

    Requirements: FR-DGQ-0025
    Each test opens the issue form in a headless browser as an operator and presses the status button.
    They do not check the layout of the buttons or the message thread.
    """

    def setUp(self):
        super().setUp()
        self.operator = self.user('operator', login='dgq_form_operator', company=self.cx)
        rule = self.make_rule('res.partner', 'required', ['name'])
        partner = self.partner('DGQ form partner')
        self.issue = self.env['data.quality.issue'].create({
            'rule_id': rule.id, 'res_model': 'res.partner', 'res_id': partner.id,
            'company_id': self.cx.id, 'severity': 'warning', 'record_name': partner.name})

    def _press(self, steps):
        url = '/odoo/action-data_governance_quality_center_app.issue_action/%s' % self.issue.id
        self.browser_js(
            url, FORM_SCRIPT % {'steps': steps}, login=self.operator.login, timeout=120)
        self.issue.invalidate_recordset()

    def test_bug_b_0235_the_form_starts_the_work(self):
        """Regression test for B-0235: In Progress on the form moves a New issue to In Progress."""
        self._press(START_STEPS)
        self.assertEqual(self.issue.state, 'in_progress')

    def test_bug_b_0235_the_form_resolves(self):
        """Regression test for B-0235: Resolve on the form moves the issue to Resolved and dates it."""
        self._press(RESOLVE_STEPS)
        self.assertEqual(self.issue.state, 'resolved')
        self.assertTrue(self.issue.date_resolved)

    def test_bug_b_0235_the_form_reopens(self):
        """Regression test for B-0235: Reopen on the form moves a Resolved issue back to New."""
        self.issue.write({'state': 'resolved'})
        self._press(REOPEN_STEPS)
        self.assertEqual(self.issue.state, 'new')

    def test_bug_b_0235_the_form_ignores_with_a_reason(self):
        """Regression test for B-0235: Ignore on the form asks for the reason, then ignores the issue."""
        self._press(IGNORE_STEPS)
        self.assertEqual(self.issue.state, 'ignored')
        self.assertEqual(self.issue.ignore_reason, 'Accepted by the data owner')
