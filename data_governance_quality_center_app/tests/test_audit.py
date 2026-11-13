import io
from datetime import date, datetime

import openpyxl

from odoo.exceptions import AccessError
from odoo.tests import Form

from odoo.addons.data_governance_quality_center_app.services import audit as audit_service
from odoo.addons.data_governance_quality_center_app.services import export

from .common import DGQTestCase


class TestAudit(DGQTestCase):
    """Own tests of T-004: the log helper, the purge guard and the export.

    Requirements: FR-DGQ-0072, FR-DGQ-0073, FR-DGQ-0074.
    The export dialog is driven with Form (filters set, dialog saved); the Export button is
    left to the screen review.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Acme'})
        cls.manager = cls.env['res.users'].create({
            'name': 'Mana', 'login': 'dgq_mana',
            'group_ids': [(6, 0, [cls.env.ref('data_governance_quality_center_app.group_dgq_manager').id])],
        })

    def _export(self, **filters):
        with Form(self.env['data.quality.audit.export']) as form:
            for name, value in filters.items():
                setattr(form, name, value)
        return form.save()

    def test_log_writes_entry_with_changes(self):
        """FR-DGQ-0072: the helper records event, user, record and before/after values."""
        entry = audit_service.log(
            self.env, 'rule_changed', self.partner, changes={'name': ['Acme', 'Acme 2']})
        self.assertEqual(entry.user_id, self.env.user)
        self.assertEqual(entry.res_model, 'res.partner')
        self.assertEqual(entry.res_id, self.partner.id)
        self.assertEqual(entry.record_name, 'Acme')
        self.assertEqual(entry.field_names, 'name')
        self.assertEqual(entry.changes, {'name': ['Acme', 'Acme 2']})

    def test_manager_cannot_read_or_purge(self):
        """FR-DGQ-0073/0074: only administrators see the trail, purge and export."""
        audit_service.log(self.env, 'merge_done', self.partner)
        with self.assertRaises(AccessError):
            self.env['data.quality.audit'].with_user(self.manager).search([])
        with self.assertRaises(AccessError):
            audit_service.purge(self.env(user=self.manager), date(2030, 1, 1))

    def test_company_isolation(self):
        """FR-DGQ-0072: an entry of another company is not seen by a user of this one."""
        other = self.env['res.company'].create({'name': 'Other'})
        admin = self.env.ref('base.user_admin')
        entry = audit_service.log(self.env['data.quality.audit'].with_company(other).env, 'merge_done')
        self.assertEqual(entry.company_id, other)
        seen = self.env['data.quality.audit'].with_user(admin).search([('id', '=', entry.id)])
        self.assertFalse(seen)

    def test_export_filters_and_xlsx(self):
        """SC-DGQ-0074-1: the file holds only the matching entries, with all columns."""
        for _i in range(3):
            audit_service.log(self.env, 'merge_done', self.partner)
        audit_service.log(self.env, 'scan_started', self.partner)
        wizard = self._export(event='merge_done')
        entries = wizard._get_entries()
        self.assertEqual(len(entries), 3)
        sheet = openpyxl.load_workbook(io.BytesIO(export.audit_xlsx(entries))).active
        rows = list(sheet.values)
        self.assertEqual(len(rows), 4)
        self.assertEqual(list(rows[0]), export.COLUMNS)
        self.assertEqual(rows[1][1], 'Merge done')
        self.assertEqual(rows[1][6], 'Acme')

    def test_export_action_by_format(self):
        """FR-DGQ-0074: XLSX opens the download route, PDF the report."""
        self.assertIn('/data_quality/xlsx/audit/', self._export().action_export()['url'])
        action = self._export(file_format='pdf').action_export()
        # a layout dialog may come first on a database whose company has no layout yet
        report = action.get('report_name') and action or action['context']['report_action']
        self.assertEqual(report['report_name'], 'data_governance_quality_center_app.report_audit')

    def _entry_at(self, when, event='merge_done', **vals):
        entry = audit_service.log(self.env, event, self.partner)
        self.env.cr.execute("UPDATE data_quality_audit SET date = %s WHERE id = %s", (when, entry.id))
        entry.invalidate_recordset(['date'])
        return entry

    def test_purge_keeps_other_companies_and_count_matches(self):
        """FR-DGQ-0073: a purge removes only the entries of the user's companies, as counted."""
        other = self.env['res.company'].create({'name': 'Other'})
        mine = self._entry_at(datetime(2023, 5, 1))
        theirs = audit_service.log(self.env['data.quality.audit'].with_company(other).env, 'merge_done')
        self.env.cr.execute("UPDATE data_quality_audit SET date = %s WHERE id = %s",
                            (datetime(2023, 5, 1), theirs.id))
        self.env.invalidate_all()
        admin_env = self.env(user=self.env.ref('base.user_admin'))
        with Form(admin_env['data.quality.audit.purge']) as form:
            form.date_before = date(2024, 1, 1)
        counted = form.save().entry_count
        self.assertEqual(counted, 1)
        audit_service.purge(admin_env, date(2024, 1, 1))
        self.env.cr.execute("SELECT id FROM data_quality_audit WHERE id IN %s", ((mine.id, theirs.id),))
        self.assertEqual(self.env.cr.fetchall(), [(theirs.id,)])
        purge_entry = self.env['data.quality.audit'].sudo().search([('event', '=', 'purge')], limit=1)
        self.assertEqual(purge_entry.changes['deleted'][1], counted)

    def test_manager_cannot_reach_export(self):
        """SC-DGQ-0074-2: no export dialog and no Audit menu for a manager.

        The buttons themselves are left to the screen review.
        """
        with self.assertRaises(AccessError):
            self.env['data.quality.audit.export'].with_user(self.manager).create({})
        menu = self.env.ref('data_governance_quality_center_app.menu_dgq_audit')
        self.assertNotIn(menu.id, self.env['ir.ui.menu'].with_user(self.manager)._visible_menu_ids())
        self.assertIn(menu.id, self.env['ir.ui.menu']._visible_menu_ids())

    def test_export_q3_period_and_filters(self):
        """SC-DGQ-0074-1: period (inclusive end day), user, model and values column."""
        self._entry_at(datetime(2026, 7, 1, 0, 0))
        self._entry_at(datetime(2026, 9, 30, 23, 59), changes=None)
        self._entry_at(datetime(2026, 8, 15))
        self._entry_at(datetime(2026, 6, 30, 23, 59))
        self._entry_at(datetime(2026, 10, 1, 0, 0))
        self._entry_at(datetime(2026, 8, 1), event='scan_started')
        wizard = self._export(event='merge_done', date_from=date(2026, 7, 1), date_to=date(2026, 9, 30))
        self.assertEqual(len(wizard._get_entries()), 3)
        self.assertFalse(self._export(user_ids=self.manager)._get_entries())
        model = self.env['ir.model']._get('res.users')
        self.assertFalse(self._export(model_ids=model)._get_entries())
        company = self.env['res.company'].create({'name': 'Other'})
        self.assertFalse(self._export(company_ids=company)._get_entries())
        entry = audit_service.log(self.env, 'rule_changed', self.partner, changes={'name': ['A', 'B']})
        rows = list(openpyxl.load_workbook(io.BytesIO(export.audit_xlsx(entry))).active.values)
        self.assertIn('name', rows[1][8])

    def test_xlsx_text_is_never_a_formula(self):
        """FR-DGQ-0074: a record named '=1+1' is exported as text, not run as a formula."""
        evil = self.env['res.partner'].create({'name': '=1+1'})
        entry = audit_service.log(self.env, 'merge_done', evil)
        sheet = openpyxl.load_workbook(io.BytesIO(export.audit_xlsx(entry))).active
        cell = sheet.cell(row=2, column=7)
        self.assertEqual((cell.value, cell.data_type), ('=1+1', 's'))
