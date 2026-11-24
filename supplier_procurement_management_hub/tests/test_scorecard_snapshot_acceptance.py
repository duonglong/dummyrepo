"""Acceptance tests of T-028 (scorecard snapshots, trends, evidence and formulas).

Requirements: FR-SPM-0083, FR-SPM-0084, NFR-SPM-0006, BR-SPM-0013, FR-SPM-0079

Written from the specification. The job, the trend and the evidence button are named in
tests/scorecard_common.py. The scorecard form (a read of the snapshot) names no field
beyond the design's, so the tests read the records; the Recompute button is not driven.
"""
from datetime import date, datetime

from freezegun import freeze_time

from .scorecard_common import SpmScorecardCase


class TestScorecardSnapshotAcceptance(SpmScorecardCase):
    """Requirements: FR-SPM-0083, FR-SPM-0084, NFR-SPM-0006"""

    def test_sc_0083_1_the_job_replaces_the_snapshot_of_the_open_period(self):
        """SC-SPM-0083-1: Acme's November scorecard computed on 2026-11-16 02:00; the job
        runs on 2026-11-17 02:00: one November scorecard of Acme exists, computed at
        2026-11-17 02:00."""
        template = self._template([('on_time', 100.0)], period='month')
        with freeze_time('2026-11-16 02:00:00'):
            self.env['procurement.job']._spm_run('scorecard')
        first = self._scorecards(template)
        self.assertEqual(len(first), 1, "the first run creates the November scorecard")
        self.assertEqual(first.computed_at, datetime(2026, 11, 16, 2, 0, 0))
        with freeze_time('2026-11-17 02:00:00'):
            self.env['procurement.job']._spm_run('scorecard')
        cards = self._scorecards(template)
        self.assertEqual(len(cards), 1, "the open period is replaced, not doubled")
        self.assertEqual(cards.computed_at, datetime(2026, 11, 17, 2, 0, 0))
        self.assertEqual(cards.period_start, date(2026, 11, 1))
        self.assertFalse(cards.period_closed)

    def test_sc_0083_1_a_closed_period_keeps_its_last_snapshot(self):
        """FR-SPM-0083: a scorecard of a closed period is not replaced by a later run."""
        template = self._template([('on_time', 100.0)], period='month')
        old = self.env['procurement.supplier.scorecard'].sudo().create({
            'partner_id': self.acme.id, 'template_id': template.id, 'company_id': self.company.id,
            'period_start': '2026-09-01', 'period_end': '2026-09-30', 'period_closed': True,
            'score': 77.0, 'computed_at': '2026-10-01 02:00:00',
        })
        with freeze_time('2026-11-17 02:00:00'):
            self.env['procurement.job']._spm_run('scorecard')
        self.env.flush_all()
        old.invalidate_recordset()
        self.assertEqual(old.computed_at, datetime(2026, 10, 1, 2, 0, 0))
        self.assertEqual(old.score, 77.0)

    def _receipts_with_one_late(self):
        """Five Acme receipt lines done in November 2026, four on time and one late."""
        order = self._line_order(['2026-11-12 12:00:00'] * 4 + ['2026-11-05 12:00:00'])
        self._receive(order, 1.0, '2026-11-10 10:00:00')
        return order

    def test_sc_0084_1_scorecard_shows_value_formula_trend_missing_data_and_evidence(self):
        """SC-SPM-0084-1: Acme's Q4 scorecard has on-time 80 and no sourcing event invitations.
        On-time shows 80, its formula and the Q1-Q3 trend; responsiveness is no data: not
        invited to any event; opening on-time lists the receipt lines used."""
        template = self._template(
            [('on_time', 60.0), ('responsiveness', 40.0)], period='quarter')
        for index, (start, end, value) in enumerate([
                ('2026-01-01', '2026-03-31', 70.0), ('2026-04-01', '2026-06-30', 75.0),
                ('2026-07-01', '2026-09-30', 78.0)]):
            self.env['procurement.supplier.scorecard'].sudo().create({
                'partner_id': self.acme.id, 'template_id': template.id, 'company_id': self.company.id,
                'period_start': start, 'period_end': end, 'period_closed': True,
                'score': value, 'computed_at': '2026-10-01 02:00:00',
                'metric_ids': [(0, 0, {
                    'metric': 'on_time', 'value': value, 'score': value, 'weight': 60.0,
                    'has_data': True})],
            })
        order = self._receipts_with_one_late()
        with freeze_time('2026-11-20 03:00:00'):
            self.env['procurement.job']._spm_run('scorecard')
        card = self._scorecards(template).filtered(lambda c: c.period_start == date(2026, 10, 1))
        self.assertEqual(len(card), 1, "one scorecard of the open fourth quarter")
        card = card.with_user(self.manager)
        on_time = self._metric(card, 'on_time')
        self.assertTrue(on_time.has_data)
        self.assertAlmostEqual(on_time.value, 80.0, places=1)
        self.assertTrue(on_time.formula, "the formula is shown in words")
        trend = card.spm_trend('on_time')
        self.assertEqual([round(row['value']) for row in trend], [70, 75, 78])
        self.assertEqual([row['period_start'] for row in trend],
                         [date(2026, 1, 1), date(2026, 4, 1), date(2026, 7, 1)])
        responsiveness = self._metric(card, 'responsiveness')
        self.assertFalse(responsiveness.has_data)
        self.assertIn('not invited', (responsiveness.missing_reason or '').lower())
        self.assertIn('responsiveness', (card.missing_metrics or '').lower())
        # BR-SPM-0013: only the metric with data counts
        self.assertAlmostEqual(card.score, 80.0, places=1)
        self.assertEqual(card.range, 'acceptable')
        # the receipt lines behind the value open from the metric
        self.assertEqual(len(on_time.evidence_ids), 5)
        action = on_time.action_spm_open_evidence()
        self.assertEqual(action['res_model'], on_time.evidence_model)
        listed = self.env[action['res_model']].search(action.get('domain') or [])
        self.assertCountEqual(listed.ids, on_time.evidence_ids)
        self.assertTrue(order.picking_ids)
