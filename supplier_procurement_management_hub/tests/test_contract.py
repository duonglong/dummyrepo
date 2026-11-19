"""Unit tests of T-021 beyond the acceptance tests.

Requirements: FR-SPM-0063, FR-SPM-0064, FR-SPM-0065
"""
from odoo.exceptions import AccessError, UserError

from .contract_common import SpmContractCase


class TestContract(SpmContractCase):

    def test_lines_and_terms_locked_while_waiting_for_approval(self):
        """Requirements: FR-SPM-0064, FR-SPM-0065: terms and lines are fixed once sent to approval."""
        contract = self._contract()
        contract.with_user(self.ann).action_spm_submit()
        with self.assertRaises(UserError):
            contract.line_ids.write({'price_unit': 1.0})
        with self.assertRaises(UserError):
            contract.write({'notice_days': 5})
        with self.assertRaises(UserError):
            contract.line_ids.unlink()

    def test_version_cannot_be_deleted(self):
        """Requirements: FR-SPM-0065: an earlier version is neither changed nor deleted."""
        contract = self._active_contract()
        contract.with_user(self.ann).action_spm_revise()
        version = contract.version_ids
        with self.assertRaises(UserError):
            version.unlink()

    def test_non_manager_cannot_approve(self):
        """Requirements: FR-SPM-0064: only a Procurement manager approves."""
        contract = self._contract()
        contract.with_user(self.ann).action_spm_submit()
        buyer = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Buyer M4', 'login': 'spm_buyer_m4', 'email': 'b@example.com',
            'group_ids': [(6, 0, [self.env.ref('supplier_procurement_management_hub.group_spm_buyer').id])],
        })
        with self.assertRaises(AccessError):
            contract.with_user(buyer).action_spm_approve()
