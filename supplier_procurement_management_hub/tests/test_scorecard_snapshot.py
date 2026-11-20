"""Unit tests of the scorecard snapshot (T-028).

Requirements: FR-SPM-0083, FR-SPM-0084, NFR-SPM-0006
"""
from datetime import date

from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError

from ..services import performance
from .scorecard_common import SpmScorecardCase


class TestScorecardSnapshot(SpmScorecardCase):
    """Requirements: FR-SPM-0083, FR-SPM-0084"""

    def test_template_without_supplier_applies_to_every_supplier(self):
        """FR-SPM-0083: a template naming no supplier and no category covers all suppliers."""
        template = self._template([('on_time', 1.0)], partners=self.env['res.partner'])
        template.partner_ids = False
        self.assertIn(template, performance.templates_for(self.env, self.acme))
        self.assertIn(self.acme, performance.suppliers_with_template(self.env))

    def test_job_closes_a_period_that_ended(self):
        """FR-SPM-0083: the run after a period ended closes its scorecard and keeps its snapshot."""
        template = self._template([('on_time', 1.0)], period='month')
        with freeze_time('2026-10-20 02:00:00'):
            self.env['procurement.job']._spm_run('scorecard')
        with freeze_time('2026-11-02 02:00:00'):
            self.env['procurement.job']._spm_run('scorecard')
        cards = self._scorecards(template).sorted('period_start')
        self.assertEqual(cards.mapped('period_start'), [date(2026, 10, 1), date(2026, 11, 1)])
        self.assertEqual(cards.mapped('period_closed'), [True, False])

    def test_recompute_needs_a_manager_and_an_open_period(self):
        """FR-SPM-0083: only a manager recomputes, and not a closed scorecard."""
        template = self._template([('on_time', 1.0)], period='month')
        with freeze_time('2026-11-16 02:00:00'):
            self.env['procurement.job']._spm_run('scorecard')
        card = self._scorecards(template)
        with freeze_time('2026-11-18 09:00:00'):
            card.with_user(self.manager).action_spm_recompute()
        self.assertEqual(str(card.computed_at), '2026-11-18 09:00:00')
        with self.assertRaises(AccessError):
            card.with_user(self.buyer).action_spm_recompute()
        card.sudo().period_closed = True
        with self.assertRaises(UserError):
            card.with_user(self.manager).action_spm_recompute()

    def test_evidence_action_lists_the_records(self):
        """FR-SPM-0084: the evidence button opens the records of the value."""
        template = self._template([('on_time', 1.0)], period='month')
        order = self._line_order(['2026-11-12 12:00:00'])
        self._receive(order, 1.0, '2026-11-10 10:00:00')
        with freeze_time('2026-11-20 02:00:00'):
            self.env['procurement.job']._spm_run('scorecard')
        metric = self._metric(self._scorecards(template), 'on_time')
        action = metric.action_spm_open_evidence()
        self.assertEqual(action['domain'], [('id', 'in', metric.evidence_ids)])
