from odoo.tests import tagged

from .common import IprCommon

MENU_XMLIDS = (
    'invoice_purchase_reconciliation.ipr_bill_to_approve_menu_account',
    'invoice_purchase_reconciliation.ipr_bill_to_approve_menu_purchase',
)


@tagged('post_install', '-at_install')
class TestMenuCounter(IprCommon):
    """Regression test for B-0304: the Bills to Approve menu entries show the waiting count.

    Requirements: FR-IPR-0011
    """

    def _waiting_bill(self, ref):
        order = self.make_order(10, 10.0, received=10)
        bill = self.make_bill_from_order(order, 10, 12.0, ref=ref)
        bill.with_user(self.accountant).action_ipr_check()
        self.assertEqual(bill.ipr_state, 'waiting')
        return bill

    def _menu_name(self, user, xmlid, company=None):
        env = self.env(user=user.id, context=dict(
            self.env.context, allowed_company_ids=(company or self.company_a).ids))
        menus = env['ir.ui.menu'].load_web_menus(False)
        return menus[self.env.ref(xmlid).id]['name']

    def test_b_0304_menu_shows_count_of_waiting_bills(self):
        """Walk state: two bills checked by the accountant and left waiting; the menus are
        loaded as each manager. The test cannot reach the rendered dropdown.
        """
        self._waiting_bill('CNT-1')
        self._waiting_bill('CNT-2')
        self.assertEqual(
            self._menu_name(self.account_admin, MENU_XMLIDS[0]), 'Bills to Approve (2)')
        self.assertEqual(
            self._menu_name(self.purchase_manager, MENU_XMLIDS[1]), 'Bills to Approve (2)')

    def test_b_0304_count_follows_the_decision(self):
        """Walk state: one waiting bill approved by a manager; the count drops to none."""
        bill = self._waiting_bill('CNT-3')
        self._waiting_bill('CNT-4')
        bill.with_user(self.account_admin).action_ipr_approve()
        self.assertEqual(
            self._menu_name(self.account_admin, MENU_XMLIDS[0]), 'Bills to Approve (1)')

    def test_b_0304_no_count_when_nothing_waits(self):
        """Walk state: no bill checked; the entry keeps its plain name."""
        self.assertEqual(
            self._menu_name(self.account_admin, MENU_XMLIDS[0]), 'Bills to Approve')

    def test_b_0304_count_ignores_other_company_bills(self):
        """Walk state: a bill waiting in company A; a manager allowed only in company B sees none."""
        self._waiting_bill('CNT-5')
        manager_b = self._make_user(
            'ipr_manager_b', self.company_b, ['account.group_account_manager'])
        self.assertEqual(self._menu_name(manager_b, MENU_XMLIDS[0], self.company_b), 'Bills to Approve')
