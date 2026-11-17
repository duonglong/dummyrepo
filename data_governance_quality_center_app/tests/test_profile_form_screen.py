from odoo.tests import HttpCase, tagged

from .acceptance_helpers import AcceptanceCase

SECTIONS_SCRIPT = """
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
        await waitFor(() => document.querySelector(".o_form_view .o_form_statusbar"), "the profile form");
        const expected = {
            "Top Values": ["Sunny Ltd", "12"],
            "Shapes": ["Aa aa", "7"],
            "Numbers": ["Minimum", "3.5", "Median", "9.25"],
            "Links": ["Empty Links", "40"],
            "Unused Choices": ["Archived"],
        };
        for (const [tab, texts] of Object.entries(expected)) {
            const link = await waitFor(
                () => [...document.querySelectorAll(".o_notebook .nav-link")].find((l) => l.textContent.trim() === tab),
                "the " + tab + " tab");
            link.click();
            const page = await waitFor(() => document.querySelector(".o_notebook .tab-pane.active"), tab + " content");
            await waitFor(() => texts.every((text) => page.textContent.includes(text)), tab + " figures " + texts);
        }
        console.log("test successful");
    } catch (error) {
        console.error(error.message || String(error));
    }
})()
"""


@tagged('post_install', '-at_install')
class TestProfileFormScreen(AcceptanceCase, HttpCase):
    """A person reads the figures of a finished profile on its form.

    Requirements: FR-DGQ-0053, FR-DGQ-0054, FR-DGQ-0055
    The browser test opens a finished profile as a viewer and reads each tab. It does not run a
    profile, export it or compare it.
    """

    def setUp(self):
        super().setUp()
        self.viewer = self.user('viewer', login='dgq_profile_viewer', company=self.cx)
        self.profile = self.env['data.quality.profile'].sudo().create({
            'model_id': self.model('res.partner').id, 'company_id': self.cx.id,
            'field_ids': [(6, 0, self.fld('res.partner', 'name').ids)]})
        self.profile.write({'state': 'done', 'record_count': 50, 'stale_count': 4})
        self.env['data.quality.profile.line'].sudo().create({
            'profile_id': self.profile.id, 'field_id': self.fld('res.partner', 'name').id, 'field_name': 'name',
            'field_type': 'char', 'empty_count': 5, 'empty_share': 10.0, 'distinct_count': 20,
            'top_values': [['Sunny Ltd', 12]], 'shapes': [['Aa aa', 7]],
            'min_value': 3.5, 'max_value': 20.0, 'mean_value': 10.0, 'median_value': 9.25,
            'link_empty_share': 40.0, 'link_distribution': [['Parent One', 3]], 'unused_choices': ['Archived']})

    def test_bug_b_0243_the_profile_form_shows_every_kind_of_figure(self):
        """Regression test for B-0243: top values, shapes, numbers, link and unused choices have a tab each."""
        url = '/odoo/action-data_governance_quality_center_app.profile_action/%s' % self.profile.id
        self.browser_js(url, SECTIONS_SCRIPT, login=self.viewer.login, timeout=120)
