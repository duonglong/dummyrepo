"""Unit tests of the scorecard metric computers and the weighted score.

Requirements: BR-SPM-0012, BR-SPM-0013, FR-SPM-0079, FR-SPM-0080, FR-SPM-0081, FR-SPM-0082
"""
from datetime import date, datetime

from freezegun import freeze_time

from odoo.addons.supplier_procurement_management_hub.services import performance

from .comparison_common import SpmComparisonCase
from .scorecard_common import SpmScorecardCase

OCTOBER = (date(2026, 10, 1), date(2026, 10, 31))
NOVEMBER = (date(2026, 11, 1), date(2026, 11, 30))


class TestScorecardMetrics(SpmScorecardCase, SpmComparisonCase):
    """Requirements: FR-SPM-0079, FR-SPM-0080, FR-SPM-0081, BR-SPM-0012"""

    def _compute(self, name, period=OCTOBER, partner=None):
        return getattr(performance, name)(self.env, partner or self.acme, self.company, *period)

    def test_on_time_counts_receipt_lines_against_the_agreed_date(self):
        """BR-SPM-0012, FR-SPM-0079: 17 of 20 lines on time give 85 with 20 evidence lines."""
        order = self._line_order(['2026-10-12 12:00:00'] * 17 + ['2026-10-02 12:00:00'] * 3)
        self._receive(order, 1.0, '2026-10-10 10:00:00')
        result = self._compute('on_time')
        self.assertAlmostEqual(result['value'], 85.0)
        self.assertEqual((result['numerator'], result['denominator']), (17, 20))
        self.assertEqual(len(result['evidence_ids']), 20)

    def test_on_time_uses_the_confirmed_date_and_the_company_tolerance(self):
        """BR-SPM-0012: the confirmed date replaces the expected one; the tolerance days extend it."""
        order = self._line_order(['2026-10-12 12:00:00', '2026-10-02 12:00:00'])
        commitment = self._commitment(order)
        commitment.filtered(lambda c: c.order_line_id == order.order_line[0]).confirmed_date = '2026-10-05 12:00:00'
        self._receive(order, 1.0, '2026-10-10 10:00:00')
        self.assertAlmostEqual(self._compute('on_time')['value'], 0.0)
        self.company.spm_on_time_tolerance_days = 8
        self.assertAlmostEqual(self._compute('on_time')['value'], 100.0)

    def test_on_time_has_no_data_without_receipt(self):
        """BR-SPM-0013: a metric without source data reports no data, not 0 or 100."""
        result = self._compute('on_time')
        self.assertFalse(result['has_data'])
        self.assertTrue(result['missing_reason'])

    def test_fulfillment_and_quality(self):
        """FR-SPM-0080: 950 of 1000 received, 19 returned: 95 and 98."""
        order = self._dorder(qty=1000.0, planned='2026-10-12 12:00:00')
        picking = self._receive(order, 950.0, '2026-10-10 10:00:00', backorder=True)
        with freeze_time('2026-10-11 10:00:00'):
            self._return(picking, 19.0)
        self.assertAlmostEqual(self._compute('fulfillment')['value'], 95.0)
        self.assertAlmostEqual(self._compute('quality')['value'], 98.0)

    def test_quality_subtracts_rejected_quantities_of_discrepancies(self):
        """FR-SPM-0080: a damaged-goods discrepancy of 50 units lowers quality; a late one does not."""
        order = self._dorder(qty=100.0, planned='2026-11-12 12:00:00')
        self._receive(order, 100.0, '2026-11-10 10:00:00')
        reasons = self.env['procurement.discrepancy.reason'].search([])
        damaged, late = (reasons.filtered(lambda r: r.kind == kind)[:1] for kind in ('damaged', 'late'))
        commitment = self._commitment(order)
        for reason, qty in ((damaged, 50.0), (late, 30.0)):
            self.env['procurement.delivery.discrepancy'].create({
                'commitment_id': commitment.id, 'reason_id': reason.id, 'quantity': qty,
                'action': 'Follow up', 'responsible_id': self.buyer.id, 'deadline': '2026-11-30'})
        self.assertAlmostEqual(self._compute('quality', NOVEMBER)['value'], 50.0)
        self.assertEqual(self._compute('complaints', NOVEMBER)['numerator'], 2.0)

    def test_responsiveness_counts_answers_before_the_deadline(self):
        """FR-SPM-0081: 3 of 4 invited events answered give 75."""
        events = [self._sourcing(self.acme) for _i in range(4)]
        for event in events[:3]:
            self._quote(self._response(event, self.acme), [(event.line_ids, 10.0, 5)])
        result = self._compute('responsiveness', NOVEMBER)
        self.assertAlmostEqual(result['value'], 75.0)
        self.assertEqual((result['numerator'], result['denominator']), (3.0, 4.0))
        self.assertGreater(result['average_answer_hours'], 0.0)

    def test_responsiveness_has_no_data_without_invitation(self):
        """FR-SPM-0081, BR-SPM-0013: not invited means no data."""
        result = self._compute('responsiveness', NOVEMBER)
        self.assertFalse(result['has_data'])
        self.assertIn('not invited', result['missing_reason'].lower())

    def test_price_competitiveness_against_the_lowest_comparable_offer(self):
        """FR-SPM-0081: 12.00 against 10.80 gives 90; the cheaper supplier gets 100."""
        event = self._sourcing(self.acme | self.beta)
        self._assume(event)
        self._quote(self._response(event, self.acme), [(event.line_ids, 12.0, 5)])
        self._quote(self._response(event, self.beta), [(event.line_ids, 10.8, 5)])
        self.assertAlmostEqual(self._compute('price_competitiveness', NOVEMBER)['value'], 90.0)
        self.assertAlmostEqual(self._compute('price_competitiveness', NOVEMBER, self.beta)['value'], 100.0)

    def test_price_competitiveness_leaves_out_events_that_are_not_normalized(self):
        """FR-SPM-0081, BR-SPM-0007: offers of an event without stated assumptions do not count."""
        event = self._sourcing(self.acme | self.beta)
        self._quote(self._response(event, self.acme), [(event.line_ids, 12.0, 5)])
        self._quote(self._response(event, self.beta), [(event.line_ids, 10.8, 5)])
        self.assertFalse(self._compute('price_competitiveness', NOVEMBER)['has_data'])

    def test_weighted_score_leaves_out_metrics_without_data(self):
        """BR-SPM-0013: only metrics with data weigh; the left-out ones are named."""
        rows = [
            {'metric': 'on_time', 'weight': 40.0, 'has_data': True, 'score': 80.0},
            {'metric': 'fulfillment', 'weight': 30.0, 'has_data': True, 'score': 95.0},
            {'metric': 'quality', 'weight': 30.0, 'has_data': True, 'score': 98.0},
            {'metric': 'responsiveness', 'weight': 50.0, 'has_data': False, 'score': 0.0},
        ]
        score, left_out = performance.weighted_score(rows)
        self.assertAlmostEqual(score, 89.9)
        self.assertEqual(left_out, ['responsiveness'])
        self.assertEqual(performance.weighted_score(rows[3:]), (None, ['responsiveness']))

    def test_range_limits_belong_to_the_higher_range(self):
        """FR-SPM-0082: good from 85 includes 85; acceptable from 60 includes 60."""
        self.assertEqual(performance.range_of(85.0, 85.0, 60.0), 'good')
        self.assertEqual(performance.range_of(84.9, 85.0, 60.0), 'acceptable')
        self.assertEqual(performance.range_of(60.0, 85.0, 60.0), 'acceptable')
        self.assertEqual(performance.range_of(59.9, 85.0, 60.0), 'poor')

    def test_evaluate_a_template_gives_score_and_range(self):
        """FR-SPM-0082, BR-SPM-0013: a template computes its metrics, the score and the range."""
        template = self._template([('on_time', 40.0), ('responsiveness', 60.0)], period='month')
        order = self._line_order(['2026-10-12 12:00:00'] * 4 + ['2026-10-02 12:00:00'])
        self._receive(order, 1.0, '2026-10-10 10:00:00')
        result = performance.evaluate(template, self.acme, *OCTOBER)
        self.assertAlmostEqual(result['score'], 80.0)
        self.assertEqual(result['range'], 'acceptable')
        self.assertEqual(result['missing_metrics'], ['responsiveness'])

    def test_period_bounds(self):
        """FR-SPM-0082: the month, quarter and year that hold a day."""
        day = date(2026, 11, 17)
        self.assertEqual(performance.period_bounds('month', day), (date(2026, 11, 1), date(2026, 11, 30)))
        self.assertEqual(performance.period_bounds('quarter', day), (date(2026, 10, 1), date(2026, 12, 31)))
        self.assertEqual(performance.period_bounds('year', day), (date(2026, 1, 1), date(2026, 12, 31)))
