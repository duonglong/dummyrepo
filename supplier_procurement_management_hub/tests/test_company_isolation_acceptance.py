"""Acceptance tests of T-002 (company isolation), written from the specification.

Requirements: BR-SPM-0001, FR-SPM-0107

SC-SPM-0107-1 names contracts, a model of a later milestone. The same rule is
checked here on the audit trail, whose entries of company-owned records are
visible only in their company while shared supplier contacts are visible to
every company.
"""
from odoo.tests.common import TransactionCase

MODULE = 'supplier_procurement_management_hub'


class TestCompanyIsolationAcceptance(TransactionCase):
    """Requirements: BR-SPM-0001, FR-SPM-0107"""

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        self.company_a = self.env.company
        self.company_b = self.env['res.company'].create({'name': 'Company B T002'})
        self.buyer_b = self._user('buyer')
        self.auditor_b = self._user('auditor')
        self.acme = self.env['res.partner'].create({'name': 'Acme', 'is_company': True})
        selection = dict(self.env['procurement.audit.log']._fields['event'].selection)
        self.event = [k for k in selection if 'approval' in k][0]
        Log = self.env['procurement.audit.log'].sudo()
        for company, comment in ((self.company_a, 'of A'), (self.company_b, 'of B'),
                                 (self.env['res.company'], 'shared')):
            Log.create({
                'date': '2026-10-01 10:00:00', 'event': self.event,
                'res_model': 'res.partner', 'res_id': self.acme.id,
                'company_id': company.id, 'comment': comment,
            })

    def _user(self, role):
        return self.env['res.users'].create({
            'name': f'User {role} B',
            'login': f'spm_{role}_b_t002',
            'email': f'spm_{role}_b_t002@example.com',
            'company_id': self.company_b.id,
            'company_ids': [(6, 0, [self.company_b.id])],
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_{role}').id])],
        })

    def test_sc_0107_1_company_a_records_are_not_returned_in_company_b(self):
        """SC-SPM-0107-1 (on audit entries): a user allowed only in company B
        does not get company A's entry, but gets company B's entry and the
        shared one."""
        entries = self.env['procurement.audit.log'].with_user(self.auditor_b).search(
            [('res_id', '=', self.acme.id), ('res_model', '=', 'res.partner')])
        self.assertEqual(set(entries.mapped('comment')), {'of B', 'shared'})

    def test_sc_0107_1_the_shared_supplier_is_returned(self):
        """SC-SPM-0107-1: Acme itself, a shared contact, is returned to a
        Buyer allowed only in company B."""
        found = self.env['res.partner'].with_user(self.buyer_b).search([('id', '=', self.acme.id)])
        self.assertEqual(found, self.acme)
