"""Shared fixtures of the invoice-matching acceptance tests (T-025, T-026).

Requirements: FR-SPM-0071, FR-SPM-0072, FR-SPM-0073, FR-SPM-0074, FR-SPM-0075

Names not fixed by the design are chosen here and recorded in the module notes:
kind values price, quantity, tax, currency, unit, duplicate; method values
two_way and three_way; exception states open, in_review, resolved, accepted,
rejected; route ``owner_role`` buyer or finance.
"""
from odoo import fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon

MODULE = 'supplier_procurement_management_hub'


class SpmMatchingCommon(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.eur = cls.env.ref('base.EUR')
        cls.usd = cls.env.ref('base.USD')
        (cls.eur | cls.usd).write({'active': True})
        groups = lambda *xmlids: [(6, 0, [cls.env.ref(x).id for x in xmlids])]  # noqa: E731
        cls.user_ann = cls._user('Ann Buyer', 'spm_ann_m5', groups(
            f'{MODULE}.group_spm_buyer', 'purchase.group_purchase_user'))
        cls.user_fin = cls._user('Fay Finance', 'spm_fay_m5', groups(
            f'{MODULE}.group_spm_finance', 'account.group_account_invoice',
            'purchase.group_purchase_user'))
        cls.user_pm = cls._user('Pam Manager', 'spm_pam_m5', groups(
            f'{MODULE}.group_spm_manager', 'purchase.group_purchase_manager'))
        cls.user_acc = cls._user('Al Accountant', 'spm_al_m5', groups(
            'account.group_account_invoice', 'purchase.group_purchase_user'))
        cls.acme = cls.env['res.partner'].create({'name': 'Acme M5', 'is_company': True})
        cls.cleanco = cls.env['res.partner'].create({'name': 'CleanCo M5', 'is_company': True})
        cls.chair = cls.env['product.product'].create({
            'name': 'Chair M5', 'type': 'consu', 'standard_price': 10.0})
        cls.company.spm_duplicate_bill_days = 7

    @classmethod
    def _user(cls, name, login, group_cmd):
        return cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': name, 'login': login, 'email': f'{login}@example.com',
            'company_id': cls.company.id, 'company_ids': [(6, 0, cls.company.ids)],
            'group_ids': group_cmd,
        })

    # configuration -------------------------------------------------------
    def _clear_config(self):
        """Remove shipped policies, tolerances and routes of the company."""
        for model in ('procurement.matching.policy', 'procurement.matching.tolerance',
                      'procurement.exception.route'):
            self.env[model].with_context(active_test=False).search(
                [('company_id', '=', self.company.id)]).unlink()

    def _policy(self, method, partner=None):
        return self.env['procurement.matching.policy'].create({
            'company_id': self.company.id, 'method': method,
            'partner_id': partner.id if partner else False,
        })

    def _tolerance(self, kind='price', percent=0.0, absolute=0.0, partner=None,
                   amount_from=0.0, amount_to=1000000.0):
        return self.env['procurement.matching.tolerance'].create({
            'company_id': self.company.id, 'kind': kind, 'percent': percent,
            'absolute_amount': absolute, 'amount_from': amount_from,
            'amount_to': amount_to, 'partner_id': partner.id if partner else False,
        })

    def _route(self, kind, owner_role='finance', requires_approval=False, owner=None):
        return self.env['procurement.exception.route'].create({
            'company_id': self.company.id, 'kind': kind, 'owner_role': owner_role,
            'owner_id': owner.id if owner else False,
            'requires_approval': requires_approval,
        })

    def _default_routes(self, approval_kinds=()):
        for kind in ('price', 'quantity'):
            self._route(kind, 'buyer', kind in approval_kinds)
        for kind in ('tax', 'currency', 'unit', 'duplicate'):
            self._route(kind, 'finance', kind in approval_kinds)

    # documents -----------------------------------------------------------
    def _order(self, partner=None, qty=100.0, price=11.5, currency=None, buyer=None, receive=None):
        partner = partner or self.acme
        order = self.env['purchase.order'].create({
            'partner_id': partner.id,
            'user_id': (buyer or self.user_ann).id,
            'currency_id': (currency or self.company.currency_id).id,
            'order_line': [(0, 0, {
                'product_id': self.chair.id, 'name': 'Chair', 'product_qty': qty,
                'price_unit': price, 'tax_ids': [(6, 0, [])],
            })],
        })
        order.button_confirm()
        if receive is not None:
            picking = order.picking_ids[:1]
            picking.move_ids.quantity = receive
            picking.move_ids.picked = True
            picking.with_context(skip_backorder=True,
                                 picking_ids_not_to_backorder=picking.ids).button_validate()
        return order

    def _bill(self, order, qty, price, currency=None, ref=None, date=None):
        line = order.order_line[:1]
        return self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': order.partner_id.id,
            'invoice_date': date or fields.Date.from_string('2026-11-10'),
            'ref': ref or False,
            'currency_id': (currency or order.currency_id).id,
            'invoice_line_ids': [(0, 0, {
                'product_id': line.product_id.id, 'name': 'Chair', 'quantity': qty,
                'price_unit': price, 'purchase_line_id': line.id, 'tax_ids': [(6, 0, [])],
            })],
        })

    def _plain_bill(self, partner, amount, ref=None, date=None, currency=None):
        """A bill with no purchase link."""
        return self.env['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': partner.id, 'ref': ref or False,
            'invoice_date': date or fields.Date.from_string('2026-11-10'),
            'currency_id': (currency or self.company.currency_id).id,
            'invoice_line_ids': [(0, 0, {
                'name': 'Service', 'quantity': 1, 'price_unit': amount,
                'account_id': self.company_data['default_account_expense'].id,
                'tax_ids': [(6, 0, [])],
            })],
        })

    def _recon(self, bill):
        self.env.flush_all()
        return self.env['procurement.reconciliation'].search([('move_id', '=', bill.id)])
