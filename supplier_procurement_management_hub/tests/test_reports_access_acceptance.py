"""Acceptance tests of T-032 (performance reports, savings baseline, filters, drill-down,
XLSX and PDF, role restriction), written from the specification.

Requirements: FR-SPM-0102, FR-SPM-0103, FR-SPM-0104, FR-SPM-0105, BR-SPM-0004

Names not fixed by the design are chosen here and recorded in the module notes:
- procurement.report.exception.spm_exception_rate(date_from, date_to, stage='receipt_to_bill')
  returns the exceptions per hundred matched lines (a float); the reconciliation lines and
  exceptions are seeded as the matching writes them (a bill's matching is another task);
- procurement.report.sourcing keeps, next to ``savings_amount`` and ``baseline_kind``, the
  baseline value in ``baseline_amount``; the kind is shown as 'Average of first offers';
- the XLSX file is Odoo's own list export (route /web/export/xlsx) of the report model;
- procurement.report.pdf.wizard.action_print() returns the report action; the PDF is checked
  through its HTML rendering (``_render_qweb_html``); the wizard prints the filter description
  it was given, the user and the print time.
"""
import io
import json

import openpyxl
from freezegun import freeze_time

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .comparison_common import SpmComparisonCase
from .matching_common import SpmMatchingCommon

MODULE = 'supplier_procurement_management_hub'
PASSWORD = 'spm-report-pass'
CRITERIA = [('Price', 'price', 'lower', 100.0)]


@tagged('post_install', '-at_install')
class TestExceptionRateAcceptance(SpmMatchingCommon):
    """Requirements: FR-SPM-0102"""

    def test_sc_0102_1_receipt_to_bill_exception_rate(self):
        """SC-SPM-0102-1: 200 bill lines matched in October with 6 exceptions; October's exception
        report shows a receipt-to-bill exception rate of 3 per hundred lines."""
        with freeze_time('2026-10-10 10:00:00'):
            bill = self.env['account.move'].create({
                'move_type': 'in_invoice', 'partner_id': self.acme.id,
                'invoice_date': '2026-10-10', 'date': '2026-10-10',
                'invoice_line_ids': [(0, 0, {
                    'name': f'Line {i}', 'quantity': 1, 'price_unit': 1.0,
                    'account_id': self.company_data['default_account_expense'].id,
                    'tax_ids': [(6, 0, [])]}) for i in range(200)],
            })
            recon = self.env['procurement.reconciliation'].sudo().search([('move_id', '=', bill.id)])
            if recon:
                recon.line_ids.unlink()
                recon.exception_ids.unlink()
            else:
                recon = self.env['procurement.reconciliation'].sudo().create({'move_id': bill.id})
            lines = self.env['procurement.reconciliation.line'].sudo().create([
                {'reconciliation_id': recon.id, 'move_line_id': line.id}
                for line in bill.invoice_line_ids])
            self.env['procurement.reconciliation.exception'].sudo().create([{
                'reconciliation_id': recon.id, 'kind': 'price', 'recon_line_id': line.id,
                'move_line_id': line.move_line_id.id} for line in lines[:6]])
        self.env.flush_all()
        report = self.env['procurement.report.exception'].with_user(self.user_pm)
        rate = report.spm_exception_rate('2026-10-01', '2026-10-31', stage='receipt_to_bill')
        self.assertAlmostEqual(rate, 3.0, places=2)
        self.assertEqual(report.spm_exception_rate('2026-10-01', '2026-10-31', stage='order_to_receipt'), 0.0)
        self.assertEqual(report.spm_exception_rate('2026-09-01', '2026-09-30', stage='receipt_to_bill'), 0.0)


class TestReportsAccessAcceptance(SpmComparisonCase):
    """Requirements: FR-SPM-0103, FR-SPM-0104, FR-SPM-0105"""

    def setUp(self):
        super().setUp()
        self.company_b = self.env['res.company'].create({'name': 'SPM Branch B M6'})
        self.exporter = self._report_user(
            'Eli Exporter', 'spm_exporter_m6', [f'{MODULE}.group_spm_manager'],
            self.company | self.company_b)
        self.user_b = self._report_user(
            'Bia Buyer B', 'spm_buyer_b_m6', [f'{MODULE}.group_spm_buyer'], self.company_b)
        self.dm = self._report_user(
            'Dan Dept', 'spm_dm_m6', [f'{MODULE}.group_spm_department_manager'], self.company)

    def _report_user(self, name, login, xmlids, companies):
        env = self.env
        return env['res.users'].with_context(no_reset_password=True).create({
            'name': name, 'login': login, 'password': PASSWORD, 'email': f'{login}@example.com',
            'company_id': companies[0].id, 'company_ids': [(6, 0, companies.ids)],
            'group_ids': [(6, 0, [env.ref(x).id for x in xmlids]
                            + [env.ref('base.group_partner_manager').id])],
        })

    def _spend(self, company, price, qty, when='2026-11-12 10:00:00'):
        """A confirmed order of ``company`` for ``qty`` chairs at ``price``."""
        with freeze_time(when):
            order = self.env['purchase.order'].with_company(company).sudo().create({
                'partner_id': self.acme.id, 'company_id': company.id, 'date_order': when,
                'order_line': [(0, 0, {
                    'product_id': self.chair.id, 'name': 'Line', 'product_qty': qty,
                    'price_unit': price, 'tax_ids': [(6, 0, [])]})],
            })
            order.write({'state': 'purchase', 'date_approve': when})
        return order

    def _export_amounts(self, user, domain):
        """The amount cells of the XLSX export of the spend report, as the user."""
        self.authenticate(user.login, PASSWORD)
        data = {'model': 'procurement.report.spend', 'fields': [
            {'name': 'amount_untaxed', 'label': 'Amount'}, {'name': 'partner_id', 'label': 'Supplier'}],
            'ids': False, 'domain': domain, 'import_compat': False, 'context': {}}
        response = self.url_open(
            '/web/export/xlsx', data={'data': json.dumps(data), 'csrf_token': self.csrf_token()})
        self.assertEqual(response.status_code, 200)
        sheet = openpyxl.load_workbook(io.BytesIO(response.content)).active
        return [row[0] for row in sheet.iter_rows(min_row=2, values_only=True)
                if isinstance(row[0], (int, float))]

    # FR-SPM-0103 -----------------------------------------------------------
    def test_sc_0103_1_savings_are_shown_against_their_baseline(self):
        """SC-SPM-0103-1: first versions of 780 and 740 and an award at 714 with the default
        baseline; savings are 46 against a baseline of 760, shown as 'average of first offers'."""
        event = self._sourcing(
            self.acme | self.beta, lines=[('Chair', self.chair, 10.0)], criteria=CRITERIA)
        self._assume(event)
        line = event.line_ids

        def offer(price):
            return [{'event_line_id': line.id, 'price_unit': price, 'discount': 0.0, 'lead_time_days': 5}]
        acme, beta = self._response(event, self.acme), self._response(event, self.beta)
        with freeze_time('2026-11-18 10:00:00'):
            acme.with_user(self.acme_user).spm_submit_version(offer(78.0))
            beta.with_user(self.beta_user).spm_submit_version(offer(74.0))
        with freeze_time('2026-11-19 10:00:00'):
            acme.with_user(self.acme_user).spm_submit_version(offer(71.4))
        with freeze_time('2026-11-20 10:00:00'):
            event.with_user(self.buyer).action_spm_evaluate()
            self.env['procurement.award.wizard'].with_user(self.buyer).create({
                'event_id': event.id, 'mode': 'whole', 'rationale': 'Best final price',
                'response_id': acme.id}).action_confirm()
        self.env.flush_all()
        row = self.env['procurement.report.sourcing'].with_user(self.manager).search(
            [('event_id', '=', event.id)])
        self.assertEqual(len(row), 1)
        self.assertAlmostEqual(row.savings_amount, 46.0, places=2)
        self.assertAlmostEqual(row.baseline_amount, 760.0, places=2)
        labels = dict(row._fields['baseline_kind']._description_selection(self.env))
        self.assertEqual(labels[row.baseline_kind].lower(), 'average of first offers')

    # FR-SPM-0104 -----------------------------------------------------------
    def test_sc_0104_1_xlsx_and_pdf_hold_the_totals_of_the_screen(self):
        """SC-SPM-0104-1: the spend report filtered on company A and Q4 2026, exported as XLSX and
        PDF: both hold the same totals as the screen, and the PDF names the filters, the user and
        the print time."""
        self._spend(self.company, 100.0, 10.0)
        self._spend(self.company, 50.0, 10.0, when='2026-12-02 10:00:00')
        self._spend(self.company_b, 70.0, 10.0)
        self.env.flush_all()
        domain = [('company_id', '=', self.company.id), ('date', '>=', '2026-10-01'),
                  ('date', '<=', '2026-12-31')]
        spend = self.env['procurement.report.spend'].with_user(self.exporter)
        ((screen_total,),) = spend._read_group(domain, [], ['amount_untaxed:sum'])
        self.assertEqual(screen_total, 1500.0)
        self.assertEqual(sum(self._export_amounts(self.exporter, domain)), screen_total)
        wizard = self.env['procurement.report.pdf.wizard'].with_user(self.exporter).create({
            'report_model': 'procurement.report.spend', 'domain': repr(domain),
            'group_by': 'partner_id',
            'filter_description': f'Company: {self.company.name}; Period: Q4 2026'})
        with freeze_time('2026-12-05 10:30:00'):
            action = wizard.action_print()
            report = self.env['ir.actions.report'].with_user(self.exporter)
            html = report._render_qweb_html(action['report_name'], wizard.ids, data=action.get('data'))[0]
        html = html.decode() if isinstance(html, bytes) else html
        self.assertIn('1,500.00', html)
        self.assertNotIn('700.00', html, "no figure of company B")
        self.assertIn(self.company.name, html)
        self.assertIn('Q4 2026', html)
        self.assertIn('Eli Exporter', html)
        self.assertIn('10:30', html)

    # FR-SPM-0105 -----------------------------------------------------------
    def test_sc_0105_1_a_user_of_company_b_sees_nothing_of_company_a(self):
        """SC-SPM-0105-1: a user allowed only in company B opens the spend report: no amount of
        company A is in any total, drill-down or export."""
        self._spend(self.company, 100.0, 10.0)
        self._spend(self.company_b, 50.0, 10.0)
        self.env.flush_all()
        spend = self.env['procurement.report.spend'].with_user(self.user_b)
        ((total,),) = spend._read_group([], [], ['amount_untaxed:sum'])
        self.assertEqual(total, 500.0)
        self.assertEqual(spend.search_count([('company_id', '=', self.company.id)]), 0)
        self.assertEqual(set(spend.search([]).mapped('company_id')), {self.company_b})
        self.assertEqual(self._export_amounts(self.user_b, []), [500.0])

    def test_fr_0105_a_requester_sees_no_supplier_price_figure_in_a_report(self):
        """FR-SPM-0105: a Requester cannot read the spend report."""
        self._spend(self.company, 100.0, 10.0)
        self.env.flush_all()
        with self.assertRaises(AccessError):
            self.env['procurement.report.spend'].with_user(self.requester).search([])

    def test_sc_0105_2_savings_are_not_shown_without_savings_rights(self):
        """SC-SPM-0105-2: a Department manager without savings rights opens the reports:
        the savings figure is not shown to them, while a Procurement manager has it."""
        report = self.env['procurement.report.sourcing']
        self.assertIn('savings_amount', report.with_user(self.manager).fields_get())
        self.assertNotIn('savings_amount', report.with_user(self.dm).fields_get())
        with self.assertRaises(AccessError):
            report.with_user(self.dm).search([]).read(['savings_amount'])
