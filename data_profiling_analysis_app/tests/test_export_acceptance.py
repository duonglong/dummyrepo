import io
import re

from odoo.tests import TransactionCase, tagged

SHEETS = ['Summary', 'Field statistics', 'Values', 'Flagged values', 'Outdated records']


@tagged('post_install', '-at_install')
class TestExportAcceptance(TransactionCase):
    """Acceptance tests of T-010 (export a report or snapshot to XLSX and PDF).

    The design names no method for the two export buttons. The tests use two
    methods of ``dpa.snapshot``, ``_export_xlsx()`` and ``_export_pdf()``, each
    returning ``(file name, content bytes)``; the download routes and buttons
    only call them, and the HTTP route itself is left to the build. In Odoo's
    test mode a PDF report is rendered as its HTML, so the PDF test reads the
    text of that content. The snapshot is created directly with its lines.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group = cls.env['res.groups'].search([('name', '=', 'Data Profiling')], limit=1)
        internal = cls.env.ref('base.group_user')
        cls.anna = cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Anna Analyst', 'login': 'anna_exp', 'email': 'anna_exp@example.com',
            'group_ids': [(6, 0, internal.ids + group.ids)],
        })
        cls.partner_model = cls.env['ir.model']._get('res.partner')
        cls.profile = cls.env['dpa.profile'].create({'name': 'Customer quality', 'model_id': cls.partner_model.id})

    def _snapshot(self, flagged=0, outdated=0):
        snapshot = self.env['dpa.snapshot'].create({
            'name': 'Customer quality 2026-12-01',
            'profile_id': self.profile.id,
            'profile_name': 'Customer quality',
            'model_id': self.partner_model.id,
            'model_name': 'res.partner',
            'model_label': 'Contact',
            'run_date': '2026-12-01 12:00:00',
            'is_saved': True,
            'origin': 'manual',
            'user_id': self.anna.id,
            'empty_threshold': 20.0,
            'outdated_days': 365,
            'record_count': 500,
            'outdated_count': outdated,
            'field_line_ids': [(0, 0, {
                'sequence': 1, 'field_name': 'phone', 'field_label': 'Phone', 'field_type': 'char',
                'is_rated': True, 'record_count': 500, 'filled_count': 400, 'empty_count': 100,
                'completeness': 80.0, 'empty_rate': 20.0, 'distinct_count': 1,
                'inconsistent_count': flagged,
                'value_ids': [(0, 0, {'sequence': 1, 'value_label': '0612345678', 'count': 400, 'share': 80.0})],
            })],
        })
        line = snapshot.field_line_ids
        Finding = self.env['dpa.snapshot.finding']
        Finding.create([{
            'snapshot_id': snapshot.id, 'field_line_id': line.id, 'kind': 'inconsistent_format',
            'res_model': 'res.partner', 'res_id': 1000 + i, 'record_name': 'Phone %s' % 'AB'[i],
            'value_label': '06-%d' % i,
        } for i in range(flagged)])
        Finding.create([{
            'snapshot_id': snapshot.id, 'kind': 'outdated', 'res_model': 'res.partner',
            'res_id': 2000 + i, 'record_name': 'Old %03d' % (i + 1),
            'last_update': '2025-01-01 10:00:00', 'days_since_update': 700 - i,
        } for i in range(outdated)])
        self.env.flush_all()
        return snapshot

    def _workbook(self, content):
        try:
            import openpyxl
        except ImportError:  # pragma: no cover - the test image holds openpyxl
            self.fail("openpyxl is needed to read the exported workbook")
        return openpyxl.load_workbook(io.BytesIO(content))

    @staticmethod
    def _rows(sheet):
        return [row for row in sheet.iter_rows(values_only=True) if any(v not in (None, '') for v in row)]

    # -- FR-DPA-0026 -------------------------------------------------------
    def test_xlsx_of_a_snapshot(self):
        """Requirements: FR-DPA-0026, SC-DPA-0026-1 (2 inconsistent phones and 1 outdated record)."""
        snapshot = self._snapshot(flagged=2, outdated=1)
        filename, content = snapshot.with_user(self.anna)._export_xlsx()
        self.assertEqual(filename, 'Customer quality 2026-12-01.xlsx')
        workbook = self._workbook(content)
        self.assertEqual(workbook.sheetnames, SHEETS)
        flagged = self._rows(workbook['Flagged values'])
        outdated = self._rows(workbook['Outdated records'])
        self.assertEqual(len(flagged), 2 + 1, "a header row and 2 flagged values")
        self.assertEqual(len(outdated), 1 + 1, "a header row and 1 outdated record")
        flagged_text = ' '.join(str(v) for row in flagged for v in row)
        self.assertIn('Phone A', flagged_text)
        self.assertIn('Phone B', flagged_text)
        self.assertIn('Old 001', ' '.join(str(v) for row in outdated for v in row))

    def test_xlsx_holds_every_detail(self):
        """Requirements: FR-DPA-0026 (summary figures, one row per field and per value)."""
        snapshot = self._snapshot(flagged=2, outdated=1)
        _filename, content = snapshot.with_user(self.anna)._export_xlsx()
        workbook = self._workbook(content)
        summary = ' '.join(str(v) for row in self._rows(workbook['Summary']) for v in row)
        for expected in ('Contact', '500', '20', '365'):
            self.assertIn(expected, summary)
        statistics = self._rows(workbook['Field statistics'])
        self.assertIn('Phone', ' '.join(str(v) for row in statistics for v in row))
        self.assertEqual(len(statistics), 1 + 1)
        values = self._rows(workbook['Values'])
        self.assertEqual(len(values), 1 + 1)
        self.assertIn('0612345678', ' '.join(str(v) for row in values for v in row))

    def test_xlsx_without_flags_has_only_headers(self):
        """Requirements: FR-DPA-0026 (boundary: no flagged value and no outdated record)."""
        snapshot = self._snapshot()
        _filename, content = snapshot.with_user(self.anna)._export_xlsx()
        workbook = self._workbook(content)
        self.assertEqual(workbook.sheetnames, SHEETS)
        self.assertLessEqual(len(self._rows(workbook['Flagged values'])), 1)
        self.assertLessEqual(len(self._rows(workbook['Outdated records'])), 1)

    # -- FR-DPA-0027 -------------------------------------------------------
    def test_pdf_cuts_long_lists(self):
        """Requirements: FR-DPA-0027, SC-DPA-0027-1 (150 outdated records: 100 listed, then "and 50 more")."""
        snapshot = self._snapshot(outdated=150)
        filename, content = snapshot.with_user(self.anna)._export_pdf()
        self.assertEqual(filename, 'Customer quality 2026-12-01.pdf')
        text = content.decode('utf-8', errors='replace') if isinstance(content, bytes) else content
        listed = set(re.findall(r'Old \d{3}', text))
        self.assertEqual(listed, {'Old %03d' % i for i in range(1, 101)})
        self.assertIn('and 50 more', text)

    def test_pdf_short_list_has_no_more_note(self):
        """Requirements: FR-DPA-0027 (boundary: exactly 100 outdated records are all listed, no "more")."""
        snapshot = self._snapshot(outdated=100)
        _filename, content = snapshot.with_user(self.anna)._export_pdf()
        text = content.decode('utf-8', errors='replace')
        self.assertEqual(len(set(re.findall(r'Old \d{3}', text))), 100)
        self.assertNotRegex(text, r'and \d+ more')

    def test_pdf_holds_summary_and_statistics(self):
        """Requirements: FR-DPA-0027 (the summary, the field statistics and the most frequent values)."""
        snapshot = self._snapshot(flagged=2, outdated=1)
        _filename, content = snapshot.with_user(self.anna)._export_pdf()
        text = content.decode('utf-8', errors='replace')
        for expected in ('Customer quality', 'Contact', 'Phone', '0612345678'):
            self.assertIn(expected, text)
