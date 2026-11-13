import io
from datetime import date

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
