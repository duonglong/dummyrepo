from odoo.exceptions import AccessError
from odoo.tests import HttpCase, tagged

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

    def test_download_buttons(self):
        """Requirements: FR-DPA-0026, FR-DPA-0027 (the buttons open the download and the report)."""
        snapshot = self.snapshot.with_user(self.member)
        self.assertEqual(snapshot.action_export_xlsx()['url'], '/data_profiling/snapshot/%d/xlsx' % snapshot.id)
        # Odoo first opens its layout setup when the company has none: still an action, never an error.
        self.assertIn(snapshot.action_export_pdf()['type'], ('ir.actions.report', 'ir.actions.act_window'))


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
        self.assertIn(self.url_open(url).status_code, (403, 404))
