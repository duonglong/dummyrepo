import importlib
import io

import openpyxl

from odoo.tests import tagged

from .acceptance_m3_helpers import M3Case


@tagged('post_install', '-at_install')
class TestProfileExportAcceptance(M3Case):
    """Acceptance tests of the export of a profile.

    Requirements: FR-DGQ-0057
    The Export buttons are left to the screen review: the tests call ``action_export_xlsx`` and
    ``action_export_pdf`` and read the file they lead to.
    """

    def finished_profile(self):
        self.isolate('res.partner')
        self.env['res.partner'].create(
            [{'name': 'x e0', 'company_id': self.cx.id}]
            + [{'name': 'x f%s' % n, 'email': 'x%s@example.com' % n, 'company_id': self.cx.id}
               for n in range(3)])
        return self.run_profile(self.make_profile('res.partner', ['email', 'country_id']))

    def xlsx(self, profile):
        action = profile.action_export_xlsx()
        self.assertIn('/data_quality/xlsx/profile/%s' % profile.id, action['url'])
        service = importlib.import_module('odoo.addons.data_governance_quality_center_app.services.profile_compare')
        return openpyxl.load_workbook(io.BytesIO(service.profile_xlsx(profile)))

    def test_xlsx_shows_model_company_date_user_and_figures(self):
        """SC-DGQ-0057-1: the XLSX names model, company, date, user and the per-field figures."""
        profile = self.finished_profile()
        book = self.xlsx(profile)
        self.assertGreaterEqual(len(book.sheetnames), 2)
        self.assertIn('Completeness', book.sheetnames)
        for sheet in book:
            text = [str(c) for row in sheet.iter_rows(max_row=6, values_only=True) for c in row]
            for fact in ('Contact', self.cx.name, self.operator.name, profile.create_date.strftime('%Y-%m-%d')):
                self.assertIn(fact, text)
        cells = [c for row in book['Completeness'].iter_rows(values_only=True) for c in row]
        self.assertIn('Email', cells)

    def test_pdf_report_shows_the_same_figures(self):
        """SC-DGQ-0057-1: the PDF report names model, company, date, user and the per-field figures."""
        profile = self.finished_profile()
        action = profile.action_export_pdf()
        html = self.env['ir.actions.report']._render_qweb_html(action['report_name'], profile.ids)[0].decode()
        for fact in ('Contact', self.cx.name, self.operator.name,
                     profile.create_date.strftime('%Y-%m-%d'), 'Completeness', 'Email'):
            self.assertIn(fact, html)
