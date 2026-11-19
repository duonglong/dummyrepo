"""Unit tests of the approval policy models.

Requirements: FR-SPM-0043, FR-SPM-0044, FR-SPM-0046
"""
from freezegun import freeze_time

from odoo import SUPERUSER_ID
from odoo.exceptions import ValidationError

from ..services import approval
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

    def test_cumulative_period_is_the_current_one_whatever_the_deadline(self):
        """FR-SPM-0046: a draft whose deadline is next month is still counted against this month."""
        with freeze_time('2026-11-17 09:00:00'):
            policy = self._policy('Acme month', steps=[self._step('B', 10, users=self.bob)],
                                  amount_min=10000.0, partner_ids=self.acme, cumulative_period='month')
            confirmed = self._order(price=8000.0, qty=1.0, date_order='2026-11-02 09:00:00')
            confirmed.button_confirm()
            draft = self._order(price=3000.0, qty=1.0, date_order='2026-12-01 09:00:00')
            result = approval.evaluate(self.env, draft)
        self.assertIn(policy, result['policies'])
        self.assertAlmostEqual(result['cumulated_amount'], 11000.0, places=2)

    def test_quarter_period_counts_the_whole_quarter(self):
        """FR-SPM-0046: a quarterly policy counts an order of the first month of the quarter."""
        with freeze_time('2026-11-17 09:00:00'):
            policy = self._policy('Acme quarter', steps=[self._step('B', 10, users=self.bob)],
                                  amount_min=10000.0, partner_ids=self.acme, cumulative_period='quarter')
            self._order(price=8000.0, qty=1.0, date_order='2026-10-02 09:00:00').button_confirm()
            result = approval.evaluate(self.env, self._order(price=3000.0, qty=1.0))
        self.assertIn(policy, result['policies'])

    def test_the_requester_is_the_creating_person_and_empty_for_the_superuser(self):
        """FR-SPM-0044: no default stamps a system user; a person's order records them."""
        by_buyer = self._order()
        self.assertEqual(by_buyer.spm_requester_id, self.buyer)
        values = {'partner_id': self.acme.id}
        by_system = self.env['purchase.order'].with_user(SUPERUSER_ID).create(values)
        self.assertFalse(by_system.spm_requester_id)
