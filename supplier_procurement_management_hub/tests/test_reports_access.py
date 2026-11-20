"""Own tests of the performance reports, the savings baseline and the PDF dialog of T-032.

Requirements: FR-SPM-0102, FR-SPM-0103, FR-SPM-0104, FR-SPM-0105
"""
from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .comparison_common import SpmComparisonCase
from .delivery_common import SpmDeliveryCase

CRITERIA = [('Price', 'price', 'lower', 100.0)]


@tagged('post_install', '-at_install')
class TestSavingsBaseline(SpmComparisonCase):
    """Requirements: FR-SPM-0103"""

    def _awarded_event(self, **award):
        """An event of 10 chairs, quoted 78 and 74 first, then 71.4 by Acme, awarded to Acme."""
        event = self._sourcing(self.acme | self.beta, lines=[('Chair', self.chair, 10.0)], criteria=CRITERIA)
        self._assume(event)
        line = event.line_ids

        def offer(price):
            return [{'event_line_id': line.id, 'price_unit': price, 'discount': 0.0, 'lead_time_days': 5}]
        acme, beta = self._response(event, self.acme), self._response(event, self.beta)
        with freeze_time('2026-11-18 10:00:00'):
            acme.with_user(self.acme_user).spm_submit_version(offer(78.0))
            beta.with_user(self.beta_user).spm_submit_version(offer(74.0))
            acme.with_user(self.acme_user).spm_submit_version(offer(71.4))
        event.with_user(self.buyer).action_spm_evaluate()
        wizard = self.env['procurement.award.wizard'].with_user(self.buyer).create(dict({
            'event_id': event.id, 'mode': 'whole', 'rationale': 'Best final price',
            'response_id': acme.id}, **award))
        wizard.action_confirm()
        return event

    def test_fr_0103_first_average_counts_the_first_version_of_each_response(self):
        """FR-SPM-0103: a later version of a response does not move the first-offer baseline."""
        event = self._awarded_event()
        self.assertEqual(event.sudo().baseline_kind, 'first_average')
        self.assertAlmostEqual(event.sudo().baseline_amount, 760.0, places=2)
        self.assertAlmostEqual(event.sudo().awarded_amount, 714.0, places=2)

    def test_fr_0103_last_purchase_price_is_the_baseline_when_chosen(self):
        """FR-SPM-0103: the baseline is the product's last confirmed purchase price times the quantity."""
        with freeze_time('2026-10-01 10:00:00'):
            order = self.env['purchase.order'].create({
                'partner_id': self.acme.id, 'user_id': self.buyer.id, 'date_order': '2026-10-01 10:00:00',
                'order_line': [(0, 0, {
                    'product_id': self.chair.id, 'name': 'Chair', 'product_qty': 5.0,
                    'price_unit': 80.0, 'tax_ids': [(6, 0, [])]})]})
            order.button_confirm()
            if order.state != 'purchase':
                order.button_approve(force=True)
        self.env.flush_all()
        event = self._awarded_event(baseline_kind='last_price')
        self.assertEqual(event.sudo().baseline_kind, 'last_price')
        self.assertAlmostEqual(event.sudo().baseline_amount, 800.0, places=2)
        self.assertAlmostEqual(event.sudo().savings_amount, 86.0, places=2)

    def test_fr_0103_last_purchase_price_without_history_is_refused(self):
        """FR-SPM-0103: no earlier purchase, no last-price baseline: the award is refused."""
        with self.assertRaises(UserError):
            self._awarded_event(baseline_kind='last_price')

    def test_fr_0103_budget_is_the_baseline_and_must_be_stated(self):
        """FR-SPM-0103: a budget baseline holds the stated amount; without it the award is refused."""
        with self.assertRaises(UserError):
            self._awarded_event(baseline_kind='budget', budget_amount=0.0)
        event = self._awarded_event(baseline_kind='budget', budget_amount=900.0)
        self.assertAlmostEqual(event.sudo().baseline_amount, 900.0, places=2)
        self.assertAlmostEqual(event.sudo().savings_amount, 186.0, places=2)

    def test_fr_0103_a_user_cannot_write_the_savings(self):
        """FR-SPM-0103: savings come from the award only; a buyer's write or create of them is refused or dropped."""
        event = self._awarded_event()
        with self.assertRaises(AccessError):
            event.with_user(self.buyer).write({'savings_amount': 1e6})
        created = self.env['procurement.sourcing.event'].with_user(self.buyer).create({
            'title': 'Forged', 'buyer_id': self.buyer.id, 'savings_amount': 99.0,
            'deadline': '2026-12-30 10:00:00'})
        self.assertEqual(created.sudo().savings_amount, 0.0)

    def test_fr_0105_the_savings_report_row_carries_its_baseline_kind(self):
        """FR-SPM-0103, FR-SPM-0105: a buyer sees the savings and their baseline kind, a requester no report."""
        event = self._awarded_event()
        self.env.flush_all()
        row = self.env['procurement.report.sourcing'].with_user(self.buyer).search([('event_id', '=', event.id)])
        self.assertEqual(row.baseline_kind, 'first_average')
        self.assertAlmostEqual(row.savings_amount, 46.0, places=2)
        with self.assertRaises(AccessError):
            self.env['procurement.report.sourcing'].with_user(self.requester).search([])


@tagged('post_install', '-at_install')
class TestPdfDialog(SpmComparisonCase):
    """Requirements: FR-SPM-0104, FR-SPM-0105"""

    def _wizard(self, user, **values):
        return self.env['procurement.report.pdf.wizard'].with_user(user).create(dict({
            'report_model': 'procurement.report.spend', 'domain': '[]'}, **values))

    def test_fr_0104_only_a_report_can_be_printed(self):
        """FR-SPM-0104: a model that is not a report is refused, whatever the dialog is given."""
        with self.assertRaises(UserError):
            self._wizard(self.manager, report_model='res.partner').action_print()

    def test_fr_0104_a_filter_that_is_not_a_domain_is_refused(self):
        """FR-SPM-0104: the filter is read as data, never evaluated."""
        with self.assertRaises(UserError):
            self._wizard(self.manager, domain="__import__('os').getcwd()").action_print()

    def test_fr_0105_a_requester_cannot_open_the_dialog(self):
        """FR-SPM-0105: a requester has no right on the dialog and no report to print."""
        with self.assertRaises(AccessError):
            self._wizard(self.requester)

    def test_fr_0104_the_printout_groups_the_figures_and_states_the_total(self):
        """FR-SPM-0104: grouped by supplier, the printout holds one row per supplier and the total."""
        with freeze_time('2026-11-12 10:00:00'):
            order = self.env['purchase.order'].sudo().create({
                'partner_id': self.acme.id, 'company_id': self.company.id, 'date_order': '2026-11-12 10:00:00',
                'order_line': [(0, 0, {
                    'product_id': self.chair.id, 'name': 'Line', 'product_qty': 4.0,
                    'price_unit': 25.0, 'tax_ids': [(6, 0, [])]})]})
            order.write({'state': 'purchase'})
        self.env.flush_all()
        wizard = self._wizard(self.manager, group_by='partner_id')
        labels, rows = wizard.spm_figures()
        self.assertEqual([row['label'] for row in rows], [self.acme.name, 'Total'])
        self.assertIn('100.00', rows[-1]['cells'])
        self.assertIn('Untaxed amount', labels)


@tagged('post_install', '-at_install')
class TestDeliveryReport(SpmDeliveryCase):
    """Requirements: FR-SPM-0102"""

    def test_fr_0102_on_time_counts_receipts_on_or_before_the_agreed_day(self):
        """FR-SPM-0102: one line received on its day and one two days late give a 50 percent on-time rate."""
        punctual = self._dorder(planned='2026-10-10 08:00:00')
        late = self._dorder(planned='2026-10-10 08:00:00')
        self._receive(punctual, 100.0, '2026-10-10 09:00:00')
        self._receive(late, 100.0, '2026-10-12 09:00:00')
        rows = self.env['procurement.report.delivery'].with_user(self.manager).search(
            [('order_id', 'in', (punctual | late).ids)])
        self.assertEqual(sum(rows.mapped('line_count')), 2)
        self.assertEqual(sum(rows.mapped('on_time_count')), 1)
        self.assertEqual(sum(rows.mapped('received_qty')), 200.0)

    def test_fr_0102_order_to_receipt_exceptions_come_from_delivery_discrepancies(self):
        """FR-SPM-0102: delivery lines are the matched lines of the order-to-receipt stage."""
        order = self._dorder(planned='2026-10-10 08:00:00')
        self._receive(order, 100.0, '2026-10-10 09:00:00')
        self.env.flush_all()
        report = self.env['procurement.report.exception'].with_user(self.manager)
        self.assertEqual(report.spm_exception_rate('2026-10-01', '2026-10-31', stage='order_to_receipt'), 0.0)
        rows = report.search([('stage', '=', 'order_to_receipt'), ('date', '=', '2026-10-10')])
        self.assertGreaterEqual(sum(rows.mapped('line_count')), 1)
