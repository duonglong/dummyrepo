"""Unit tests of T-021 beyond the acceptance tests.

Requirements: FR-SPM-0063, FR-SPM-0064, FR-SPM-0065
"""
from freezegun import freeze_time

from odoo.exceptions import AccessError, UserError

from .contract_common import MODULE, SpmContractCase


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

    def test_version_cannot_be_changed_or_deleted(self):
        """Requirements: FR-SPM-0065: an earlier version is neither changed nor deleted."""
        contract = self._active_contract()
        contract.with_user(self.ann).action_spm_revise()
        version = contract.version_ids
        with self.assertRaises(UserError):
            version.unlink()
        with self.assertRaises(UserError):
            version.write({'date_end': '2030-01-01'})

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

    def test_revision_keeps_the_original_author_and_binds_the_reviser(self):
        """Requirements: FR-SPM-0064, FR-SPM-0065: the reviser keeps the author; neither approves the revision."""
        contract = self._active_contract()
        contract.with_user(self.bob).action_spm_revise()
        self.assertEqual(contract.author_id, self.ann)
        self.assertEqual(contract.revised_by_id, self.bob)
        contract.with_user(self.bob).action_spm_submit()
        with self.assertRaises(UserError):
            contract.with_user(self.bob).action_spm_approve()
        with freeze_time('2027-06-01 09:00:00'):
            contract.with_user(self.root).action_spm_approve()
        self.assertEqual(contract.version_ids.author_id, self.ann)

    def test_workflow_fields_are_not_written_directly(self):
        """Requirements: FR-SPM-0064: state, authors, approver, version, number only through actions."""
        contract = self._contract()
        for vals in ({'state': 'active'}, {'author_id': self.bob.id}, {'approved_by_id': self.ann.id},
                     {'version': 5}, {'name': 'X'}, {'revised_by_id': self.bob.id}):
            with self.assertRaises(UserError, msg=str(vals)):
                contract.with_user(self.ann).write(vals)

    def test_supplier_and_company_frozen_after_draft(self):
        """Requirements: FR-SPM-0062: the supplier is a term, the company only changes in a draft."""
        contract = self._active_contract()
        with self.assertRaises(UserError):
            contract.with_user(self.ann).write({'partner_id': self.beta.id})
        with self.assertRaises(UserError):
            contract.with_user(self.ann).write({'company_id': self.company.id})

    def test_only_a_draft_contract_is_deleted(self):
        """Requirements: FR-SPM-0065: deleting an active contract would wipe its versions."""
        active = self._active_contract()
        with self.assertRaises(UserError):
            active.with_user(self.ann).unlink()
        draft = self._contract()
        draft.with_user(self.ann).unlink()
        self.assertFalse(draft.exists())

    def test_contract_without_lines_can_be_approved(self):
        """Requirements: FR-SPM-0064: a category plus ceiling contract has no line."""
        contract = self._contract(lines=[], scope_kind='categories',
                                  product_category_ids=[(6, 0, self.furniture.ids)], ceiling_value=5000.0)
        contract.with_user(self.ann).action_spm_submit()
        with freeze_time('2026-12-15 09:00:00'):
            contract.with_user(self.bob).action_spm_approve()
        self.assertEqual(contract.state, 'active')

    def _ordered_line(self, contract):
        order = self.env['purchase.order'].create({
            'partner_id': self.acme.id,
            'order_line': [(0, 0, {'product_id': self.chair.id, 'product_qty': 1.0,
                                   'spm_contract_line_id': contract.line_ids.id})],
        })
        return order.order_line

    def test_ordered_line_cannot_be_removed_or_retargeted_in_a_revision(self):
        """Requirements: FR-SPM-0065: orders keep their link and consumption."""
        contract = self._active_contract()
        self._ordered_line(contract)
        contract.with_user(self.ann).action_spm_revise()
        other = self.env['product.product'].create({'name': 'Desk M4', 'type': 'consu'})
        with self.assertRaises(UserError):
            contract.line_ids.with_user(self.ann).write({'product_id': other.id})
        with self.assertRaises(UserError):
            contract.line_ids.with_user(self.ann).unlink()
        contract.line_ids.with_user(self.ann).write({'price_unit': 10.0})
        contract.with_user(self.ann).action_spm_submit()
        contract.invalidate_recordset()

    def test_diff_pairs_lines_by_id_when_one_is_removed(self):
        """Requirements: FR-SPM-0065: removing the first line is not a price change of the second."""
        other = self.env['product.product'].create({'name': 'Desk M4', 'type': 'consu'})
        contract = self._active_contract(
            lines=[self._line_vals(price=11.5), self._line_vals(price=90.0, product=other)])
        contract.with_user(self.ann).action_spm_revise()
        contract.line_ids.filtered(lambda line: line.product_id == self.chair).with_user(self.ann).unlink()
        diff = contract.version_ids.diff_html
        self.assertNotIn('90', diff)
        self.assertIn('Office chair M4', diff)

    def test_payment_term_and_product_of_another_company_are_refused(self):
        """Requirements: FR-SPM-0062, FR-SPM-0063: terms and products belong to the contract's company."""
        company_b = self.env['res.company'].create({'name': 'Company B M4'})
        term = self.env['account.payment.term'].create({'name': 'B 30 days', 'company_id': company_b.id})
        with self.assertRaises(UserError):
            self._contract(user=self.env.user, payment_term_id=term.id)
        product = self.env['product.product'].create({
            'name': 'B only M4', 'type': 'consu', 'company_id': company_b.id})
        with self.assertRaises(UserError):
            self._contract(user=self.env.user, lines=[self._line_vals(product=product)])

    def test_daily_step_runs_through_the_job_runner(self):
        """Requirements: FR-SPM-0064, FR-SPM-0096: the scheduled action runs the job, which logs a run."""
        contract = self._contract(start='2027-01-01')
        contract.with_user(self.ann).action_spm_submit()
        with freeze_time('2026-12-15 09:00:00'):
            contract.with_user(self.bob).action_spm_approve()
        with freeze_time('2027-01-02 06:00:00'):
            run = self.env['procurement.job']._spm_run('contract_states')
        self.assertEqual(run.state, 'done')
        self.assertEqual(run.processed_count, 1)
        self.assertEqual(contract.state, 'active')
        cron = self.env.ref(f'{MODULE}.ir_cron_spm_contract_states')
        self.assertEqual(cron.code, "model._spm_run('contract_states')")

    def test_approval_with_a_future_start_is_audited(self):
        """Requirements: FR-SPM-0064: every approval writes an audit entry."""
        contract = self._contract(start='2027-01-01')
        contract.with_user(self.ann).action_spm_submit()
        with freeze_time('2026-12-15 09:00:00'):
            contract.with_user(self.bob).action_spm_approve()
        entry = self.env['procurement.audit.log'].search(
            [('res_model', '=', contract._name), ('res_id', '=', contract.id), ('event', '=', 'contract')])
        self.assertEqual(entry.comment, 'approved')
