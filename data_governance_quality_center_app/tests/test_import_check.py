from unittest.mock import patch

from odoo.exceptions import UserError

from ..services import import_check
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

    def test_a_critical_required_rule_on_a_field_the_file_has_no_column_for_is_an_error(self):
        """FR-DGQ-0059: a rule is judged on a field without a column as the import would create it."""
        rule = self.make_rule('res.partner', 'required', ['function'], severity='critical', name='Job needed')
        session = self.check_file(self.make_session(csv_file(['Name'], [['A'], ['B']]), 'nocolumn.csv', rules=[rule]))
        failures = self.findings(session).filtered(lambda row: row.message.startswith('Job needed'))
        self.assertEqual(failures.mapped('row_number'), [2, 3])
        self.assertEqual(set(failures.mapped('severity')), {'error'})

    def test_a_rule_that_cannot_run_on_a_file_is_named(self):
        """FR-DGQ-0059: a rule left out of the check is a warning on the header row, never silence."""
        registry_class = type(self.env['data.quality.registry'])

        def extended(this):
            return super(registry_class, this)._dgq_validators() + [
                {'key': 'dgq_file_validator', 'label': 'File', 'handler': lambda env, rule, records: {}}]

        patcher = patch.object(registry_class, '_dgq_validators', extended, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        rule = self.make_rule('res.partner', 'custom', validator_key='dgq_file_validator', name='Foreign rule')
        session = self.check_file(self.make_session(csv_file(['Name'], [['A']]), 'skipped.csv', rules=[rule]))
        notes = self.findings(session).filtered(lambda row: row.message.startswith('Foreign rule'))
        self.assertEqual(notes.mapped('row_number'), [1])
        self.assertEqual(set(notes.mapped('severity')), {'warning'})

    def test_an_unknown_value_of_a_many2many_column_is_an_error(self):
        """FR-DGQ-0059: each comma separated value of a many-to-many column is matched."""
        self.env['res.partner.category'].create({'name': 'DGQ known tag'})
        content = csv_file(['Name', 'Tags'], [['A', 'DGQ known tag'], ['B', 'DGQ known tag, DGQ nowhere tag']])
        session = self.check_file(self.make_session(content, 'tags.csv'))
        errors = self.findings(session).filtered(lambda row: row.field_name == 'category_id')
        self.assertEqual(errors.mapped('row_number'), [3])
        self.assertIn('DGQ nowhere tag', errors.message)

    def test_a_selection_value_given_by_its_label_fills_a_required_rule(self):
        """FR-DGQ-0059: a column the check reads as the import does is not judged as empty."""
        rule = self.make_rule('res.partner', 'required', ['type'], severity='critical', name='Type needed')
        content = csv_file(['Name', 'Address Type'], [['A', 'Invoice'], ['B', 'Nonsense']])
        session = self.check_file(self.make_session(content, 'selection.csv', rules=[rule]))
        findings = self.findings(session).filtered(lambda row: row.field_name == 'type')
        self.assertEqual(findings.mapped('row_number'), [3])
        self.assertEqual(len(findings), 1, "a value that cannot be read is not also judged as empty")

    def test_an_external_id_without_module_is_read_as_the_native_import_reads_it(self):
        """FR-DGQ-0059: an id without module is looked up in the module '__import__'."""
        country = self.env.ref('base.be')
        self.env['ir.model.data'].create(
            {'module': '__import__', 'name': 'dgq_country', 'model': 'res.country', 'res_id': country.id})
        content = csv_file(['Name', 'Country/External ID'], [['A', 'dgq_country'], ['B', 'dgq_missing']])
        session = self.check_file(self.make_session(content, 'noprefix.csv'))
        self.assertEqual(self.findings(session).mapped('row_number'), [3])

    def test_a_repeat_on_a_key_given_by_external_id_is_found(self):
        """FR-DGQ-0059: a duplicate key column given by external id is compared by its text."""
        content = csv_file(['Name', 'Country/External ID'], [['A', 'base.be'], ['B', 'base.be']])
        session = self.check_file(self.make_session(
            content, 'repeat.csv', duplicate_fields=[self.fld('res.partner', 'country_id')]))
        repeats = self.findings(session).filtered(lambda row: row.message.startswith('Repeats'))
        self.assertEqual(repeats.mapped('row_number'), [3])

    def test_the_limits_are_inclusive(self):
        """FR-DGQ-0058: a file of exactly the largest size or row count is checked, one more is refused."""
        content = csv_file(['Name'], [['A'], ['B'], ['C']])
        with patch.object(import_check, 'IMPORT_MAX_ROWS', 3), \
                patch.object(import_check, 'IMPORT_MAX_BYTES', len(content)):
            self.check_file(self.make_session(content, 'limit.csv'))
            with self.assertRaises(UserError):
                self.make_session(csv_file(['Name'], [['A'], ['B'], ['C'], ['D']]), 'over.csv').action_start()
            with self.assertRaises(UserError):
                self.make_session(content + b' ', 'big.csv').action_start()

    def test_a_file_is_kept_per_session_and_released(self):
        """NFR-DGQ-0003: no state outside the cursor; the file of a session is dropped on request."""
        first = self.make_session(csv_file(['Name'], [['A']]), 'first.csv')
        second = self.make_session(csv_file(['Name'], [['B'], ['C']]), 'second.csv')
        first.file_checksum = import_check.file_checksum(first)
        second.file_checksum = import_check.file_checksum(second)
        self.assertEqual(len(import_check.load_file(first).rows), 1)
        self.assertEqual(len(import_check.load_file(second).rows), 2)
        self.assertEqual(len(import_check.load_file(first).rows), 1)
        import_check.forget_file(first)
        self.assertNotIn(first.id, self.env.cr.cache[import_check.FILE_CACHE_KEY])
        self.assertIn(second.id, self.env.cr.cache[import_check.FILE_CACHE_KEY])

    def test_the_file_type_follows_the_name(self):
        """FR-DGQ-0058: a name that is neither .csv nor .xlsx leaves no file type."""
        session = self.make_session(csv_file(['Name'], [['A']]), 'data.csv')
        self.assertEqual(session.file_type, 'csv')
        session.file_name = 'notes.txt'
        self.assertFalse(session.file_type)

    def test_a_link_rule_is_named_as_not_checked(self):
        """FR-DGQ-0059: a link integrity rule needs saved records; the check says so instead of failing."""
        rule = self.make_rule('res.partner', 'link', ['country_id'], severity='critical', name='Country holds')
        session = self.check_file(self.make_session(
            csv_file(['Name', 'Country'], [['A', 'Belgium']]), 'link.csv', rules=[rule]))
        self.assertEqual(session.state, 'done')
        notes = self.findings(session).filtered(lambda row: row.message.startswith('Country holds'))
        self.assertEqual(notes.mapped('row_number'), [1])
