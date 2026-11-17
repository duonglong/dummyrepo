from odoo import fields
from odoo.exceptions import UserError

from .acceptance_m3_helpers import M3Case, csv_file, xlsx_file

HEADER = ['Name', 'Email', 'Country']
EMAIL_PATTERN = r'^[^@\s]+@[^@\s]+\.[^@\s]+$'


class TestImportCheckAcceptance(M3Case):
    """Acceptance tests of T-023: check an import file without importing it, row by row.

    A session is filled in on its form (model, company, file, file name, mode, rules, duplicate
    fields), saved and checked with the job mixin's ``action_start``; findings are the lines of
    ``row_ids``. Whether the first data row is numbered 1 or 2 (a header counted or not) is left open
    by the scenarios: the row numbers are read with one shift for the whole file, 0 or 1. The row
    required rule of a contact name is a quality rule here (critical, as the scenario's missing name
    is an error), since a contact's name is not required by its field; the required check of the
    field itself is tested on products. The history of FR-DGQ-0062 needs only the session model.

    Requirements: FR-DGQ-0058, FR-DGQ-0059, FR-DGQ-0062
    """

    def contact_count(self):
        return self.env['res.partner'].with_context(active_test=False).search_count([])

    def rows_of(self, session, number, severity=None):
        rows = session.sudo().row_ids.filtered(lambda row: row.row_number == number)
        return rows.filtered(lambda row: row.severity == severity) if severity else rows

    def on(self, rows, *names):
        return rows.filtered(lambda row: (row.field_name or '').lower() in names
                             or (row.column or '').lower() in names)

    # ---- FR-DGQ-0058 -------------------------------------------------------

    def test_check_a_csv_file_writes_nothing(self):
        """SC-DGQ-0058-1: a CSV file of 200 contacts gives a session with 200 rows; the contacts do not change."""
        before = self.contact_count()
        content = csv_file(HEADER, [['Imp %03d' % n, 'imp%s@example.com' % n, 'Belgium'] for n in range(200)])
        session = self.check_file(self.make_session(content, 'contacts.csv'))
        self.assertEqual(session.state, 'done')
        self.assertEqual(session.row_count, 200)
        self.assertEqual(session.error_count, 0)
        self.assertEqual(self.contact_count(), before)
        self.assertEqual(session.import_state, 'not_imported')
        self.assertTrue(session.date_checked)

    def test_check_an_xlsx_file(self):
        """FR-DGQ-0058: an XLSX file is checked as a CSV file is."""
        content = xlsx_file(HEADER, [['Xl %s' % n, 'xl%s@example.com' % n, 'Belgium'] for n in range(5)])
        session = self.check_file(self.make_session(content, 'contacts.xlsx'))
        self.assertEqual(session.state, 'done')
        self.assertEqual(session.row_count, 5)

    def test_a_file_of_more_than_100000_rows_is_refused(self):
        """SC-DGQ-0058-2: an XLSX file of 150,000 rows is refused with a message of at most 100,000 rows."""
        content = xlsx_file(['Name'], [['Big %s' % n] for n in range(150000)])
        with self.assertRaises(UserError) as caught:
            self.check_file(self.make_session(content, 'big.xlsx'))
        self.assertRegex(str(caught.exception), r'100[,.\s]?000')

    def test_a_file_of_100001_rows_is_refused(self):
        """FR-DGQ-0058 (just above the limit): 100,001 rows are refused."""
        content = csv_file(['Name'], [['R%s' % n] for n in range(100001)])
        with self.assertRaises(UserError) as caught:
            self.check_file(self.make_session(content, 'edge.csv'))
        self.assertRegex(str(caught.exception), r'100[,.\s]?000')

    def test_a_file_of_more_than_20_mb_is_refused(self):
        """FR-DGQ-0058: a file of 21 MB is refused with a message naming 20 MB."""
        content = b'Name\n' + (b'x' * 1023 + b'\n') * 21000
        with self.assertRaises(UserError) as caught:
            self.check_file(self.make_session(content, 'heavy.csv'))
        self.assertRegex(str(caught.exception), r'20\s?MB')

    def test_an_empty_file_is_refused(self):
        """FR-DGQ-0058: a file with no content is refused with a message."""
        with self.assertRaises(UserError):
            self.check_file(self.make_session(b'', 'empty.csv'))

    def test_a_file_that_cannot_be_read_is_refused(self):
        """FR-DGQ-0058: a file that is not a spreadsheet is refused with a message."""
        with self.assertRaises(UserError):
            self.check_file(self.make_session(b'this is not a spreadsheet', 'broken.xlsx'))

    # ---- FR-DGQ-0059 -------------------------------------------------------

    def test_row_level_findings(self):
        """SC-DGQ-0059-1: no name (row 3), unknown country (row 5), repeated row (row 7), bad e-mail (row 9)."""
        name_rule = self.make_rule('res.partner', 'required', ['name'], severity='critical')
        mail_rule = self.make_rule(
            'res.partner', 'pattern', ['email'], name='E-mail format', pattern=EMAIL_PATTERN, severity='warning')
        rows = [['Row %s' % n, 'row%s@example.com' % n, 'Belgium'] for n in range(1, 13)]
        rows[2][0] = ''  # data row 3: no name
        rows[4][2] = 'Atlantis'  # data row 5: no such country
        rows[6][0] = rows[1][0]  # data row 7 repeats data row 2 on the name
        rows[8][1] = 'abc'  # data row 9: e-mail that fails the warning rule
        session = self.check_file(self.make_session(
            csv_file(HEADER, rows), 'findings.csv', rules=[name_rule, mail_rule],
            duplicate_fields=[self.fld('res.partner', 'name')]))
        errors = session.sudo().row_ids.filtered(lambda row: row.severity == 'error')
        shift = min(errors.mapped('row_number')) - 3
        self.assertIn(shift, (0, 1), 'row numbers count from the first data row or from the header')
        self.assertTrue(self.on(self.rows_of(session, 3 + shift, 'error'), 'name'))
        self.assertTrue(self.on(self.rows_of(session, 5 + shift, 'error'), 'country_id', 'country'))
        repeat = self.rows_of(session, 7 + shift, 'error')
        self.assertTrue(repeat.filtered(lambda row: str(2 + shift) in (row.message or '')),
                        'the repeat names the row it repeats')
        warning = self.on(self.rows_of(session, 9 + shift, 'warning'), 'email', 'e-mail')
        self.assertTrue(warning)
        self.assertEqual(set(errors.mapped('row_number')), {3 + shift, 5 + shift, 7 + shift})
        self.assertEqual(
            set(session.sudo().row_ids.filtered(lambda row: row.severity == 'warning').mapped('row_number')),
            {9 + shift})
        self.assertEqual(session.error_row_count, 3)
        self.assertTrue(all(row.message for row in session.sudo().row_ids))

    def test_a_required_field_of_a_model_is_an_error(self):
        """FR-DGQ-0059: an empty required field (the name of a product) is an error with its row and column."""
        content = csv_file(['Name'], [['Chair'], [''], ['Desk']])
        session = self.check_file(self.make_session(content, 'products.csv', model_name='product.template'))
        errors = session.sudo().row_ids.filtered(lambda row: row.severity == 'error')
        self.assertEqual(len(set(errors.mapped('row_number'))), 1)
        self.assertTrue(self.on(errors, 'name'))

    def test_a_link_matching_several_records_is_an_error(self):
        """FR-DGQ-0059: a link value that matches several records is an error on that column."""
        for n in (1, 2):
            self.env['res.users'].create({'name': 'DGQ Twin', 'login': 'dgq_twin_%s' % n})
        content = csv_file(['Name', 'Salesperson'], [['Sales ok', ''], ['Sales twin', 'DGQ Twin']])
        session = self.check_file(self.make_session(content, 'twins.csv'))
        errors = session.sudo().row_ids.filtered(lambda row: row.severity == 'error')
        self.assertTrue(self.on(errors, 'user_id', 'salesperson'))
        self.assertEqual(len(set(errors.mapped('row_number'))), 1)

    def test_a_clean_file_has_no_finding(self):
        """FR-DGQ-0059 (boundary): a file without a problem leaves no finding."""
        rows = [['Clean %s' % n, 'clean%s@example.com' % n, 'Belgium'] for n in range(5)]
        session = self.check_file(self.make_session(
            csv_file(HEADER, rows), 'clean.csv', duplicate_fields=[self.fld('res.partner', 'name')]))
        self.assertFalse(session.row_ids)
        self.assertEqual((session.error_count, session.warning_count, session.error_row_count), (0, 0, 0))

    # ---- FR-DGQ-0062 -------------------------------------------------------

    def test_history_shows_the_checked_file_to_its_user_and_to_managers_of_the_company(self):
        """SC-DGQ-0062-1: Ann checked 'contacts_nov.csv' in strict mode and imported it; a manager of her company sees it.

        The session is written as the checking and the import leave it; the import step itself is T-024's.
        """
        ann = self.user('operator', login='dgq_m3_hist_ann', company=self.cx)
        session = self.env['data.quality.import.session'].sudo().create({
            'name': 'contacts_nov.csv', 'file_name': 'contacts_nov.csv', 'file_type': 'csv',
            'model_id': self.model('res.partner').id, 'company_id': self.cx.id, 'mode': 'strict',
            'user_id': ann.id, 'row_count': 200, 'error_count': 0, 'warning_count': 5,
            'date_checked': fields.Datetime.now(), 'import_state': 'imported',
            'date_imported': fields.Datetime.now(), 'imported_count': 200})
        for reader in (ann, self.manager_a):
            seen = session.with_user(reader).search([('id', '=', session.id)])
            self.assertEqual(len(seen), 1, '%s sees the session' % reader.login)
            self.assertEqual(seen.file_name, 'contacts_nov.csv')
            self.assertEqual(seen.model_id.model, 'res.partner')
            self.assertEqual((seen.mode, seen.user_id), ('strict', ann))
            self.assertEqual((seen.row_count, seen.error_count, seen.warning_count), (200, 0, 5))
            self.assertEqual(seen.import_state, 'imported')
            self.assertTrue(seen.date_checked and seen.date_imported)
        stranger = self.user('manager', login='dgq_m3_hist_other', company=self.cy)
        self.assertFalse(session.with_user(stranger).search([('id', '=', session.id)]))
