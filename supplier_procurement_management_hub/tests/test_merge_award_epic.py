"""Regression test of B-0255: the award work and the epic's order work load together.

Requirements: FR-SPM-0044, FR-SPM-0055
"""
from odoo.tests import TransactionCase


class TestMergeAwardEpic(TransactionCase):

    def test_bug_b_0255_award_and_epic_order_fields_coexist(self):
        """The order keeps the sourcing links of T-017 and the requester and acknowledgement of the epic.

        Cannot reach: the text conflict itself, only its outcome in the loaded registry.
        """
        order_fields = self.env['purchase.order']._fields
        for name in ('spm_sourcing_event_id', 'spm_request_ids', 'spm_requester_id',
                     'spm_acknowledged_date', 'spm_acknowledged_user_id'):
            self.assertIn(name, order_fields, name)
        for model in ('procurement.award.wizard', 'procurement.approval.policy',
                      'procurement.delivery.proposal'):
            self.assertIn(model, self.env.registry, model)
        ids = self.env['ir.access'].search([('model_id.model', 'like', 'procurement.')]).get_external_id().values()
        self.assertEqual(len(ids), len(set(ids)))
