from odoo.tests import HttpCase, tagged

from .acceptance_helpers import AcceptanceCase

EMPTY_SCRIPT = """
(async () => {
    try {
        const waitFor = async (check, what) => {
            for (let i = 0; i < 100; i++) {
                const found = check();
                if (found) {
                    return found;
                }
                await new Promise((resolve) => setTimeout(resolve, 100));
            }
            throw new Error("not reached: " + what);
        };
        const date = await waitFor(() => document.querySelector(".o_dgq_dashboard input[type=date]"), "the date filter");
        date.value = "2000-01-01";
        date.dispatchEvent(new Event("change", { bubbles: true }));
        await waitFor(() => !document.querySelector(".o_dgq_dashboard .o_dgq_score"), "the score gone");
        const dashboard = document.querySelector(".o_dgq_dashboard");
        if (!dashboard.textContent.includes("No scan has run yet.")) {
            throw new Error("no empty message: " + dashboard.textContent);
        }
        if (![...dashboard.querySelectorAll("button")].some((button) => button.textContent.includes("Start a scan"))) {
            throw new Error("no Start a scan button: " + dashboard.textContent);
        }
        console.log("test successful");
    } catch (error) {
        console.error(error.message || String(error));
    }
})()
"""


@tagged('post_install', '-at_install')
class TestDashboardEmptyScreen(AcceptanceCase, HttpCase):
    """A date before the first scan shows the empty state, as no scan has run by then.

    Requirements: FR-DGQ-0032, FR-DGQ-0033
    The browser test opens the dashboard as an operator and types a past date. It does not reach the
    company switcher or the viewer's view without the button.
    """

    def setUp(self):
        super().setUp()
        self.operator = self.user('operator', login='dgq_empty_operator', company=self.cx)
        self.env['data.quality.score.snapshot'].search([]).unlink()
        self.env['data.quality.score.snapshot'].create({
            'model_id': self.model('res.partner').id, 'company_id': self.cx.id, 'date': '2026-11-08 10:00:00',
            'applicable_weight': 100, 'failed_weight': 10, 'has_score': True, 'score': 90.0})

    def figures(self, **kw):
        companies = self.operator.company_ids.ids
        return self.env['data.quality.dashboard'].with_user(self.operator).with_context(
            allowed_company_ids=companies).get_figures(**kw)

    def test_bug_b_0242_a_past_date_before_any_scan_is_empty(self):
        """Regression test for B-0242: a date before the first snapshot says no scan has run."""
        self.assertFalse(self.figures(date='2000-01-01')['has_scan'])
        self.assertTrue(self.figures(date='2026-11-09')['has_scan'])

    def test_bug_b_0242_a_company_never_scanned_is_empty(self):
        """Regression test for B-0242: a company with no snapshot of its own says no scan has run."""
        other = self.env['res.company'].create({'name': 'Never scanned'})
        self.operator.company_ids |= other
        self.assertFalse(self.figures(company_ids=[other.id])['has_scan'])

    def test_bug_b_0242_the_screen_shows_the_message_and_start_scan(self):
        """Regression test for B-0242: the past-date dashboard shows the message and Start a scan."""
        url = '/odoo/action-data_governance_quality_center_app.dashboard_action'
        self.browser_js(url, EMPTY_SCRIPT, login=self.operator.login, timeout=120)
