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
            throw new Error("%(name)s did not open: " + selector + " not found");
        };
        await waitFor("%(selector)s");
        if (document.querySelector(".o_error_dialog")) {
            throw new Error("%(name)s opened an error dialog");
        }
        console.log("test successful");
    } catch (error) {
        console.error(error.message || String(error));
    }
})()
"""


@tagged('post_install', '-at_install')
class TestClientScreens(HttpCase):
    """The dashboard and the health page open in the web client without an error dialog.

    Requirements: FR-DGQ-0032, FR-DGQ-0079
    Each test opens the action in a headless browser as the administrator and waits for the
    screen's own root. It does not click a figure, a filter or a widget control: what happens after
    the first render is left to the server-side tests of the figures.
    """

    def setUp(self):
        super().setUp()
        self.env['data.quality.score.snapshot'].create({
            'model_id': self.env['ir.model']._get('res.partner').id,
            'company_id': self.env.company.id,
            'has_score': True,
            'score': 80.0,
            'applicable_weight': 10,
            'failed_weight': 2,
        })

    def _open(self, action, name, selector):
        script = SCREEN_SCRIPT % {'name': name, 'selector': selector}
        url = '/odoo/action-data_governance_quality_center_app.%s' % action
        self.browser_js(url, script, login='admin', timeout=120)

    def test_bug_b_0228_the_dashboard_opens(self):
        """Regression test for B-0228: the dashboard renders with a scan result, its trend widget included.

        Requirements: FR-DGQ-0032
        """
        self._open('dashboard_action', 'The dashboard', '.o_dgq_dashboard .o_dgq_trend')

    def test_bug_b_0229_the_health_page_opens(self):
        """Regression test for B-0229: the health page renders.

        Requirements: FR-DGQ-0079
        """
        self._open('health_action', 'The health page', '.o_dgq_health')
