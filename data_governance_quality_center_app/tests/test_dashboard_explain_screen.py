from odoo.tests import HttpCase, tagged

from .acceptance_helpers import AcceptanceCase

EXPLAIN_SCRIPT = """
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
        const why = await waitFor(
            () => document.querySelector(".o_dgq_dashboard .o_dgq_score button[name=explain_score]"),
            "an explain control next to the score");
        why.click();
        const box = await waitFor(() => document.querySelector(".o_dgq_dashboard .o_dgq_explanation"), "the causes");
        for (const text of ["Vendor needs VAT", "Rule added", "-7.0"]) {
            if (!box.textContent.includes(text)) {
                throw new Error("the explanation lacks " + text + ": " + box.textContent);
            }
        }
        console.log("test successful");
    } catch (error) {
        console.error(error.message || String(error));
    }
})()
"""


@tagged('post_install', '-at_install')
class TestDashboardExplainScreen(AcceptanceCase, HttpCase):
    """A person asks the dashboard why the score changed.

    Requirements: FR-DGQ-0035
    The browser test opens the dashboard as an operator and presses the control next to the score.
    It does not reach the company switcher, a past date or the trend rows.
    """

    def setUp(self):
        super().setUp()
        self.operator = self.user('operator', login='dgq_explain_operator', company=self.cx)
        self.env['data.quality.score.snapshot'].search([]).unlink()
        kept = self.make_rule('res.partner', 'required', ['name'], name='Contact needs name')
        added = self.make_rule('res.partner', 'required', ['vat'], name='Vendor needs VAT', severity='critical')
        old = self._snapshot('2026-11-01 10:00:00', 200, 30)
        self._rule_row(old, kept, 200, 30)
        new = self._snapshot('2026-11-08 10:00:00', 300, 66)
        self._rule_row(new, kept, 200, 30)
        self._rule_row(new, added, 100, 36)

    def _snapshot(self, date, applicable, failed, **vals):
        return self.env['data.quality.score.snapshot'].create(dict({
            'model_id': self.model('res.partner').id, 'company_id': self.cx.id, 'date': date,
            'applicable_weight': applicable, 'failed_weight': failed, 'has_score': True,
            'score': 100.0 * (1 - failed / applicable)}, **vals))

    def _rule_row(self, parent, rule, applicable, failed):
        return self._snapshot(
            parent.date, applicable, failed, scan_id=parent.scan_id.id, rule_id=rule.id,
            rule_version=1, severity=rule.severity)

    def test_bug_b_0241_the_score_has_an_explain_control(self):
        """Regression test for B-0241: the dashboard explains the latest score change on request."""
        url = '/odoo/action-data_governance_quality_center_app.dashboard_action'
        self.browser_js(url, EXPLAIN_SCRIPT, login=self.operator.login, timeout=120)

    def test_bug_b_0241_explain_score_lists_the_latest_change_of_each_model(self):
        """Regression test for B-0241: the server answers with the causes of the latest change per model."""
        result = self.env['data.quality.dashboard'].with_user(self.operator).explain_score()
        self.assertEqual(len(result['changes']), 1)
        change = result['changes'][0]
        self.assertEqual((round(change['score_before']), round(change['score_after'])), (85, 78))
        self.assertEqual(change['causes'][0]['rule_name'], 'Vendor needs VAT')
        self.assertEqual(change['model'], self.model('res.partner').name)
