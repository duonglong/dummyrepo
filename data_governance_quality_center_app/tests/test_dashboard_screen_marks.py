from odoo.tests import HttpCase, tagged

LAYOUT_SCRIPT = """
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
        const root = await waitFor(() => document.querySelector(".o_dgq_dashboard .btn-link"), "Layout link");
        root.click();
        const down = await waitFor(
            () => document.querySelector(".o_dgq_dashboard button[aria-label=Down]"), "a move-down control");
        for (const name of ["Up", "Down"]) {
            const control = document.querySelector(".o_dgq_dashboard button[aria-label=" + name + "]");
            const icon = control.querySelector(".oi");
            const glyph = icon && getComputedStyle(icon, "::before");
            if (!icon || !glyph.content.includes(icon.dataset.icon) || !glyph.fontFamily.includes("Material Symbols")) {
                throw new Error("the move-" + name + " control shows no icon: "
                    + (icon ? glyph.content + " / " + glyph.fontFamily : "no icon element"));
            }
        }
        const labels = () => [...document.querySelectorAll(".o_dgq_dashboard button[aria-label=Down]")]
            .map((button) => button.parentElement.querySelector("span").textContent);
        const before = labels();
        down.click();
        await waitFor(() => labels()[0] !== before[0], "the first widget moving down");
        if (labels()[1] !== before[0]) {
            throw new Error("the first widget did not move one place down");
        }
        console.log("test successful");
    } catch (error) {
        console.error(error.message || String(error));
    }
})()
"""

BADGE_SCRIPT = """
(async () => {
    try {
        for (let i = 0; i < 100; i++) {
            const badge = document.querySelector(".o_dgq_dashboard .badge");
            if (badge) {
                if (!badge.textContent.includes("Illustrative")) {
                    throw new Error("the mark reads " + badge.textContent);
                }
                console.log("test successful");
                return;
            }
            await new Promise((resolve) => setTimeout(resolve, 100));
        }
        throw new Error("no Illustrative mark on the dashboard figures");
    } catch (error) {
        console.error(error.message || String(error));
    }
})()
"""


@tagged('post_install', '-at_install')
class TestDashboardScreenMarks(HttpCase):
    """What a person sees on the dashboard: the illustrative mark and the widget order controls.

    Requirements: FR-DGQ-0037, FR-DGQ-0080
    Each test opens the dashboard in a headless browser as the administrator. They do not drag
    anything and do not reach the layout of another user.
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

    def _open(self, script):
        url = '/odoo/action-data_governance_quality_center_app.dashboard_action'
        self.browser_js(url, script, login='admin', timeout=120)

    def test_bug_b_0240_widgets_can_be_moved_from_the_layout(self):
        """Regression test for B-0240: each Layout row has a visible move control that reorders.

        Requirements: FR-DGQ-0037
        """
        self._open(LAYOUT_SCRIPT)

    def test_bug_b_0239_demo_figures_carry_the_illustrative_mark(self):
        """Regression test for B-0239: figures from a demo snapshot show the Illustrative mark.

        Requirements: FR-DGQ-0080
        """
        self.env['data.quality.score.snapshot'].search([]).write({'is_illustrative': True})
        self._open(BADGE_SCRIPT)
