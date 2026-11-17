"""Acceptance tests of T-002 (audit trail), written from the specification.

Requirements: BR-SPM-0018, FR-SPM-0109
"""
from datetime import date

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase

MODULE = 'supplier_procurement_management_hub'


class TestAuditTrailAcceptance(TransactionCase):
    """Requirements: FR-SPM-0109, BR-SPM-0018"""

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        self.auditor = self.env['res.users'].create({
            'name': 'Audrey Auditor',
            'login': 'spm_auditor_t002',
            'email': 'spm_auditor_t002@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_auditor').id])],
        })
        self.admin = self.env.ref('base.user_admin')

    def _approval_event(self):
        selection = dict(self.env['procurement.audit.log']._fields['event'].selection)
        keys = [k for k in selection if 'approval' in k]
        self.assertTrue(keys, "the audit event list has an approval kind")
        return keys[0]

    def _log_entries(self):
        from odoo.addons.supplier_procurement_management_hub.services import audit
        event = self._approval_event()
        company = self.env.company
        audit.log(self.env, event, company, {'state': 'pending'}, {'state': 'approved'}, 'first')
        audit.log(self.env, event, company, {'state': 'pending'}, {'state': 'rejected'}, 'second')
        return event

    def test_sc_0109_1_auditor_filters_and_exports_approval_entries(self):
        """SC-SPM-0109-1 (first part): an Auditor filters on kind approval and
        the month and exports; the rows hold those entries."""
        event = self._log_entries()
        month_start = date.today().replace(day=1)
        entries = self.env['procurement.audit.log'].with_user(self.auditor).search([
            ('event', '=', event), ('date', '>=', month_start),
        ])
        self.assertEqual(set(entries.mapped('comment')), {'first', 'second'})
        exported = entries.export_data(['comment', 'event'])['datas']
        self.assertEqual(sorted(row[0] for row in exported), ['first', 'second'])
        entry = entries.filtered(lambda e: e.comment == 'first')
        self.assertEqual(entry.res_model, 'res.company')
        self.assertEqual(entry.res_id, self.env.company.id)
        self.assertEqual(entry.old_values, {'state': 'pending'})
        self.assertEqual(entry.new_values, {'state': 'approved'})
        self.assertTrue(entry.date)

    def test_sc_0109_1_administrator_cannot_delete_or_edit_an_entry(self):
        """SC-SPM-0109-1 (second part): an Administrator trying to delete an
        entry is refused; editing is refused too."""
        self._log_entries()
        entries = self.env['procurement.audit.log'].with_user(self.admin).search(
            [('comment', 'in', ['first', 'second'])])
        self.assertEqual(len(entries), 2)
        with self.assertRaises((AccessError, UserError, ValidationError)):
            entries[0].unlink()
        with self.assertRaises((AccessError, UserError, ValidationError)):
            entries[0].write({'comment': 'changed'})
        self.env.flush_all()
        self.assertEqual(entries[0].comment, 'first')

    def test_auditor_cannot_delete_an_entry(self):
        """FR-SPM-0109: no user, an Auditor included, changes or deletes an entry."""
        self._log_entries()
        entry = self.env['procurement.audit.log'].with_user(self.auditor).search(
            [('comment', '=', 'first')], limit=1)
        with self.assertRaises((AccessError, UserError, ValidationError)):
            entry.unlink()
