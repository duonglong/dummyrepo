"""Shared fixtures of the scorecard acceptance tests (T-028, T-029).

Requirements: BR-SPM-0012, BR-SPM-0013, FR-SPM-0079, FR-SPM-0083, FR-SPM-0084, FR-SPM-0087

Names not fixed by the design are chosen here and recorded in the module notes:
- the scorecard job is the procurement.job of code ``scorecard`` (``_spm_run('scorecard')``);
  it creates, for each supplier a template applies to (``partner_ids``), one
  procurement.supplier.scorecard of the period that holds the run date;
- procurement.supplier.scorecard.spm_trend(metric) returns the same metric of the earlier
  scorecards of the same supplier and template, oldest first, as a list of dicts with the keys
  ``period_start`` and ``value``;
- procurement.supplier.metric.action_spm_open_evidence() returns the window action that lists
  the records of ``evidence_ids`` (model ``evidence_model``);
- metric values are 0 to 100; the metric kind keys are the template's (on_time, fulfillment,
  quality, complaints, responsiveness, price_competitiveness).
"""
from .delivery_common import SpmDeliveryCase


class SpmScorecardCase(SpmDeliveryCase):

    def setUp(self):
        super().setUp()
        # the shipped template must not add a second scorecard of its own
        self.env['procurement.scorecard.template'].with_context(active_test=False).search([]).write(
            {'active': False})

    def _template(self, metrics, period='month', threshold=0.0, good_from=85.0,
                  acceptable_from=60.0, partners=None):
        """A template for ``partners`` (Acme by default); ``metrics`` is a list of (kind, weight)."""
        return self.env['procurement.scorecard.template'].create({
            'name': 'Template M6', 'period': period, 'company_id': self.company.id,
            'good_from': good_from, 'acceptable_from': acceptable_from,
            'review_threshold': threshold,
            'partner_ids': [(6, 0, (partners or self.acme).ids)],
            'metric_ids': [(0, 0, {'metric': kind, 'weight': weight}) for kind, weight in metrics],
        })

    def _line_order(self, planned_dates, supplier=None):
        """A confirmed order of the buyer with one line of 1 chair per planned date."""
        order = self.env['purchase.order'].create({
            'partner_id': (supplier or self.acme).id,
            'user_id': self.buyer.id,
            'order_line': [(0, 0, {
                'product_id': self.chair.id, 'name': 'Chair', 'product_qty': 1.0,
                'price_unit': 10.0, 'tax_ids': [(6, 0, [])], 'date_planned': planned,
            }) for planned in planned_dates],
        })
        order.button_confirm()
        if order.state != 'purchase':
            order.button_approve(force=True)
        return order

    def _scorecards(self, template, supplier=None):
        self.env.flush_all()
        return self.env['procurement.supplier.scorecard'].search([
            ('partner_id', '=', (supplier or self.acme).id), ('template_id', '=', template.id)])

    def _metric(self, scorecard, kind):
        metric = scorecard.metric_ids.filtered(lambda m: m.metric == kind)
        self.assertEqual(len(metric), 1, f"one {kind} metric row expected")
        return metric
