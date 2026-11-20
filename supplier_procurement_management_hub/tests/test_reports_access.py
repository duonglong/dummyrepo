"""Own tests of the performance reports, the savings baseline and the PDF dialog of T-032.

Requirements: FR-SPM-0102, FR-SPM-0103, FR-SPM-0104, FR-SPM-0105
"""
import io
import json

import openpyxl
from freezegun import freeze_time

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, tagged

from .comparison_common import SpmComparisonCase
from .delivery_common import SpmDeliveryCase

CRITERIA = [('Price', 'price', 'lower', 100.0)]
EXPORT_PASSWORD = 'spm-export-pass'


def export_group_rows(case, user, model, columns, groupby, domain):
    """The group header rows (first cell ends with the record count) of a grouped XLSX export, as ``user``."""
    user.sudo().password = EXPORT_PASSWORD
    case.authenticate(user.login, EXPORT_PASSWORD)
    data = {'model': model, 'fields': [{'name': name, 'label': name} for name in columns],
            'ids': False, 'domain': domain, 'groupby': groupby, 'import_compat': False, 'context': {}}
    response = case.url_open('/web/export/xlsx', data={'data': json.dumps(data), 'csrf_token': case.csrf_token()})
    case.assertEqual(response.status_code, 200)
    sheet = openpyxl.load_workbook(io.BytesIO(response.content)).active
    return [row for row in sheet.iter_rows(min_row=2, values_only=True)
            if isinstance(row[0], str) and row[0].rstrip().endswith(')')]


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

    def test_fr_0103_last_purchase_price_follows_the_unit_of_the_event_line(self):
        """FR-SPM-0103: bought by the box of 10 at 100 and sourced by the unit, the baseline is 10 a unit."""
        with freeze_time('2026-10-01 10:00:00'):
            order = self.env['purchase.order'].create({
                'partner_id': self.acme.id, 'user_id': self.buyer.id, 'date_order': '2026-10-01 10:00:00',
                'order_line': [(0, 0, {
                    'product_id': self.chair.id, 'name': 'Chair', 'product_qty': 5.0, 'uom_id': self.box.id,
                    'price_unit': 100.0, 'tax_ids': [(6, 0, [])]})]})
            order.button_confirm()
            if order.state != 'purchase':
                order.button_approve(force=True)
        self.env.flush_all()
        event = self._awarded_event(baseline_kind='last_price')
        self.assertAlmostEqual(event.sudo().baseline_amount, 100.0, places=2)

    def test_fr_0103_savings_are_totalled_inside_one_baseline_kind_only(self):
        """FR-SPM-0103: no read path adds the savings of two baseline kinds into one figure."""
        self._awarded_event()
        self._awarded_event(baseline_kind='budget', budget_amount=900.0)
        self.env.flush_all()
        report = self.env['procurement.report.sourcing'].with_user(self.manager)
        measure = ['savings_amount:sum']
        domain = [('baseline_kind', '!=', False)]
        self.assertEqual(report._read_group(domain, [], measure), [(None,)])
        self.assertEqual(
            [row[1] for row in report._read_group(domain, ['awarded_partner_id'], measure)], [None])
        by_kind = dict(report._read_group(domain, ['baseline_kind'], measure))
        self.assertAlmostEqual(by_kind['first_average'], 46.0, places=2)
        self.assertAlmostEqual(by_kind['budget'], 186.0, places=2)
        sets = report._read_grouping_sets(domain, [[], ['baseline_kind']], measure)
        self.assertEqual(sets[0], [(None,)])
        self.assertAlmostEqual(dict(sets[1])['budget'], 186.0, places=2)
        pinned = report._read_group([('baseline_kind', '=', 'budget')], [], measure)
        self.assertAlmostEqual(pinned[0][0], 186.0, places=2)

    def test_fr_0103_the_multi_currency_total_of_savings_is_blanked_too(self):
        """FR-SPM-0103: the sum_currency aggregate the list, pivot and graph show beside the sum is guarded alike."""
        self._awarded_event()
        self._awarded_event(baseline_kind='budget', budget_amount=900.0)
        self.env.flush_all()
        report = self.env['procurement.report.sourcing'].with_user(self.manager)
        measure = ['savings_amount:sum', 'savings_amount:sum_currency', 'baseline_amount:sum_currency']
        domain = [('baseline_kind', '!=', False)]
        self.assertEqual(report._read_group(domain, [], measure), [(None, None, None)])
        sets = report._read_grouping_sets(domain, [[], ['baseline_kind']], measure)
        self.assertEqual(sets[0], [(None, None, None)])
        self.assertTrue(all(row[1] is not None for row in sets[1]))

    def test_fr_0104_a_grouped_export_holds_the_group_totals_of_the_screen(self):
        """FR-SPM-0103, FR-SPM-0104: grouped by supplier the savings cell is empty; by baseline kind it is per kind."""
        self._awarded_event()
        self._awarded_event(baseline_kind='budget', budget_amount=900.0)
        self.env.flush_all()
        domain = [('baseline_kind', '!=', False)]
        by_supplier = export_group_rows(
            self, self.manager, 'procurement.report.sourcing',
            ['awarded_partner_id', 'savings_amount'], ['awarded_partner_id'], domain)
        self.assertEqual([row[1] for row in by_supplier], [None] * len(by_supplier))
        by_kind = export_group_rows(
            self, self.manager, 'procurement.report.sourcing',
            ['baseline_kind', 'savings_amount'], ['baseline_kind'], domain)
        self.assertEqual(sorted(round(row[1], 2) for row in by_kind), [46.0, 186.0])

    def test_fr_0103_a_printout_of_the_savings_names_the_baseline_kind_of_each_total(self):
        """FR-SPM-0103, FR-SPM-0104: with no grouping the printed savings are split by baseline kind."""
        self._awarded_event()
        self.env.flush_all()
        wizard = self.env['procurement.report.pdf.wizard'].with_user(self.manager).create({
            'report_model': 'procurement.report.sourcing', 'domain': "[('baseline_kind', '!=', False)]"})
        _labels, rows = wizard.spm_figures()
        self.assertEqual([row['label'] for row in rows], [
            'Average of first offers', 'Total - Average of first offers'])

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


    def test_fr_0104_the_stated_filters_name_the_records_and_keep_the_operators(self):
        """FR-SPM-0104: the filters of the printout are read from the domain, with names and an 'or'."""
        domain = ['|', ('partner_id', '=', self.acme.id), ('company_id', 'in', [self.company.id]),
                  ('date', '>=', '2026-10-01')]
        wizard = self._wizard(self.manager, domain=repr(domain))
        text = wizard.filter_text
        self.assertIn(self.acme.name, text)
        self.assertIn(self.company.name, text)
        self.assertIn(' or ', text)
        self.assertIn('2026-10-01', text)
        self.assertNotIn(str(self.company.id) + ']', text)

    def test_fr_0104_a_user_note_is_printed_beside_the_derived_filters(self):
        """FR-SPM-0104: the note cannot replace the filters of the printout."""
        wizard = self._wizard(self.manager, domain=repr([('company_id', '=', self.company.id)]),
                              filter_description='Q4 review')
        text = wizard.spm_filter_text()
        self.assertIn(self.company.name, text)
        self.assertIn('Q4 review', text)

    def test_fr_0104_the_selected_rows_narrow_the_printout(self):
        """FR-SPM-0104: opened on selected rows, the dialog holds the filter and the selection."""
        context = {'active_model': 'procurement.report.spend', 'active_domain': [('company_id', '=', self.company.id)],
                   'active_ids': [7, 8]}
        values = self.env['procurement.report.pdf.wizard'].with_user(self.manager).with_context(
            **context).default_get(['report_model', 'domain'])
        self.assertEqual(values['report_model'], 'procurement.report.spend')
        self.assertIn("'id', 'in', [7, 8]", values['domain'])
        self.assertIn('company_id', values['domain'])

    def test_fr_0104_a_wrong_grouping_is_refused_with_a_message(self):
        """FR-SPM-0104: a granularity the report cannot give ends in a message, not a traceback."""
        for group in ('date:foo', 'partner_id:month', 'no_such_field'):
            with self.subTest(group=group), self.assertRaises(UserError):
                self._wizard(self.manager, group_by=group).action_print()

    def test_fr_0104_the_print_dialog_opens_as_the_user_does(self):
        """FR-SPM-0104: opened with Form in the list's context; the pressing of Print is the screen review's."""
        context = {'active_model': 'procurement.report.spend', 'active_domain': [], 'active_ids': []}
        form = Form(self.env['procurement.report.pdf.wizard'].with_user(self.manager).with_context(**context))
        form.group_by = 'partner_id'
        wizard = form.save()
        self.assertEqual(wizard.report_model, 'procurement.report.spend')
        self.assertTrue(wizard.filter_text)


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
        self.assertEqual(sum(rows.mapped('line_count')), 1)

    def test_fr_0102_on_time_boundary_one_day_late_and_with_a_tolerance(self):
        """FR-SPM-0102: one day late is late with no tolerance and on time with a tolerance of one day."""
        order = self._dorder(planned='2026-10-10 08:00:00')
        self._receive(order, 100.0, '2026-10-11 09:00:00')
        report = self.env['procurement.report.delivery'].with_user(self.manager)
        ((rate,),) = report._read_group([('order_id', '=', order.id)], [], ['on_time_rate:sum'])
        self.assertEqual(rate, 0.0)
        order.company_id.spm_on_time_tolerance_days = 1
        self.env.flush_all()
        ((rate,),) = report._read_group([('order_id', '=', order.id)], [], ['on_time_rate:sum'])
        self.assertEqual(rate, 100.0)

    def test_fr_0102_the_rates_are_exact_under_any_grouping(self):
        """FR-SPM-0102: one of two due lines on time and 200 of 300 received make 50 and 66.67 per hundred."""
        punctual = self._dorder(planned='2026-10-10 08:00:00', qty=100.0)
        late = self._dorder(planned='2026-10-10 08:00:00', qty=200.0)
        self._receive(punctual, 100.0, '2026-10-10 09:00:00')
        self._receive(late, 100.0, '2026-10-20 09:00:00')
        orders = (punctual | late).ids
        report = self.env['procurement.report.delivery'].with_user(self.manager)
        ((on_time, fulfillment),) = report._read_group(
            [('order_id', 'in', orders)], [], ['on_time_rate:sum', 'fulfillment_rate:sum'])
        self.assertAlmostEqual(on_time, 50.0, places=2)
        self.assertAlmostEqual(fulfillment, 200.0 / 3.0, places=2)
        grouped = report._read_group([('order_id', 'in', orders)], ['partner_id'], ['on_time_rate:sum'])
        self.assertAlmostEqual(grouped[0][1], 50.0, places=2)

    def test_fr_0102_one_discrepancy_on_two_due_lines_is_fifty_per_hundred(self):
        """FR-SPM-0102: order to receipt, one delivery discrepancy over two due lines gives 50."""
        first = self._dorder(supplier=self.beta, planned='2026-10-10 08:00:00')
        second = self._dorder(supplier=self.beta, planned='2026-10-11 08:00:00')
        self._receive(first, 100.0, '2026-10-10 09:00:00')
        self._receive(second, 100.0, '2026-10-11 09:00:00')
        reason = self.env['procurement.discrepancy.reason'].search([('kind', '=', 'late')], limit=1)
        self.env['procurement.delivery.discrepancy'].with_user(self.buyer).create({
            'commitment_id': self._commitment(first).id, 'reason_id': reason.id, 'action': 'Chase',
            'responsible_id': self.manager.id, 'deadline': '2026-12-20'})
        self.env.flush_all()
        report = self.env['procurement.report.exception'].with_user(self.manager)
        ((rate,),) = report._read_group(
            [('stage', '=', 'order_to_receipt'), ('partner_id', '=', self.beta.id)], [], ['exception_rate:sum'])
        self.assertAlmostEqual(rate, 50.0, places=2)

    def test_fr_0104_a_grouped_export_divides_the_sums_of_a_rate(self):
        """FR-SPM-0102, FR-SPM-0104: one of two due lines on time exports 50 on its group row, not 100."""
        punctual = self._dorder(planned='2026-10-10 08:00:00', qty=100.0)
        late = self._dorder(planned='2026-10-10 08:00:00', qty=100.0)
        self._receive(punctual, 100.0, '2026-10-10 09:00:00')
        self._receive(late, 100.0, '2026-10-20 09:00:00')
        rows = export_group_rows(
            self, self.manager, 'procurement.report.delivery', ['partner_id', 'on_time_rate', 'line_count'],
            ['partner_id'], [('order_id', 'in', (punctual | late).ids)])
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0][1], 50.0, places=2)
        self.assertEqual(rows[0][2], '2')

    def test_fr_0102_a_commitment_not_yet_due_is_no_matched_line(self):
        """FR-SPM-0102: a commitment due in thirty days counts neither as a delivery line nor as a matched line."""
        later = fields.Date.add(fields.Date.today(), days=30)
        order = self._dorder(planned=f'{later} 08:00:00')
        self.env.flush_all()
        self.assertFalse(self.env['procurement.report.delivery'].with_user(self.manager).search(
            [('order_id', '=', order.id)]))
        self.assertFalse(self.env['procurement.report.exception'].with_user(self.manager).search(
            [('stage', '=', 'order_to_receipt'), ('date', '=', later)]))

    def test_fr_0102_a_rate_grouped_by_kind_is_empty_not_zero(self):
        """FR-SPM-0102: kind is known on exception rows only, so a group by kind has no denominator."""
        first = self._dorder(supplier=self.beta, planned='2026-10-10 08:00:00')
        second = self._dorder(supplier=self.beta, planned='2026-10-11 08:00:00')
        self._receive(first, 100.0, '2026-10-10 09:00:00')
        self._receive(second, 100.0, '2026-10-11 09:00:00')
        self.env.flush_all()
        report = self.env['procurement.report.exception'].with_user(self.manager)
        rows = report._read_group(
            [('stage', '=', 'order_to_receipt'), ('partner_id', '=', self.beta.id)],
            ['kind'], ['exception_rate:sum', 'line_count:sum'])
        self.assertEqual([(rate, lines) for _kind, rate, lines in rows], [(None, 2)])

    def test_fr_0102_a_rate_grouped_by_kind_is_empty_with_a_discrepancy_and_on_the_pivot_path(self):
        """FR-SPM-0102: with one discrepancy in the data, the list path and the pivot path leave the kind rate empty."""
        first = self._dorder(supplier=self.beta, planned='2026-10-10 08:00:00')
        second = self._dorder(supplier=self.beta, planned='2026-10-11 08:00:00')
        self._receive(first, 100.0, '2026-10-10 09:00:00')
        self._receive(second, 100.0, '2026-10-11 09:00:00')
        reason = self.env['procurement.discrepancy.reason'].search([('kind', '=', 'late')], limit=1)
        self.env['procurement.delivery.discrepancy'].with_user(self.buyer).create({
            'commitment_id': self._commitment(first).id, 'reason_id': reason.id, 'action': 'Chase',
            'responsible_id': self.manager.id, 'deadline': '2026-12-20'})
        self.env.flush_all()
        report = self.env['procurement.report.exception'].with_user(self.manager)
        domain = [('stage', '=', 'order_to_receipt'), ('partner_id', '=', self.beta.id)]
        aggregates = ['exception_rate:sum', 'exception_count:sum', 'line_count:sum']
        listed = report._read_group(domain, ['kind'], aggregates)
        self.assertEqual([row[1:] for row in listed], [(None, 1, 2)])
        paths = report._read_grouping_sets(domain, [['kind'], []], aggregates)
        self.assertEqual([row[1:] for row in paths[0]], [(None, 1, 2)])
        self.assertAlmostEqual(paths[1][0][0], 50.0, places=2)

    def test_fr_0102_a_missing_rate_prints_as_a_dash(self):
        """FR-SPM-0102: the printout shows a dash for a rate with no denominator, never 0.00."""
        wizard = self.env['procurement.report.pdf.wizard']
        rate = self.env['procurement.report.exception']._fields['exception_rate']
        self.assertEqual(wizard._spm_format(rate, False), '-')
        self.assertEqual(wizard._spm_format(rate, 0.0), '0.00')


@tagged('post_install', '-at_install')
class TestSavingsScreen(SpmComparisonCase):
    """Requirements: FR-SPM-0103"""

    def test_fr_0103_the_savings_screen_adds_up_savings_of_one_baseline_only(self):
        """FR-SPM-0103: grouped by baseline kind by default, with no total across kinds and no pivot."""
        action = self.env.ref('supplier_procurement_management_hub.procurement_report_savings_action')
        self.assertEqual(action.view_mode, 'list')
        self.assertIn('group_baseline', action.context)
        arch = self.env.ref('supplier_procurement_management_hub.procurement_report_savings_list').arch
        self.assertNotIn('sum=', arch)
