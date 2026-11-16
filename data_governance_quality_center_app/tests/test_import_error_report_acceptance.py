import importlib
import io

import openpyxl

from odoo.tests import tagged

from .acceptance_m3_helpers import M3Case, xlsx_file

HEADER = ['Name', 'Email', 'Country']
EMAIL_PATTERN = r'^[^@\s]+@[^@\s]+\.[^@\s]+$'


@tagged('post_install', '-at_install')
class TestImportErrorReportAcceptance(M3Case):
    """Acceptance tests of the download of the error report of a checked import file.

    Requirements: FR-DGQ-0061
    The Download button is left to the screen review: the test calls ``action_download_error_report``
    and reads the file ``services.import_run.error_report`` builds for the session.
    """

    def test_xlsx_report_holds_only_the_rows_with_findings(self):
        """SC-DGQ-0061-1: findings on rows 3, 5 and 9 give an XLSX of those 3 rows plus severity and message columns."""
        rule = self.make_rule(
            'res.partner', 'pattern', ['email'], name='E-mail format', pattern=EMAIL_PATTERN, severity='critical')
        rows = [['Rep %02d' % n, 'rep%s@example.com' % n, 'Belgium'] for n in range(10)]
        bad = {3: 'bad3', 5: 'bad5', 9: 'bad9'}  # file row number (header is row 1)
        for number, email in bad.items():
            rows[number - 2][1] = email
        session = self.check_file(self.make_session(
            xlsx_file(HEADER, rows), 'contacts.xlsx', rules=[rule]))
        action = session.action_download_error_report()
        self.assertIn('/data_quality/import/%s/error_report' % session.id, action['url'])
        service = importlib.import_module('odoo.addons.data_governance_quality_center_app.services.import_run')
        content, name, mimetype = service.error_report(session)
        self.assertTrue(name.endswith('.xlsx'))
        self.assertIn('spreadsheetml', mimetype)
        sheet = openpyxl.load_workbook(io.BytesIO(content)).active
        table = list(sheet.iter_rows(values_only=True))
        self.assertEqual(len(table), 4, 'the header and the 3 rows with findings')
        self.assertEqual(list(table[0][:3]), HEADER)
        self.assertEqual(len(table[0]), 5)
        self.assertIn('Severity', table[0][3])
        self.assertIn('Message', table[0][4])
        self.assertEqual([row[1] for row in table[1:]], ['bad3', 'bad5', 'bad9'])
        self.assertEqual([row[0] for row in table[1:]], ['Rep 01', 'Rep 03', 'Rep 07'])
        for row in table[1:]:
            self.assertTrue(row[3])
            self.assertTrue(row[4])
