"""Acceptance tests of T-001 (eight role groups), written from the specification.

Requirements: FR-SPM-0106

SC-SPM-0106-1 and SC-SPM-0106-2 name approval policies, contracts and sourcing
responses, models of later milestones. These tests check the same rights on
the models that exist after T-001..T-008 (statuses and types, applications,
assessments): the auditor reads and never changes, the requester has no access
to the supplier files. The policy, contract and response halves are proved by
the tasks that build those models.
"""
from odoo.exceptions import AccessError
from odoo.tests.common import TransactionCase

MODULE = 'supplier_procurement_management_hub'
GROUPS = [
    'group_spm_requester', 'group_spm_department_manager', 'group_spm_buyer',
    'group_spm_manager', 'group_spm_finance', 'group_spm_auditor',
    'group_spm_admin',
]


class TestSecurityGroupsAcceptance(TransactionCase):
    """Requirements: FR-SPM-0106"""

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        self.auditor = self._user('auditor')
        self.requester = self._user('requester')

    def _user(self, role):
        return self.env['res.users'].create({
            'name': f'User {role}',
            'login': f'spm_{role}_t001',
            'email': f'spm_{role}_t001@example.com',
            'group_ids': [(6, 0, [self.env.ref(f'{MODULE}.group_spm_{role}').id])],
        })

    def test_the_role_groups_exist(self):
        """FR-SPM-0106: the seven internal role groups exist (the eighth role,
        the supplier portal user, is Odoo's portal group)."""
        for name in GROUPS:
            self.assertTrue(self.env.ref(f'{MODULE}.{name}', raise_if_not_found=False), name)

    def test_sc_0106_1_auditor_reads_and_cannot_change(self):
        """SC-SPM-0106-1 (on configuration records of M1): an Auditor reads
        the supplier statuses and types, and a write or create is refused with
        an access error."""
        status = self.env['procurement.supplier.status'].search([], limit=1)
        as_auditor = status.with_user(self.auditor)
        self.assertTrue(as_auditor.name)
        with self.assertRaises(AccessError):
            as_auditor.write({'name': 'Changed by the auditor'})
        with self.assertRaises(AccessError):
            self.env['procurement.supplier.type'].with_user(self.auditor).create({'name': 'Auditor type'})
        self.env['procurement.supplier.type'].with_user(self.auditor).search([])

    def test_sc_0106_2_requester_gets_no_supplier_application_back(self):
        """SC-SPM-0106-2 (on onboarding applications of M1): a Requester who
        searches applications gets no record back (or an access error)."""
        supplier_type = self.env['procurement.supplier.type'].create({'name': 'Goods T001'})
        self.env['procurement.supplier.application'].create({
            'email': 'sales@acme.example', 'supplier_type_id': supplier_type.id,
            'legal_name': 'Acme',
        })
        try:
            found = self.env['procurement.supplier.application'].with_user(self.requester).search([])
        except AccessError:
            return
        self.assertFalse(found)
