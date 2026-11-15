from odoo import Command
from odoo.exceptions import UserError

from .acceptance_m3_helpers import M3Case, csv_file

HEADER = ['Name', 'Email', 'Country']
EMAIL_PATTERN = r'^[^@\s]+@[^@\s]+\.[^@\s]+$'


class TestImportRunAcceptance(M3Case):
    """Acceptance tests of T-024: strict and warning-only modes of the import of a checked file.

    The import is the session's button ``action_import`` (the design names no method for it; the
    import dialog of the design's file list is left to the build). The import may run in the
    background: the test lets the job runner work after the button. The download of the error report
    (FR-DGQ-0061) is reported as unclear: no method or route is named. In SC-DGQ-0060-2 the rows
    listed as not imported are left untested, for the design names no place for that list; the
    findings of those rows stay in the session.

    The operator also holds Odoo's contact creation group: FR-DGQ-0060 says the import goes through
    Odoo's own import with all its usual rules, run as the session's user, so a user who may not
    create contacts imports none (FR-DGQ-0071).

    Requirements: FR-DGQ-0060, FR-DGQ-0062
    """

    def setUp(self):
        super().setUp()
        self.operator.write({'group_ids': [Command.link(self.env.ref('base.group_partner_manager').id)]})

    def file_with(self, errors, warnings, total=200):
        rows = [['Imp %03d' % n, 'imp%s@example.com' % n, 'Belgium'] for n in range(total)]
        for n in range(errors):
            rows[10 + n][2] = 'Atlantis'  # no such country: an error
        for n in range(warnings):
            rows[100 + n][1] = 'abc'  # fails the warning rule
        return csv_file(HEADER, rows)

    def checked_session(self, mode, errors, warnings):
        rule = self.make_rule(
            'res.partner', 'pattern', ['email'], name='E-mail format', pattern=EMAIL_PATTERN, severity='warning')
        session = self.check_file(self.make_session(
            self.file_with(errors, warnings), 'contacts.csv', mode=mode, rules=[rule]))
        self.assertEqual((session.error_row_count, session.warning_count), (errors, warnings))
        return session

    def imported(self):
        return self.env['res.partner'].with_context(active_test=False).search_count([('name', '=like', 'Imp %')])

    def test_strict_mode_refuses_while_rows_are_in_error(self):
        """SC-DGQ-0060-1: a strict session with 2 rows in error out of 200 refuses the import and imports nothing."""
        session = self.checked_session('strict', 2, 0)
        with self.assertRaises(UserError) as caught:
            session.action_import()
        self.assertRegex(str(caught.exception), r'\b2\b')
        self.assertIn('error', str(caught.exception).lower())
        self.assertEqual(self.imported(), 0)
        session.invalidate_recordset()
        self.assertNotEqual(session.import_state, 'imported')

    def test_strict_mode_imports_a_file_without_errors(self):
        """FR-DGQ-0060: a strict session with warnings only is allowed to import every row."""
        session = self.checked_session('strict', 0, 3)
        session.action_import()
        self.drain_job(session)
        session.invalidate_recordset()
        self.assertEqual(self.imported(), 200)
        self.assertEqual(session.import_state, 'imported')
        self.assertEqual(session.imported_count, 200)
        self.assertTrue(session.date_imported)

    def test_warning_only_mode_leaves_out_the_rows_in_error(self):
        """SC-DGQ-0060-2: 2 rows in error and 5 with warnings out of 200: 198 contacts are created."""
        session = self.checked_session('warning', 2, 5)
        session.action_import()
        self.drain_job(session)
        session.invalidate_recordset()
        self.assertEqual(self.imported(), 198)
        self.assertEqual(session.import_state, 'imported')
        self.assertEqual(session.imported_count, 198)
        names = set(self.env['res.partner'].search([('name', '=like', 'Imp %')]).mapped('name'))
        self.assertNotIn('Imp 010', names)
        self.assertNotIn('Imp 011', names)
        self.assertIn('Imp 100', names)  # a warning does not stop a row

    def test_a_session_only_checked_imports_nothing(self):
        """FR-DGQ-0060: checking alone never imports; the session stays not imported."""
        session = self.checked_session('warning', 2, 5)
        self.assertEqual(self.imported(), 0)
        self.assertEqual(session.import_state, 'not_imported')

    def test_history_shows_the_checked_and_imported_file_to_a_manager(self):
        """SC-DGQ-0062-1 (FR-DGQ-0062): the operator checked 'contacts_nov.csv' in strict mode and imported it.

        A manager of the same company finds the session with the file, Contact, strict, the user,
        the dates, the counts and imported.
        """
        rule = self.make_rule(
            'res.partner', 'pattern', ['email'], name='E-mail format', pattern=EMAIL_PATTERN, severity='warning')
        session = self.check_file(self.make_session(
            self.file_with(0, 3, total=20), 'contacts_nov.csv', mode='strict', rules=[rule]))
        session.action_import()
        self.drain_job(session)
        self.env.flush_all()
        self.env.invalidate_all()
        seen = self.env['data.quality.import.session'].with_user(self.manager_a).search(
            [('id', '=', session.id)])
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen.file_name, 'contacts_nov.csv')
        self.assertEqual(seen.model_id.model, 'res.partner')
        self.assertEqual(seen.mode, 'strict')
        self.assertEqual(seen.user_id, self.operator)
        self.assertTrue(seen.date_checked)
        self.assertEqual((seen.row_count, seen.error_count, seen.warning_count), (20, 0, 3))
        self.assertEqual(seen.import_state, 'imported')
        self.assertEqual(seen.imported_count, 20)
        self.assertTrue(seen.date_imported)
