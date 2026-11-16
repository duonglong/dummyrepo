from odoo import Command
from odoo.tests import tagged

from .acceptance_m3_helpers import M3Case


@tagged('post_install', '-at_install')
class TestDashboardFilterAcceptance(M3Case):
    """Acceptance tests of the dashboard filter by company and date.

    Requirements: FR-DGQ-0033
    The filter form belongs to the client widget; the server entry
    ``data.quality.dashboard.get_figures(company_ids, date)`` is tested.
    """

    def snap(self, company, failed, date):
        return self.env['data.quality.score.snapshot'].create({
            'model_id': self.model('res.partner').id, 'company_id': company.id, 'date': date,
            'applicable_weight': 100, 'failed_weight': failed, 'has_score': True, 'records_scanned': 10})

    def figures(self, user, **kw):
        return self.env['data.quality.dashboard'].with_user(user).get_figures(**kw)

    def test_date_filter_shows_the_last_state_at_that_date(self):
        """SC-DGQ-0033-1: snapshots 70 (2026-10-31) and 85 (2026-11-12); company A and 2026-10-31 give 70."""
        self.env['data.quality.score.snapshot'].search([]).unlink()
        self.snap(self.cx, 30, '2026-10-31 10:00:00')
        self.snap(self.cx, 15, '2026-11-12 10:00:00')
        data = self.figures(self.viewer, company_ids=[self.cx.id], date='2026-10-31')
        self.assertEqual(data['score'], 70.0)
        # just above the date: the later snapshot counts
        self.assertEqual(self.figures(self.viewer, company_ids=[self.cx.id], date='2026-11-12')['score'], 85.0)
        # just below the first snapshot: nothing known yet
        self.assertIsNone(self.figures(self.viewer, company_ids=[self.cx.id], date='2026-10-30')['score'])

    def test_company_filter_leaves_out_other_companies(self):
        """SC-DGQ-0033-1: a filter on one company uses only its snapshots."""
        self.env['data.quality.score.snapshot'].search([]).unlink()
        self.snap(self.cx, 30, '2026-10-31 10:00:00')
        self.snap(self.cy, 0, '2026-10-31 10:00:00')
        viewer = self.viewer
        viewer.write({'company_ids': [Command.set((self.cx | self.cy).ids)], 'company_id': self.cx.id})
        dashboard = self.env['data.quality.dashboard'].with_user(viewer).with_context(
            allowed_company_ids=(self.cx | self.cy).ids)
        self.assertEqual(dashboard.get_figures(company_ids=[self.cx.id], date='2026-10-31')['score'], 70.0)

    def test_only_the_allowed_company_is_offered(self):
        """SC-DGQ-0033-2: a viewer allowed company A only is offered company A only, and company B's figures are not used."""
        self.env['data.quality.score.snapshot'].search([]).unlink()
        self.snap(self.cx, 30, '2026-10-31 10:00:00')
        self.snap(self.cy, 0, '2026-10-31 10:00:00')
        viewer = self.viewer
        viewer.write({'company_ids': [Command.set(self.cx.ids)], 'company_id': self.cx.id})
        data = self.figures(viewer, company_ids=[self.cx.id, self.cy.id])
        self.assertEqual([c['id'] for c in data['companies']], self.cx.ids)
        self.assertEqual(data['score'], 70.0)
