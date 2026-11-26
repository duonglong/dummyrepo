from lxml import etree

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tools.safe_eval import safe_eval

from .common import IprCommon


@tagged('post_install', '-at_install')
class TestApprovalAcceptance(IprCommon):
    """Acceptance tests of T-006: approve and reject a bill's exceptions, Bills to Approve list.

    Requirements: BR-IPR-0003, FR-IPR-0011, FR-IPR-0012, FR-IPR-0013, FR-IPR-0014,
    FR-IPR-0015, FR-IPR-0023.
    The decision is taken by its methods (action_ipr_approve, action_ipr_reject); the
    reject dialog (ipr.reject.wizard) that collects the reason is left to the build.
    """

    def _set_creator(self, bill, user):
        self.env.flush_all()
        self.env.cr.execute("UPDATE account_move SET create_uid = %s WHERE id = %s", (user.id, bill.id))
        bill.invalidate_recordset(['create_uid'])

    def _waiting_bill(self, creator=None, ref='APPR-1'):
        """Bill entered by `creator` billing 12.00 against 10.00, tolerance 0: one price exception."""
        order = self.make_order(10, 10.0, received=10)
        bill = self.make_bill_from_order(order, 10, 12.0, ref=ref)
        self._set_creator(bill, creator or self.accountant)
        bill.with_user(self.accountant).action_ipr_check()
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(bill.ipr_state, 'waiting')
        return bill

    def _history(self, bill, event):
        return bill.sudo().ipr_history_ids.filtered(lambda h: h.event == event)

    # SC-IPR-0012-1 (the approval part; posting afterwards is T-007)
    def test_approve_with_comment(self):
        """FR-IPR-0012: a purchase manager approves with a comment: Approved, decider, time, history entry."""
        bill = self._waiting_bill()
        bill.with_user(self.purchase_manager).action_ipr_approve('Price rise agreed by phone')
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(bill.ipr_state, 'approved')
        self.assertEqual(bill.ipr_decided_by_id, self.purchase_manager)
        self.assertTrue(bill.ipr_decided_date)
        self.assertEqual(bill.ipr_decision_comment, 'Price rise agreed by phone')
        entry = self._history(bill, 'approve')
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry.user_id, self.purchase_manager)
        self.assertEqual(entry.comment, 'Price rise agreed by phone')
        self.assertEqual(entry.state_after, 'approved')

    def test_approve_without_comment(self):
        """FR-IPR-0012: the comment is optional."""
        bill = self._waiting_bill()
        bill.with_user(self.account_admin).action_ipr_approve()
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(bill.ipr_state, 'approved')
        self.assertEqual(bill.ipr_decided_by_id, self.account_admin)

    # SC-IPR-0013-1
    def test_reject_with_reason(self):
        """SC-IPR-0013-1: an accounting administrator rejects with a reason: Rejected, reason in the history."""
        bill = self._waiting_bill()
        bill.with_user(self.account_admin).action_ipr_reject('Ask Acme for a credit note')
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(bill.ipr_state, 'rejected')
        self.assertEqual(bill.ipr_decided_by_id, self.account_admin)
        self.assertTrue(bill.ipr_decided_date)
        entry = self._history(bill, 'reject')
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry.user_id, self.account_admin)
        self.assertEqual(entry.comment, 'Ask Acme for a credit note')
        self.assertEqual(entry.state_after, 'rejected')

    # SC-IPR-0013-2
    def test_reject_without_reason(self):
        """SC-IPR-0013-2: a rejection without a reason is refused, the status stays Waiting."""
        for reason in ('', False):
            bill = self._waiting_bill(ref=f'NOREASON-{reason!r}')
            with self.assertRaises(UserError) as caught:
                bill.with_user(self.account_admin).action_ipr_reject(reason)
            self.assertIn('reason', str(caught.exception).lower())
            self.env.flush_all()
            self.env.invalidate_all()
            self.assertEqual(bill.ipr_state, 'waiting')
            self.assertFalse(self._history(bill, 'reject'))

    # SC-IPR-0014-1
    def test_purchase_user_refused(self):
        """SC-IPR-0014-1: a user with Purchase user rights only cannot approve or reject."""
        bill = self._waiting_bill()
        with self.assertRaises(UserError):
            bill.with_user(self.purchase_user).action_ipr_approve()
        with self.assertRaises(UserError):
            bill.with_user(self.purchase_user).action_ipr_reject('No')
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(bill.ipr_state, 'waiting')
        self.assertFalse(bill.ipr_decided_by_id)

    def test_accountant_refused(self):
        """FR-IPR-0014: an accountant without manager rights cannot decide either."""
        bill = self._waiting_bill()
        with self.assertRaises(UserError):
            bill.with_user(self.accountant).action_ipr_approve()
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(bill.ipr_state, 'waiting')

    # SC-IPR-0014-2
    def test_creator_refused(self):
        """SC-IPR-0014-2: the accounting administrator who entered the bill cannot approve it."""
        bill = self._waiting_bill(creator=self.account_admin)
        with self.assertRaises(UserError) as caught:
            bill.with_user(self.account_admin).action_ipr_approve()
        self.assertIn('another manager must decide', str(caught.exception))
        with self.assertRaises(UserError) as caught:
            bill.with_user(self.account_admin).action_ipr_reject('Not right')
        self.assertIn('another manager must decide', str(caught.exception))
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(bill.ipr_state, 'waiting')

    # SC-IPR-0015-1
    def test_second_decision_refused(self):
        """SC-IPR-0015-1: the first decision counts; the second is refused naming the first approver and time."""
        bill = self._waiting_bill()
        bill.with_user(self.purchase_manager).action_ipr_approve()
        self.env.flush_all()
        self.env.invalidate_all()
        decided_on = bill.ipr_decided_date
        with self.assertRaises(UserError) as caught:
            bill.with_user(self.account_admin).action_ipr_reject('Too late')
        message = str(caught.exception)
        self.assertIn(self.purchase_manager.name, message)
        self.assertIn(str(decided_on.year), message)
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertEqual(bill.ipr_state, 'approved')
        self.assertEqual(bill.ipr_decided_by_id, self.purchase_manager)
        self.assertFalse(self._history(bill, 'reject'))

    def test_reset_between(self):
        """FR-IPR-0015: a bill set back to Not checked meanwhile is refused: it must be checked again."""
        bill = self._waiting_bill()
        bill.write({'ipr_state': 'not_checked'})  # superuser: stands for the bill changed meanwhile
        self.env.flush_all()
        self.env.invalidate_all()
        with self.assertRaises(UserError) as caught:
            bill.with_user(self.purchase_manager).action_ipr_approve()
        self.assertIn('must be checked again', str(caught.exception))

    # SC-IPR-0011-1
    def test_bills_to_approve_list(self):
        """SC-IPR-0011-1: a purchase manager allowed only in company A sees the two company A bills waiting."""
        now = fields.Datetime.now()
        bills_a = self._waiting_bill(ref='LIST-A1') | self._waiting_bill(ref='LIST-A2')
        bill_b = self.env['account.move'].with_company(self.company_b).create({
            'move_type': 'in_invoice', 'partner_id': self.supplier_acme.id, 'ref': 'LIST-B1',
            'invoice_date': fields.Date.today(), 'company_id': self.company_b.id,
            'invoice_line_ids': [(0, 0, {'name': 'Service', 'quantity': 1, 'price_unit': 5.0})],
        })
        bill_b.write({'ipr_state': 'waiting', 'ipr_waiting_since': now})  # superuser fixture
        self.env.flush_all()
        self.env.invalidate_all()

        action = self.env['ir.actions.act_window'].search([
            ('res_model', '=', 'account.move'), ('name', '=', 'Bills to Approve')])
        self.assertEqual(len(action), 1, 'one Bills to Approve window action expected')
        domain = safe_eval(action.domain or '[]')
        manager_env = self.env['account.move'].with_user(self.purchase_manager).with_context(
            allowed_company_ids=self.company_a.ids)
        found = manager_env.search(domain)
        self.assertEqual(found, bills_a)
        self.assertNotIn(bill_b, found)

        # the list shows supplier, reference, total, exception kinds and waiting since
        list_view = action.view_ids.filtered(lambda v: v.view_mode == 'list').view_id or action.view_id
        views = manager_env.get_views([(list_view.id or False, 'list')])
        arch = etree.fromstring(views['views']['list']['arch'])
        columns = {node.get('name') for node in arch.iter('field')}
        for name in ('partner_id', 'ref', 'ipr_exception_kinds', 'ipr_waiting_since'):
            self.assertIn(name, columns)
        self.assertTrue({'amount_total', 'amount_total_signed', 'amount_total_in_currency_signed'} & columns)
        row = found[:1]
        self.assertTrue(row.ipr_exception_kinds)
        self.assertTrue(row.ipr_waiting_since)

    def test_bills_to_approve_menus(self):
        """FR-IPR-0011: two menu entries open the list, one per approver group."""
        menus = self.env['ir.ui.menu'].search([('name', '=', 'Bills to Approve')])
        self.assertEqual(len(menus), 2)
        groups = menus.mapped('group_ids')
        self.assertEqual(
            set(groups.ids),
            {self.env.ref('purchase.group_purchase_manager').id,
             self.env.ref('account.group_account_manager').id})
        self.assertEqual(len(menus.mapped('action')), 1)

    # SC-IPR-0023-1 / SC-IPR-0023-2
    def test_form_buttons(self):
        """SC-IPR-0023-1/2: Approve and Reject are for the other manager, Run check for the accountant.

        ipr_can_decide drives the buttons; the browser rendering of the hidden buttons is left to the build.
        """
        bill = self._waiting_bill()
        for user, expected in ((self.purchase_manager, True), (self.account_admin, True),
                               (self.accountant, False), (self.purchase_user, False)):
            self.env.invalidate_all()
            self.assertEqual(
                bill.with_user(user).ipr_can_decide, expected, user.login)
        own = self._waiting_bill(creator=self.account_admin, ref='OWN-1')
        self.env.invalidate_all()
        self.assertFalse(own.with_user(self.account_admin).ipr_can_decide)
        self.assertTrue(own.with_user(self.purchase_manager).ipr_can_decide)
        bill.write({'ipr_state': 'approved'})  # superuser fixture
        self.env.invalidate_all()
        self.assertFalse(bill.with_user(self.purchase_manager).ipr_can_decide)

        manager_env = self.env['account.move'].with_user(self.purchase_manager)
        arch = etree.fromstring(manager_env.get_views([(False, 'form')])['views']['form']['arch'])
        buttons = {btn.get('string'): btn for btn in arch.iter('button')}
        self.assertIn('Approve', buttons)
        self.assertIn('Reject', buttons)
        self.assertIn('ipr_can_decide', buttons['Approve'].get('invisible', ''))
        self.assertIn('ipr_can_decide', buttons['Reject'].get('invisible', ''))
        self.assertIn('ipr_can_decide', {node.get('name') for node in arch.iter('field')})

        accountant_env = self.env['account.move'].with_user(self.accountant)
        arch = etree.fromstring(accountant_env.get_views([(False, 'form')])['views']['form']['arch'])
        self.assertIn('Run check', {btn.get('string') for btn in arch.iter('button')})
