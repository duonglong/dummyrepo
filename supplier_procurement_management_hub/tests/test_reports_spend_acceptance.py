"""Acceptance tests of T-031 (report views: spend, price trends, sourcing and approval cycle),
written from the specification.

Requirements: FR-SPM-0099, FR-SPM-0100, FR-SPM-0101

Names used (the design names the report models and their fields; the rest is fixed here and
recorded in the module notes): the spend and cycle figures are read from the report models
with ``_read_group`` over the fields the design lists; the price variance, which the design
leaves to the report service, is procurement.report.price.spm_price_trend(product, date_from,
date_to) returning one dict per supplier with ``partner_id``, ``price``, ``period_average``
and ``variance``. The reports are read as a Procurement manager.
"""
from freezegun import freeze_time

from .comparison_common import SpmComparisonCase

CRITERIA = [('Price', 'price', 'lower', 100.0)]


class TestReportsSpendAcceptance(SpmComparisonCase):
    """Requirements: FR-SPM-0099, FR-SPM-0100, FR-SPM-0101"""

    def _order_at(self, supplier, price, qty, when, product=None, cancel=False):
        with freeze_time(when):
            order = self.env['purchase.order'].create({
                'partner_id': supplier.id, 'user_id': self.buyer.id, 'date_order': when,
                'order_line': [(0, 0, {
                    'product_id': (product or self.chair).id, 'name': 'Line', 'product_qty': qty,
                    'price_unit': price, 'tax_ids': [(6, 0, [])]})],
            })
            if cancel:
                order.button_cancel()
            else:
                order.button_confirm()
                if order.state != 'purchase':
                    order.button_approve(force=True)
        return order

    # FR-SPM-0099 -----------------------------------------------------------
    def test_sc_0099_1_spend_by_supplier_excludes_cancelled_lines(self):
        """SC-SPM-0099-1: confirmed lines of 1,000 with Acme and 500 with Beta in October and a
        cancelled line of 200 with Acme; October spend grouped by supplier shows Acme 1,000 and
        Beta 500."""
        self._order_at(self.acme, 100.0, 10.0, '2026-10-12 10:00:00')
        self._order_at(self.beta, 50.0, 10.0, '2026-10-14 10:00:00')
        self._order_at(self.acme, 20.0, 10.0, '2026-10-15 10:00:00', cancel=True)
        self.env.flush_all()
        rows = self.env['procurement.report.spend'].with_user(self.manager)._read_group(
            [('date', '>=', '2026-10-01'), ('date', '<=', '2026-10-31')],
            ['partner_id'], ['amount_untaxed:sum'])
        spend = {partner: amount for partner, amount in rows}
        self.assertEqual(spend.get(self.acme), 1000.0)
        self.assertEqual(spend.get(self.beta), 500.0)
        self.assertEqual(len(spend), 2)

    def test_fr_0099_spend_can_be_grouped_by_product_and_company(self):
        """FR-SPM-0099: the same lines group by product and by company with the same total."""
        self._order_at(self.acme, 100.0, 10.0, '2026-10-12 10:00:00')
        self._order_at(self.beta, 50.0, 10.0, '2026-10-14 10:00:00', product=self.desk)
        self.env.flush_all()
        spend = self.env['procurement.report.spend'].with_user(self.manager)
        by_product = dict(spend._read_group([], ['product_id'], ['amount_untaxed:sum']))
        self.assertEqual(by_product.get(self.chair), 1000.0)
        self.assertEqual(by_product.get(self.desk), 500.0)
        by_company = dict(spend._read_group([], ['company_id'], ['amount_untaxed:sum']))
        self.assertEqual(by_company.get(self.company), 1500.0)

    # FR-SPM-0100 -----------------------------------------------------------
    def test_sc_0100_1_price_variance_per_supplier(self):
        """SC-SPM-0100-1: chairs bought in Q3 from Acme at 11.00 and Beta at 13.00 for equal
        quantities; the price report for chairs shows the Q3 average 12.00, Acme's variance
        -1.00 and Beta's +1.00."""
        self._order_at(self.acme, 11.0, 10.0, '2026-08-12 10:00:00')
        self._order_at(self.beta, 13.0, 10.0, '2026-08-14 10:00:00')
        self.env.flush_all()
        rows = self.env['procurement.report.price'].with_user(self.manager).spm_price_trend(
            self.chair, '2026-07-01', '2026-09-30')
        by_supplier = {row['partner_id']: row for row in rows}
        acme, beta = by_supplier[self.acme.id], by_supplier[self.beta.id]
        self.assertAlmostEqual(acme['price'], 11.0, places=2)
        self.assertAlmostEqual(beta['price'], 13.0, places=2)
        self.assertAlmostEqual(acme['period_average'], 12.0, places=2)
        self.assertAlmostEqual(acme['variance'], -1.0, places=2)
        self.assertAlmostEqual(beta['variance'], 1.0, places=2)

    # FR-SPM-0101 -----------------------------------------------------------
    def _event_answered(self, sent_at, awarded_at, partners, answering):
        """An event sent to ``partners`` at ``sent_at``, answered by ``answering`` and awarded
        to the first answer at ``awarded_at``."""
        with freeze_time(sent_at):
            event = self.env['procurement.sourcing.event'].with_user(self.buyer).create({
                'title': 'Cycle M6', 'buyer_id': self.buyer.id, 'deadline': '2026-10-30 17:00:00',
                'invited_partner_ids': [(6, 0, partners.ids)],
                'line_ids': [(0, 0, {
                    'name': 'Chair', 'product_id': self.chair.id, 'quantity': 10.0,
                    'uom_id': self.unit.id})],
                'criterion_ids': [(0, 0, {
                    'name': name, 'kind': kind, 'direction': direction, 'weight': weight})
                    for name, kind, direction, weight in CRITERIA],
            })
            event.with_user(self.buyer).action_spm_send()
        self._assume(event)
        responses = []
        for index, supplier in enumerate(answering):
            response = self._response(event, supplier)
            self._quote(response, [(event.line_ids, 10.0 + index, 5)],
                        submitted_date='2026-10-03 10:00:00')
            responses.append(response)
        with freeze_time(awarded_at):
            event.with_user(self.buyer).action_spm_evaluate()
            wizard = self.env['procurement.award.wizard'].with_user(self.buyer).create({
                'event_id': event.id, 'mode': 'whole', 'rationale': 'Best price',
                'response_id': responses[0].id})
            wizard.action_confirm()
        return event

    def test_sc_0101_1_response_rate_and_sourcing_cycle(self):
        """SC-SPM-0101-1: two October events sent to 3 suppliers each, with 4 answers in total,
        awarded 5 and 9 days after sending; October's cycle report shows a response rate of
        66.7 percent and an average sourcing cycle of 7 days."""
        suppliers = self.acme | self.beta | self.gamma
        first = self._event_answered(
            '2026-10-01 09:00:00', '2026-10-06 09:00:00', suppliers, self.acme | self.beta)
        second = self._event_answered(
            '2026-10-02 09:00:00', '2026-10-11 09:00:00', suppliers, self.acme | self.gamma)
        self.env.flush_all()
        report = self.env['procurement.report.sourcing'].with_user(self.manager)
        rows = report.search([('event_id', 'in', (first | second).ids)])
        self.assertEqual(sorted(rows.mapped('cycle_days')), [5.0, 9.0])
        ((invited, responded, cycle),) = report._read_group(
            [('event_id', 'in', (first | second).ids)], [],
            ['invited_count:sum', 'responded_count:sum', 'cycle_days:avg'])
        self.assertEqual((invited, responded), (6, 4))
        self.assertAlmostEqual(100.0 * responded / invited, 66.7, places=1)
        self.assertAlmostEqual(cycle, 7.0, places=1)

    def test_fr_0101_pending_approval_steps_show_their_age(self):
        """FR-SPM-0101: the approval report holds a row per pending step with its approver."""
        policy = self.env['procurement.approval.policy'].create({
            'name': 'Cycle policy M6', 'sequence': 1, 'company_id': self.company.id,
            'step_ids': [(0, 0, {
                'name': 'Step', 'sequence': 1, 'approver_type': 'users', 'mode': 'any',
                'user_ids': [(6, 0, self.manager.ids)]})]})
        self.assertTrue(policy)
        order = self.env['purchase.order'].with_user(self.buyer).create({
            'partner_id': self.acme.id, 'user_id': self.buyer.id,
            'order_line': [(0, 0, {
                'product_id': self.chair.id, 'name': 'Line', 'product_qty': 1.0,
                'price_unit': 100.0, 'tax_ids': [(6, 0, [])]})]})
        with freeze_time('2026-11-17 09:00:00'):
            order.with_user(self.buyer).action_spm_submit_approval()
        self.env.flush_all()
        rows = self.env['procurement.report.approval'].with_user(self.manager).search(
            [('state', '=', 'open')])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.approver_id, self.manager)
        self.assertEqual(rows.submitted_date.date().isoformat(), '2026-11-17')
