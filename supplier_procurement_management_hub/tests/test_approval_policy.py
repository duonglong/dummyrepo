"""Unit tests of the approval policy models.

Requirements: FR-SPM-0043, FR-SPM-0044
"""
from odoo.exceptions import ValidationError

from .approval_common import SpmApprovalCase


class TestApprovalPolicy(SpmApprovalCase):
    """Requirements: FR-SPM-0043, FR-SPM-0044"""

    def test_upper_amount_must_exceed_lower(self):
        """FR-SPM-0043: an upper limit not above the lower one is refused."""
        with self.assertRaises(ValidationError):
            self._policy('Bad', amount_min=500.0, amount_max=100.0)

    def test_manager_step_needs_a_level(self):
        """FR-SPM-0044: a manager step of 0 levels is refused."""
        with self.assertRaises(ValidationError):
            self._policy('Zero', steps=[self._step('M', 10, approver_type='manager', manager_levels=0)])

    def test_resolvers_are_extendable(self):
        """FR-SPM-0044: every approver type has a resolver method."""
        step_model = self.env['procurement.approval.policy.step']
        for method in step_model._spm_approver_resolvers().values():
            self.assertTrue(hasattr(step_model, method))
