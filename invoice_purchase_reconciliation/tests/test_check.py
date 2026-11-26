from lxml import etree

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged

from .common import IprCommon


@tagged('post_install', '-at_install')
class TestCheck(IprCommon):
    """Integration tests of the check and the bill's Reconciliation tab.

    Requirements: FR-IPR-0001, FR-IPR-0010, FR-IPR-0020, FR-IPR-0023, BR-IPR-0004
    """

    def _bill(self, qty=100, price=10.0, received=100.0):
        order = self.make_order(100, 10.0, received=received)
        return self.make_bill_from_order(order, qty, price)

    def test_create_with_status_refused(self):
        """FR-IPR-0010: a user cannot create a bill that is already Approved."""
        with self.assertRaisesRegex(UserError, 'only be changed by a check or a decision'):
            self.env['account.move'].with_user(self.accountant).with_company(self.company_a).create({
                'move_type': 'in_invoice', 'partner_id': self.supplier_acme.id,
                'ipr_state': 'approved', 'company_id': self.company_a.id})

    def test_decision_fields_write_refused(self):
        """FR-IPR-0010: the decision fields are as closed as the status."""
        bill = self._bill()
        with self.assertRaisesRegex(UserError, 'only be changed by a check or a decision'):
            bill.with_user(self.accountant).write({'ipr_decision_comment': 'ok'})

    def test_results_not_writable_by_user(self):
        """FR-IPR-0010: a user cannot create check lines, exceptions or history entries."""
        bill = self._bill()
        for model, vals in (
            ('ipr.check.line', {'move_id': bill.id, 'result': 'matched'}),
            ('ipr.exception', {'move_id': bill.id, 'kind': 'price'}),
            ('ipr.history', {'move_id': bill.id, 'event': 'check',
                             'company_id': self.company_a.id, 'user_id': self.accountant.id}),
        ):
            with self.assertRaises(AccessError):
                self.env[model].with_user(self.accountant).create(vals)

    def test_other_company_results_hidden(self):
        """NFR-IPR-0002: a user of company B does not see company A's comparison."""
        bill = self._bill(price=11.0)
        bill.with_user(self.accountant).action_ipr_check()
        user_b = self._make_user('ipr_accountant_b', self.company_b, ['account.group_account_invoice'])
        env_b = self.env(user=user_b, context={'allowed_company_ids': self.company_b.ids})
        self.assertFalse(env_b['ipr.check.line'].search([('move_id', '=', bill.id)]))
        self.assertFalse(env_b['ipr.exception'].search([('move_id', '=', bill.id)]))

    def test_credit_note_cannot_be_checked(self):
        """BR-IPR-0004: the check refuses a vendor credit note."""
        refund = self.env['account.move'].with_company(self.company_a).create({
            'move_type': 'in_refund', 'partner_id': self.supplier_acme.id,
            'invoice_date': fields.Date.today(), 'company_id': self.company_a.id,
            'invoice_line_ids': [Command.create({'name': 'Return', 'quantity': 1, 'price_unit': 5.0})],
        })
        with self.assertRaises(UserError):
            refund.with_user(self.accountant).action_ipr_check()

    def test_two_bill_lines_one_order_line(self):
        """FR-IPR-0004: two lines of one order line show the same totals; the exception is recorded once."""
        order = self.make_order(100, 10.0, received=100)
        line = order.order_line
        vals = {'product_id': line.product_id.id, 'quantity': 60, 'price_unit': 10.0,
                'purchase_line_id': line.id}
        bill = self.make_bill(order.partner_id, [vals, dict(vals)])
        bill.with_user(self.accountant).action_ipr_check()
        self.assertEqual(bill.ipr_check_line_ids.mapped('billed_qty'), [120.0, 120.0])
        self.assertEqual(len(bill.ipr_exception_ids.filtered(lambda e: e.kind == 'quantity')), 1)

    def test_exception_description_and_kinds(self):
        """FR-IPR-0003: the exception reads in words and the bill lists its kinds."""
        bill = self._bill(qty=103, price=11.0)
        bill.with_user(self.accountant).action_ipr_check()
        self.assertEqual(bill.ipr_exception_count, 2)
        self.assertEqual(bill.ipr_exception_kinds, 'Price difference, Quantity difference')
        self.assertIn('billed price', bill.ipr_exception_ids[0].description)

    def test_form_arch_hides_for_other_documents(self):
        """FR-IPR-0023: the tab, the badge and Run check are invisible unless the bill is a vendor bill; Run check only in draft."""
        arch = self.env['account.move'].get_view(
            self.env.ref('account.view_move_form').id)['arch']
        tree = etree.fromstring(arch)
        page = tree.xpath("//page[@name='ipr_reconciliation']")[0]
        self.assertIn("move_type != 'in_invoice'", page.get('invisible'))
        badge = tree.xpath("//header/field[@name='ipr_state']")[0]
        self.assertIn("move_type != 'in_invoice'", badge.get('invisible'))
        buttons = tree.xpath("//button[@name='action_ipr_check']")
        self.assertEqual(len(buttons), 2)
        for button in buttons:
            self.assertIn("move_type != 'in_invoice'", button.get('invisible'))
            self.assertIn("state != 'draft'", button.get('invisible'))

    def test_bill_list_has_state_filters(self):
        """FR-IPR-0023: the bill list shows the status column and the three filters."""
        search = self.env['account.move'].get_view(
            self.env.ref('account.view_account_bill_filter').id, 'search')['arch']
        for name in ('ipr_not_checked', 'ipr_waiting', 'ipr_rejected'):
            self.assertIn(name, search)

    @tagged('ipr_benchmark', '-standard')
    def test_check_speed(self):
        """NFR-IPR-0001: the check of a 100-line bill among 50,000 bills runs a bounded number of queries."""
        order = self.make_order(100, 10.0, received=100)
        line = order.order_line
        vals = {'product_id': line.product_id.id, 'quantity': 1, 'price_unit': 10.0,
                'purchase_line_id': line.id}
        bill = self.make_bill(order.partner_id, [dict(vals) for _i in range(100)])
        self.env.cr.execute("""
            INSERT INTO account_move (name, move_type, state, company_id, journal_id, currency_id, ref, auto_post, review_state, date, ipr_state, document_tax_mode)
            SELECT 'SPEED/' || g, 'in_invoice', 'draft', %s, %s, %s, 'SPEED-' || g, 'no', 'todo', CURRENT_DATE, 'not_checked', 'tax_excluded'
              FROM generate_series(1, 50000) g""",
            (self.company_a.id, bill.journal_id.id, bill.currency_id.id))
        self.env.flush_all()
        queries_before = self.env.cr.sql_log_count
        bill.with_user(self.accountant).action_ipr_check()
        self.env.flush_all()
        self.assertLess(self.env.cr.sql_log_count - queries_before, 150)

    def test_check_refused_for_auditor(self):
        """FR-IPR-0001: a user who may only read bills cannot run the check by RPC."""
        bill = self._bill()
        auditor = self._make_user(
            'ipr_auditor', self.company_a, ['account.group_account_readonly'])
        with self.assertRaises(AccessError):
            bill.with_user(auditor).action_ipr_check()
        self.assertEqual(bill.ipr_state, 'not_checked')
        self.assertFalse(bill.ipr_history_ids)
