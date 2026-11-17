import io
from datetime import date, datetime

import openpyxl

from odoo.exceptions import AccessError
from odoo.tests import Form, new_test_user
from odoo.tests.common import TransactionCase

MODULE = 'data_governance_quality_center_app'


class TestAuditExportAcceptance(TransactionCase):
    """Acceptance tests of T-043 (export the audit trail).

    Requirements: FR-DGQ-0074 (SC-DGQ-0074-1, SC-DGQ-0074-2).
    The export dialog is driven with Form (filters set, dialog saved, then
    action_export is called); the Export button and the file download itself
    are left to the screen review, so the XLSX bytes are built from the
    entries the dialog selects.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.manager = new_test_user(
            cls.env, login='dgq_t043_manager',
            groups='base.group_user,%s.group_dgq_manager' % MODULE)
        cls.admin = new_test_user(
            cls.env, login='dgq_t043_admin',
            groups='base.group_user,%s.group_dgq_admin' % MODULE)

    def _entry(self, when, event, name):
        return self.env['data.quality.audit'].sudo().create({
            'date': when,
            'event': event,
            'user_id': self.admin.id,
            'company_id': self.env.company.id,
            'res_model': 'res.partner',
            'res_id': 7,
            'record_name': name,
            'field_names': 'name',
            'changes': {'name': ['Old', 'New']},
        })

    def _export(self, user, **filters):
        with Form(self.env['data.quality.audit.export'].with_user(user)) as form:
            for name, value in filters.items():
                setattr(form, name, value)
        return form.save()

    def test_admin_exports_the_merges_of_q3_to_xlsx(self):
        """SC-DGQ-0074-1: 3 merges and 10 other entries in Q3 2026; the file holds the 3 merges."""
        for index, day in enumerate((1, 15, 30)):
            self._entry(datetime(2026, 7, day, 9, 0), 'merge_done', 'Merged %s' % index)
        for index in range(10):
            event = 'rule_changed' if index % 2 else 'scan_started'
            self._entry(datetime(2026, 8, index + 1, 9, 0), event, 'Other %s' % index)
        wizard = self._export(
            self.admin, date_from=date(2026, 7, 1), date_to=date(2026, 9, 30),
            event='merge_done', file_format='xlsx')
        self.env.flush_all()
        action = wizard.action_export()
        self.assertEqual(action.get('type'), 'ir.actions.act_url', 'XLSX is a file to download')
        entries = wizard.sudo()._get_entries()
        self.assertEqual(len(entries), 3)
        from odoo.addons.data_governance_quality_center_app.services import export
        sheet = openpyxl.load_workbook(io.BytesIO(export.audit_xlsx(entries))).active
        rows = list(sheet.values)
        header = [str(cell).lower() for cell in rows[0]]
        for column in ('event', 'user', 'time', 'company', 'record', 'values'):
            self.assertTrue(
                any(column in title for title in header), 'no %s column in %s' % (column, header))
        self.assertEqual(len(rows), 4, 'a header and the 3 merge entries')
        text = ' '.join(str(cell) for row in rows[1:] for cell in row)
        for index in range(3):
            self.assertIn('Merged %s' % index, text)
        self.assertNotIn('Other', text)
        self.assertIn('New', text, 'the values column')
        self.assertIn(self.admin.name, text)
        self.assertIn(self.env.company.name, text)

    def test_period_excludes_entries_outside_q3(self):
        """SC-DGQ-0074-1: merges just before and after the quarter are not exported."""
        self._entry(datetime(2026, 6, 29, 12, 0), 'merge_done', 'Before')
        self._entry(datetime(2026, 7, 2, 12, 0), 'merge_done', 'Inside')
        self._entry(datetime(2026, 10, 2, 12, 0), 'merge_done', 'After')
        wizard = self._export(
            self.admin, date_from=date(2026, 7, 1), date_to=date(2026, 9, 30), event='merge_done')
        self.assertEqual(wizard.sudo()._get_entries().mapped('record_name'), ['Inside'])

    def test_admin_exports_a_pdf_report(self):
        """SC-DGQ-0074-1: the PDF format returns a report action for the matching entries."""
        self._entry(datetime(2026, 7, 5, 9, 0), 'merge_done', 'Merged pdf')
        wizard = self._export(
            self.admin, date_from=date(2026, 7, 1), date_to=date(2026, 9, 30),
            event='merge_done', file_format='pdf')
        action = wizard.action_export()
        self.assertEqual(action.get('type'), 'ir.actions.report')

    def test_manager_is_not_offered_the_audit_export(self):
        """SC-DGQ-0074-2: a manager cannot open the export dialog and sees no Audit menu."""
        with self.assertRaises(AccessError):
            self.env['data.quality.audit.export'].with_user(self.manager).create({})
        menu = self.env.ref('%s.menu_dgq_audit' % MODULE)
        menus = self.env['ir.ui.menu']
        self.assertNotIn(menu.id, menus.with_user(self.manager)._visible_menu_ids())
        self.assertIn(menu.id, menus.with_user(self.admin)._visible_menu_ids())
