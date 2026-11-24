"""Regression test for B-0249: the sourcing and the contract/portal-profile work load side by side.

Requirements: FR-SPM-0016, FR-SPM-0018, FR-SPM-0063
"""
from odoo.tests import TransactionCase, tagged

from ..services import portal_access

SOURCING_MODELS = (
    'procurement.sourcing.event', 'procurement.sourcing.event.line', 'procurement.sourcing.criterion',
    'procurement.sourcing.response', 'procurement.sourcing.template')
CONTRACT_MODELS = ('procurement.contract', 'procurement.contract.line', 'procurement.contract.version')
PORTAL_FUNCTIONS = ('quote_rows', 'update_profile', 'upload_document')


@tagged('post_install', '-at_install')
class TestMergeSourcingContract(TransactionCase):

    def test_bug_b_0249_both_feature_sets_are_loaded(self):
        """Requirements: FR-SPM-0016, FR-SPM-0018, FR-SPM-0063: models and portal functions of both sides exist."""
        for model in SOURCING_MODELS + CONTRACT_MODELS:
            self.assertIn(model, self.env, model)
        for name in PORTAL_FUNCTIONS:
            self.assertTrue(callable(getattr(portal_access, name, None)), name)

    def test_bug_b_0249_both_sides_keep_their_access_rows(self):
        """Requirements: FR-SPM-0016, FR-SPM-0063: each model of both sides has access rows."""
        for model in SOURCING_MODELS + CONTRACT_MODELS:
            access = self.env['ir.access'].sudo().search_count([('model_id.model', '=', model)])
            self.assertTrue(access, model)
