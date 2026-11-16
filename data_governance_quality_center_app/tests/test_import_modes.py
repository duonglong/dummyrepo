import base64
import csv
import datetime
import io
from unittest.mock import patch

import openpyxl
import xlsxwriter

from odoo.exceptions import AccessError, UserError

from odoo.addons.base_import.models.base_import import Base_ImportImport

from ..services import import_run
from .acceptance_m3_helpers import M3Case, csv_file, xlsx_file

HEADER = ['Name', 'Email', 'Country']


class TestImportModes(M3Case):
    """Own tests of the import of a checked file, its error report and its history.

    The buttons (Import, Error Report) are left to the screen review: the tests call their methods.

    Requirements: FR-DGQ-0060, FR-DGQ-0061, FR-DGQ-0062
    """

    def setUp(self):
        super().setUp()
        # the import runs as the person who asks for it: she needs the right to create contacts
        self.operator.write({'group_ids': [(4, self.env.ref('base.group_partner_manager').id)]})

    def rows(self, total=12, bad=()):
        return [
            ['Mod %02d' % n, 'mod%s@example.com' % n, 'Atlantis' if n in bad else 'Belgium']
            for n in range(total)]

    def imported(self):
        return self.env['res.partner'].with_context(active_test=False).search_count([('name', '=like', 'Mod %')])

    def checked(self, content, name, mode='warning'):
        return self.check_file(self.make_session(content, name, mode=mode))

    def test_changed_file_is_checked_again_before_import_in_strict_mode(self):
        """FR-DGQ-0060: a file changed after the check is checked again; the new error refuses the import."""
        session = self.checked(csv_file(HEADER, self.rows()), 'changed.csv', mode='strict')
        self.assertEqual(session.error_row_count, 0)
        session.sudo().write({'file': base64.b64encode(csv_file(HEADER, self.rows(bad=(3,)))).decode()})
        session.action_import()
        self.drain_job(session)
        self.assertEqual(session.error_row_count, 1)
        self.assertIn('1', session.error_message)
        self.assertEqual(session.import_state, 'not_imported')
        self.assertEqual(self.imported(), 0)

    def test_changed_file_is_checked_again_then_imported(self):
        """FR-DGQ-0060: after the second check the rows of the changed file are imported, not those of the first."""
        session = self.checked(csv_file(HEADER, self.rows(bad=(3,))), 'again.csv')
        session.sudo().write({'file': base64.b64encode(csv_file(HEADER, self.rows(total=5))).decode()})
        session.action_import()
        self.drain_job(session)
        self.drain_job(session)
        self.assertEqual(session.import_state, 'imported')
        self.assertEqual(session.imported_count, 5)
        self.assertEqual(self.imported(), 5)

    def test_a_file_is_imported_once(self):
        """FR-DGQ-0060: an imported session cannot be imported again, and an unchecked one cannot be imported."""
        session = self.make_session(csv_file(HEADER, self.rows()), 'once.csv')
        with self.assertRaises(UserError):
            session.action_import()
        self.check_file(session)
        session.action_import()
        self.drain_job(session)
        with self.assertRaises(UserError):
            session.action_import()
        self.assertEqual(self.imported(), 12)

    def test_rows_are_imported_in_batches(self):
        """FR-DGQ-0060: a file longer than a batch is imported completely, the rows with errors left out."""
        session = self.checked(csv_file(HEADER, self.rows(total=25, bad=(4, 20))), 'batches.csv')
        with patch('odoo.addons.data_governance_quality_center_app.models.import_session.JOB_BATCH_SIZE', 10):
            session.action_import()
            self.drain_job(session)
        self.assertEqual(session.imported_count, 23)
        self.assertEqual(self.imported(), 23)

    def test_error_report_of_an_xlsx_file(self):
        """SC-DGQ-0061-1: findings on rows 3, 5 and 9 give an XLSX with those rows and the severity and messages."""
        rows = self.rows(total=10)
        for number in (3, 5, 9):
            rows[number - 2][2] = 'Atlantis'
        session = self.checked(xlsx_file(HEADER, rows), 'report.xlsx')
        content, name, mimetype = import_run.error_report(session)
        self.assertEqual(name, 'report_errors.xlsx')
        self.assertIn('spreadsheetml', mimetype)
        sheet = [tuple(row) for row in openpyxl.load_workbook(io.BytesIO(content)).active.iter_rows(values_only=True)]
        self.assertEqual(sheet[0], tuple(HEADER) + ('Severity', 'Messages'))
        self.assertEqual([row[0] for row in sheet[1:]], ['Mod 01', 'Mod 03', 'Mod 07'])
        self.assertEqual({row[3] for row in sheet[1:]}, {'Error'})
        self.assertTrue(all('Atlantis' in row[4] for row in sheet[1:]))

    def test_error_report_of_a_csv_file_keeps_clean_rows_out(self):
        """FR-DGQ-0061: a CSV error report holds only the rows with findings, in the format of the file."""
        session = self.checked(csv_file(HEADER, self.rows(bad=(2, 7))), 'report.csv')
        content, name, mimetype = import_run.error_report(session)
        table = list(csv.reader(io.StringIO(content.decode())))
        self.assertEqual(table[0], HEADER + ['Severity', 'Messages'])
        self.assertEqual([row[0] for row in table[1:]], ['Mod 02', 'Mod 07'])
        self.assertEqual(mimetype, 'text/csv')

    def test_error_report_needs_a_finished_check(self):
        """FR-DGQ-0061: a session not checked yet has no error report."""
        session = self.make_session(csv_file(HEADER, self.rows()), 'early.csv')
        with self.assertRaises(UserError):
            import_run.error_report(session)

    def test_history_shows_the_session_to_its_user_and_to_managers_of_the_company(self):
        """SC-DGQ-0062-1: a manager of the company sees the file, model, mode, user, counts and that it was imported."""
        session = self.make_session(csv_file(HEADER, self.rows(bad=(1,))), 'contacts_nov.csv', mode='strict')
        self.check_file(session)
        session.sudo().write({'file': base64.b64encode(csv_file(HEADER, self.rows())).decode()})
        session.action_import()
        self.drain_job(session)
        self.drain_job(session)
        seen = session.with_user(self.manager_a).search([('id', '=', session.id)])
        self.assertEqual(
            (seen.file_name, seen.res_model, seen.mode, seen.user_id, seen.row_count, seen.import_state),
            ('contacts_nov.csv', 'res.partner', 'strict', self.operator, 12, 'imported'))
        self.assertTrue(seen.date_checked and seen.date_imported)
        self.assertEqual((seen.error_count, seen.warning_count, seen.imported_count), (0, 0, 12))
        self.assertTrue(session.with_user(self.operator).search([('id', '=', session.id)]))
        with self.assertRaises(AccessError):
            session.with_user(self.viewer).search([('id', '=', session.id)])

    def import_one_batch_then_cancel(self, session):
        """Run the import of a session for one batch of 10 rows, then cancel it, as a user does."""
        with patch('odoo.addons.data_governance_quality_center_app.models.import_session.JOB_BATCH_SIZE', 10):
            session.action_import()
            session.write({'state': 'running'})
            processed, remaining = session._dgq_run_batch()
            session._dgq_record_batch(processed, remaining)
        self.assertTrue(remaining)
        session.action_cancel()

    def test_rows_in_error_are_listed_as_not_imported_and_stay_in_the_report(self):
        """SC-DGQ-0060-2: 2 rows in error and 5 with warnings: 5 rows left out of none, the 2 are listed, report kept."""
        rule = self.make_rule(
            'res.partner', 'pattern', ['email'], name='Mail', pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$',
            severity='warning')
        rows = self.rows(total=20, bad=(3, 8))
        for index in (11, 12, 13, 14, 15):
            rows[index][1] = 'abc'
        session = self.check_file(self.make_session(csv_file(HEADER, rows), 'left.csv', mode='warning', rules=[rule]))
        session.action_import()
        self.drain_job(session)
        self.assertEqual(self.imported(), 18)
        self.assertEqual(session.left_out_ids.mapped('row_number'), [5, 10])
        table = list(csv.reader(io.StringIO(import_run.error_report(session)[0].decode())))
        self.assertEqual(len(table), 1 + 2 + 5)

    def test_a_cancelled_import_keeps_its_count_and_cannot_be_repeated(self):
        """FR-DGQ-0062: after a cancel between batches the import is failed with its count; no Check, no Import."""
        session = self.checked(csv_file(HEADER, self.rows(total=25)), 'cancel.csv')
        self.import_one_batch_then_cancel(session)
        self.assertEqual((session.import_state, session.imported_count), ('failed', 10))
        with self.assertRaises(UserError):
            session.action_start()
        with self.assertRaises(UserError):
            session.action_import()
        self.assertEqual((session.imported_count, self.imported()), (10, 10))

    def test_a_crashed_import_that_is_resumed_and_finishes_is_imported(self):
        """FR-DGQ-0062: crash after one batch, resume, finish: imported, dated, with the full count."""
        session = self.checked(csv_file(HEADER, self.rows(total=25)), 'resume.csv')
        with patch('odoo.addons.data_governance_quality_center_app.models.import_session.JOB_BATCH_SIZE', 10):
            session.action_import()
            session.write({'state': 'running'})
            session._dgq_record_batch(*session._dgq_run_batch())
            session.write({'state': 'failed'})
            self.assertEqual(session.import_state, 'failed')
            session.action_resume()
            self.assertEqual(session.import_state, 'not_imported')
            session.write({'state': 'running'})
            remaining = True
            while remaining:
                processed, remaining = session._dgq_run_batch()
                session._dgq_record_batch(processed, remaining)
        self.assertEqual((session.import_state, session.imported_count), ('imported', 25))
        self.assertTrue(session.date_imported)

    def test_header_row_errors_are_not_listed_as_not_imported(self):
        """FR-DGQ-0060: only data rows in error are left out, not the findings on the header row."""
        session = self.checked(csv_file(HEADER, self.rows(bad=(3,))), 'head.csv')
        session.sudo().row_ids.create({
            'session_id': session.id, 'row_number': 1, 'severity': 'error', 'message': 'Missing column'})
        session.action_import()
        self.assertTrue(session.left_out_ids)
        self.assertNotIn(1, session.left_out_ids.mapped('row_number'))

    def test_a_cancelled_check_can_be_checked_again(self):
        """FR-DGQ-0058: a session with nothing imported can still be checked again after a cancel."""
        session = self.make_session(csv_file(HEADER, self.rows()), 'again.csv')
        session.action_start()
        session.action_cancel()
        session.action_start()
        self.assertEqual(session.state, 'queued')

    def test_xlsx_date_cells_are_imported_and_kept_in_the_error_report(self):
        """FR-DGQ-0060, FR-DGQ-0061: a date-time cell reaches the import and the report as the native import reads it."""
        stamp = datetime.datetime(2024, 1, 5, 10, 0, 0)
        buffer = io.BytesIO()
        book = xlsxwriter.Workbook(buffer, {'in_memory': True})
        sheet = book.add_worksheet()
        style = book.add_format({'num_format': 'yyyy-mm-dd hh:mm:ss'})
        sheet.write_row(0, 0, ['Name', 'Email', 'Country', 'Date'])
        for index, (name, country) in enumerate([('Mod 00', 'Belgium'), ('Mod 01', 'Atlantis')], start=1):
            sheet.write_row(index, 0, [name, '%s@example.com' % index, country])
            sheet.write_datetime(index, 3, stamp, style)
        book.close()
        session = self.checked(buffer.getvalue(), 'dates.xlsx')
        self.assertEqual(session.error_row_count, 1)
        session.action_import()
        self.drain_job(session)
        self.assertEqual((session.import_state, self.imported()), ('imported', 1))
        report = openpyxl.load_workbook(io.BytesIO(import_run.error_report(session)[0])).active
        self.assertEqual([cell.value for cell in report[2]][3], stamp)

    def test_a_refused_batch_is_recorded_and_counted(self):
        """FR-DGQ-0060: when the native import refuses a batch, the session is failed, with its error counted."""
        session = self.checked(csv_file(HEADER, self.rows(total=5)), 'refused.csv')
        refusal = {'messages': [{'type': 'error', 'message': 'No', 'rows': {'from': 1, 'to': 1}}], 'ids': []}
        with patch.object(Base_ImportImport, 'execute_import', return_value=refusal):
            session.action_import()
            self.drain_job(session)
        self.assertEqual((session.import_state, session.imported_count, session.error_row_count), ('failed', 0, 1))
        self.assertIn('row 3', session.error_message)
        self.assertEqual(session.row_ids.filtered(lambda finding: finding.message == 'No').row_number, 3)

    def test_an_import_stops_when_the_file_changes_while_it_runs(self):
        """FR-DGQ-0060: a file written during the import fails the job instead of importing the wrong rows."""
        session = self.checked(csv_file(HEADER, self.rows(total=25)), 'moving.csv')
        with patch('odoo.addons.data_governance_quality_center_app.models.import_session.JOB_BATCH_SIZE', 10):
            session.action_import()
            session.sudo().write({'file': base64.b64encode(csv_file(HEADER, self.rows(total=3))).decode()})
            self.drain_job(session)
        self.assertEqual((session.state, session.import_state, self.imported()), ('failed', 'failed', 0))
