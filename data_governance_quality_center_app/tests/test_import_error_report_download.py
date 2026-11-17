import io

import openpyxl

from odoo.tests import HttpCase, tagged

from .acceptance_m3_helpers import M3Case, csv_file, xlsx_file

HEADER = ['Name', 'Email', 'Country']
EMAIL_PATTERN = r'^[^@\s]+@[^@\s]+\.[^@\s]+$'


@tagged('post_install', '-at_install')
class TestImportErrorReportDownload(M3Case, HttpCase):
    """The Error Report button of a finished import check downloads the report file.

    Requirements: FR-DGQ-0061
    The test follows the address the button opens, as the person who ran the check. It does not
    click the button in a browser.
    """

    def _checked_session(self, content, file_name):
        rule = self.make_rule(
            'res.partner', 'pattern', ['email'], name='E-mail format', pattern=EMAIL_PATTERN, severity='critical')
        return self.check_file(self.make_session(content, file_name, rules=[rule]))

    def _download(self, session):
        self.authenticate(self.operator.login, self.operator.login)
        url = session.action_download_error_report()['url']
        return self.url_open(url)

    def test_bug_b_0234_csv_error_report_downloads(self):
        """Regression test for B-0234: a CSV check with one bad row downloads its error report."""
        rows = [['Rep %02d' % n, 'rep%s@example.com' % n, 'Belgium'] for n in range(3)]
        rows[1][1] = 'bad'
        response = self._download(self._checked_session(csv_file(HEADER, rows), 'new_contacts.csv'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response.headers['Content-Disposition'])
        self.assertIn('new_contacts_errors.csv', response.headers['Content-Disposition'])
        self.assertIn('Rep 01,bad,Belgium', response.text)

    def test_bug_b_0234_xlsx_error_report_downloads(self):
        """Regression test for B-0234: an XLSX check with one bad row downloads its error report."""
        rows = [['Rep %02d' % n, 'rep%s@example.com' % n, 'Belgium'] for n in range(3)]
        rows[1][1] = 'bad'
        response = self._download(self._checked_session(xlsx_file(HEADER, rows), 'new_contacts.xlsx'))
        self.assertEqual(response.status_code, 200)
        sheet = openpyxl.load_workbook(io.BytesIO(response.content)).active
        self.assertEqual([row[1] for row in list(sheet.iter_rows(values_only=True))[1:]], ['bad'])
