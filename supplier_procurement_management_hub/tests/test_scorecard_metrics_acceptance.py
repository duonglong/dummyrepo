"""Acceptance tests of T-028 for the metric scenarios of T-027 (on-time, fulfillment, quality,
responsiveness, price competitiveness, weighted score).

Requirements: FR-SPM-0079, FR-SPM-0080, FR-SPM-0081, FR-SPM-0082, BR-SPM-0012, BR-SPM-0013

A scorecard is the snapshot model T-028 builds, so these scenarios of T-027 are recorded under
T-028, the task that builds the last model they need. The scorecard is computed by the job
named in tests/scorecard_common.py and read back from procurement.supplier.scorecard and its
metric rows (value, numerator, denominator, has_data, evidence_ids). The average answer time of
SC-SPM-0081-1 has no field in the design, so it is not asserted.
"""
from datetime import datetime

from freezegun import freeze_time

from .comparison_common import SpmComparisonCase
from .scorecard_common import SpmScorecardCase


class TestScorecardMetricsAcceptance(SpmScorecardCase, SpmComparisonCase):
    """Requirements: FR-SPM-0079, FR-SPM-0080, FR-SPM-0081"""

    def _run_job(self, when):
        with freeze_time(when):
            self.env['procurement.job']._spm_run('scorecard')
        self.env.flush_all()

    def test_sc_0079_1_on_time_is_the_share_of_receipt_lines_on_time(self):
        """SC-SPM-0079-1: 20 receipt lines done in October, 17 on time with a 0-day tolerance;
        on-time delivery is 85 and the evidence lists the 20 receipt lines."""
        template = self._template([('on_time', 100.0)], period='month')
        order = self._line_order(['2026-10-12 12:00:00'] * 17 + ['2026-10-02 12:00:00'] * 3)
        self._receive(order, 1.0, '2026-10-10 10:00:00')
        self._run_job('2026-10-31 23:00:00')
        card = self._scorecards(template)
        self.assertEqual(len(card), 1)
        metric = self._metric(card, 'on_time')
        self.assertTrue(metric.has_data)
        self.assertAlmostEqual(metric.value, 85.0, places=1)
        self.assertEqual(len(metric.evidence_ids), 20)

    def test_br_0012_a_receipt_done_on_the_agreed_date_is_on_time(self):
        """BR-SPM-0012 boundary: done exactly on the agreed date is on time, one day later is
        late (0-day tolerance)."""
        template = self._template([('on_time', 100.0)], period='month')
        order = self._line_order(['2026-10-10 12:00:00', '2026-10-09 12:00:00'])
        self._receive(order, 1.0, '2026-10-10 10:00:00')
        self._run_job('2026-10-31 23:00:00')
        metric = self._metric(self._scorecards(template), 'on_time')
        self.assertAlmostEqual(metric.value, 50.0, places=1)

    def test_sc_0080_1_fulfillment_and_quality_acceptance(self):
        """SC-SPM-0080-1: 1,000 units due and 950 received in October, of which 19 returned;
        quantity fulfillment is 95 and quality acceptance is 98."""
        template = self._template([('fulfillment', 50.0), ('quality', 50.0)], period='month')
        order = self._dorder(qty=1000.0, planned='2026-10-12 12:00:00')
        picking = self._receive(order, 950.0, '2026-10-10 10:00:00', backorder=True)
        with freeze_time('2026-10-11 10:00:00'):
            self._return(picking, 19.0)
        self._run_job('2026-10-31 23:00:00')
        card = self._scorecards(template)
        self.assertAlmostEqual(self._metric(card, 'fulfillment').value, 95.0, places=1)
        self.assertAlmostEqual(self._metric(card, 'quality').value, 98.0, places=1)

    def test_fr_0080_quality_has_no_data_without_a_receipt(self):
        """FR-SPM-0080: where no goods were received the quality metric shows no data, not 100."""
        template = self._template([('quality', 100.0)], period='month')
        self._dorder(qty=10.0, planned='2026-10-12 12:00:00')
        self._run_job('2026-10-31 23:00:00')
        metric = self._metric(self._scorecards(template), 'quality')
        self.assertFalse(metric.has_data)

    def test_sc_0081_1_responsiveness_is_the_share_of_events_answered_in_time(self):
        """SC-SPM-0081-1: Acme invited to 4 events and answered 3 before the deadline;
        responsiveness is 75 (3 of 4). The events are in November 2026 here, the period
        being the one the job runs in."""
        template = self._template([('responsiveness', 100.0)], period='month')
        events = [self._sourcing(self.acme, deadline=datetime(2026, 11, 30, 17, 0, 0))
                  for _i in range(4)]
        for event in events[:3]:
            self._quote(self._response(event, self.acme), [(event.line_ids, 10.0, 5)])
        self._run_job('2026-11-25 03:00:00')
        metric = self._metric(self._scorecards(template), 'responsiveness')
        self.assertTrue(metric.has_data)
        self.assertAlmostEqual(metric.value, 75.0, places=1)
        self.assertEqual((metric.numerator, metric.denominator), (3.0, 4.0))

    def test_sc_0081_2_price_competitiveness_against_the_lowest_comparable_offer(self):
        """SC-SPM-0081-2: Acme's only quoted line was 12.00 when the lowest comparable offer
        was 10.80; price competitiveness is 90."""
        template = self._template([('price_competitiveness', 100.0)], period='month')
        event = self._sourcing(self.acme | self.beta)
        self._assume(event)
        self._quote(self._response(event, self.acme), [(event.line_ids, 12.0, 5)])
        self._quote(self._response(event, self.beta), [(event.line_ids, 10.8, 5)])
        self._run_job('2026-11-25 03:00:00')
        metric = self._metric(self._scorecards(template), 'price_competitiveness')
        self.assertTrue(metric.has_data)
        self.assertAlmostEqual(metric.value, 90.0, places=1)
