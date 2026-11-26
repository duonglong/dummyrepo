import io
import zipfile
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, tagged

from ..services import xlsx
from .common import DpaCase


@tagged('post_install', '-at_install')
class TestExport(DpaCase):
    """Requirements: FR-DPA-0026, FR-DPA-0027 (who may export, what the PDF lists)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        model = cls.env['ir.model']._get('res.partner')
        cls.snapshot = cls.env['dpa.snapshot'].create({
            'name': 'Q 2026-12-01', 'profile_name': 'Q', 'model_id': model.id, 'model_name': 'res.partner',
            'model_label': 'Contact', 'run_date': '2026-12-01 12:00:00', 'is_saved': True,
            'field_line_ids': [(0, 0, {
                'field_name': 'phone', 'field_label': 'Phone', 'field_type': 'char', 'record_count': 20,
                'is_rated': True,
                'value_ids': [(0, 0, {'sequence': n, 'value_label': 'val%02d' % n, 'count': 1}) for n in range(12)],
            })],
        })

    def test_outsider_cannot_export(self):
        """Requirements: FR-DPA-0026, FR-DPA-0027 (only the Data Profiling group exports)."""
        for export in ('_export_xlsx', '_export_pdf'):
            with self.assertRaises(AccessError):
                getattr(self.snapshot.with_user(self.outsider), export)()

    def test_pdf_lists_ten_most_frequent_values(self):
        """Requirements: FR-DPA-0027 (the 10 most frequent values of a field)."""
        _name, content = self.snapshot.with_user(self.member)._export_pdf()
        text = content.decode()
        self.assertIn('val09', text)
        self.assertNotIn('val10', text)

    def test_pdf_says_how_many_values_it_leaves_out(self):
        """Requirements: FR-DPA-0027 (12 values: 10 listed, 2 more; exactly 10: nothing more)."""
        line = self.snapshot.field_line_ids
        self.assertEqual(self.snapshot._pdf_values_more(line), 2)
        _name, content = self.snapshot.with_user(self.member)._export_pdf()
        self.assertIn('and 2 more', content.decode())
        ten = self.env['dpa.snapshot'].create({
            'name': 'T', 'profile_name': 'T', 'model_name': 'res.partner', 'run_date': '2026-12-01 12:00:00',
            'field_line_ids': [(0, 0, {
                'field_name': 'phone', 'field_label': 'Phone', 'field_type': 'char',
                'value_ids': [(0, 0, {'sequence': n, 'value_label': 'v%02d' % n, 'count': 1}) for n in range(10)],
            })],
        })
        self.assertEqual(ten._pdf_values_more(ten.field_line_ids), 0)

    def test_exports_show_the_type_as_the_screen_does(self):
        """Requirements: FR-DPA-0026 (the type reads Text, not char)."""
        self.assertEqual(self.snapshot.type_label(self.snapshot.field_line_ids), 'Text')

    def test_xlsx_text_is_never_a_formula(self):
        """Requirements: FR-DPA-0026 (profiled text starting with '=' stays text)."""
        snapshot = self.env['dpa.snapshot'].create({
            'name': 'F', 'profile_name': 'F', 'model_name': 'res.partner', 'run_date': '2026-12-01 12:00:00',
            'field_line_ids': [(0, 0, {
                'field_name': 'name', 'field_label': 'Name', 'field_type': 'char',
                'value_ids': [(0, 0, {'sequence': 1, 'value_label': '=1+1', 'count': 1})],
            })],
        })
        _name, content = snapshot._export_xlsx()
        archive = zipfile.ZipFile(io.BytesIO(content))
        self.assertNotIn('<f>', archive.read('xl/worksheets/sheet3.xml').decode())
        self.assertIn('=1+1', archive.read('xl/sharedStrings.xml').decode())

    def test_xlsx_refuses_a_sheet_past_the_row_limit(self):
        """Requirements: FR-DPA-0026 (no row is dropped without a word)."""
        rows = [[0]] * xlsx.ROW_LIMIT
        with patch.object(xlsx, '_sheets', return_value=[('Big', ['h'], rows)]), self.assertRaises(UserError):
            xlsx.build(self.snapshot)

    def test_download_buttons(self):
        """Requirements: FR-DPA-0026, FR-DPA-0027 (the buttons open the download and the report)."""
        snapshot = self.snapshot.with_user(self.member)
        self.assertEqual(snapshot.action_export_xlsx()['url'], '/data_profiling/snapshot/%d/xlsx' % snapshot.id)
        self.member.company_id.external_report_layout_id = False
        self.assertEqual(snapshot.action_export_pdf()['type'], 'ir.actions.report')


@tagged('post_install', '-at_install')
class TestExportRoute(HttpCase):
    """Requirements: FR-DPA-0026 (the XLSX download route)."""

    def test_route_serves_members_and_refuses_outsiders(self):
        group = self.env.ref('data_profiling_analysis_app.group_data_profiling')
        internal = self.env.ref('base.group_user')
        Users = self.env['res.users'].with_context(no_reset_password=True)
        for login, groups in (('dpa_route_in', internal | group), ('dpa_route_out', internal)):
            Users.create({'name': login, 'login': login, 'password': login, 'group_ids': [(6, 0, groups.ids)]})
        snapshot = self.env['dpa.snapshot'].create({
            'name': 'R', 'profile_name': 'R', 'model_name': 'res.partner', 'run_date': '2026-12-01 12:00:00'})
        self.env.cr.flush()
        url = '/data_profiling/snapshot/%d/xlsx' % snapshot.id
        self.authenticate('dpa_route_in', 'dpa_route_in')
        member = self.url_open(url)
        self.assertEqual(member.status_code, 200)
        self.assertIn('R%202026-12-01.xlsx', member.headers['Content-Disposition'])
        self.assertEqual(self.url_open('/data_profiling/snapshot/0/xlsx').status_code, 404)
        self.authenticate('dpa_route_out', 'dpa_route_out')
        self.assertEqual(self.url_open(url).status_code, 404)
