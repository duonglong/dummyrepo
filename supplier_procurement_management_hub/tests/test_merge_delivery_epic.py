"""Regression test of B-0253: the delivery work and the epic's sourcing work load together.

Requirements: FR-SPM-0025, FR-SPM-0058, FR-SPM-0060
"""
from odoo.tests import TransactionCase


class TestMergeDeliveryEpic(TransactionCase):

    def test_bug_b_0253_delivery_and_epic_models_coexist(self):
        """Models of T-024 and of the epic are all registered and every access row is unique.

        Cannot reach: the text conflict itself, only its outcome in the loaded registry.
        """
        for model in ('procurement.delivery.proposal', 'procurement.delivery.discrepancy',
                      'procurement.comparison', 'procurement.sourcing.question',
                      'procurement.contract'):
            self.assertIn(model, self.env.registry, model)
        names = self.env['ir.access'].search([('name', 'like', 'access_spm_')]).mapped('name')
        self.assertEqual(len(names), len(set(names)))
