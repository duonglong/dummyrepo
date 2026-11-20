"""Regression test of B-0254: the approval policy work and the epic's order work load together.

Requirements: FR-SPM-0024, FR-SPM-0043, FR-SPM-0055
"""
from odoo.tests import TransactionCase


class TestMergeApprovalEpic(TransactionCase):

    def test_bug_b_0254_approval_and_epic_order_fields_coexist(self):
        """The order keeps the requester of T-018 and the acknowledgement of the epic; access rows are unique.

        Cannot reach: the text conflict itself, only its outcome in the loaded registry.
        """
        order_fields = self.env['purchase.order']._fields
        for name in ('spm_requester_id', 'spm_acknowledged_date', 'spm_acknowledged_user_id'):
            self.assertIn(name, order_fields, name)
        for model in ('procurement.approval.policy', 'procurement.approval.policy.step',
                      'procurement.delivery.proposal', 'procurement.comparison'):
            self.assertIn(model, self.env.registry, model)
        names = self.env['ir.access'].search([('name', 'like', 'access_spm_')]).mapped('name')
        self.assertEqual(len(names), len(set(names)))
