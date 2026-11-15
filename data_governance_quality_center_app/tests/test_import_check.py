from odoo.exceptions import UserError

from .acceptance_m3_helpers import M3Case, csv_file, xlsx_file


class TestImportCheck(M3Case):
    """Own tests of the file and row checks of an import session.

    Requirements: FR-DGQ-0058, FR-DGQ-0059
    """

    def findings(self, session):
        return session.sudo().row_ids.sorted('id')

    def test_a_session_without_file_is_refused(self):
        """FR-DGQ-0058: a session with no file cannot be started."""
        session = self.make_session(b'', 'none.csv')
        with self.assertRaises(UserError):
            session.action_start()

    def test_a_file_of_another_type_is_refused(self):
        """FR-DGQ-0058: only CSV and XLSX files are checked."""
        session = self.make_session(b'Name\nA\n', 'notes.txt')
        with self.assertRaises(UserError):
            session.action_start()

    def test_link_by_external_id_and_unknown_external_id(self):
        """FR-DGQ-0059: a link column given by external id is matched by that id."""
        content = csv_file(['Name', 'Country/External ID'], [['Ok', 'base.be'], ['Bad', 'base.nowhere']])
        session = self.check_file(self.make_session(content, 'ids.csv'))
        errors = self.findings(session)
        self.assertEqual(errors.mapped('row_number'), [3])
        self.assertEqual(errors.severity, 'error')

    def test_session_is_kept_per_user(self):
        """FR-DGQ-0062: an operator does not see the session of another operator of the company."""
        session = self.make_session(csv_file(['Name'], [['A']]), 'mine.csv')
        other = self.user('operator', login='dgq_m3_other_operator', company=self.cx)
        self.assertFalse(session.with_user(other).search([('id', '=', session.id)]))
        self.assertTrue(session.with_user(self.operator).search([('id', '=', session.id)]))

    def test_xlsx_numbering_counts_the_header(self):
        """FR-DGQ-0059: in an XLSX file the header is row 1."""
        content = xlsx_file(['Name', 'Country'], [['A', 'Belgium'], ['B', 'Atlantis']])
        session = self.check_file(self.make_session(content, 'rows.xlsx'))
        self.assertEqual(self.findings(session).mapped('row_number'), [3])
