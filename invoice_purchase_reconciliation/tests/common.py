from odoo import Command, fields
from odoo.tests import TransactionCase


class IprCommon(TransactionCase):
    """Shared fixture of the module's tests.

    Requirements: NFR-IPR-0003
    """

    @classmethod
    def _make_user(cls, login, company, group_xmlids):
        return cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': login,
            'login': login,
            'email': f'{login}@example.com',
            'company_id': company.id,
            'company_ids': [Command.set(company.ids)],
            'group_ids': [Command.set(
                [cls.env.ref(xmlid).id for xmlid in group_xmlids])],
        })

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Company = cls.env['res.company']
        cls.company_a = Company.create({'name': 'IPR Company A'})
        cls.company_b = Company.create({'name': 'IPR Company B'})
        for company in cls.company_a | cls.company_b:
            cls.env['account.chart.template'].try_loading(
                'generic_coa', company, install_demo=False)
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=cls.company_a.ids))
        cls.accountant = cls._make_user(
            'ipr_accountant', cls.company_a, ['account.group_account_invoice'])
        cls.purchase_user = cls._make_user(
            'ipr_purchase_user', cls.company_a, ['purchase.group_purchase_user'])
        cls.purchase_manager = cls._make_user(
            'ipr_purchase_manager', cls.company_a, ['purchase.group_purchase_manager'])
        cls.account_admin = cls._make_user(
            'ipr_account_admin', cls.company_a, ['account.group_account_manager'])
        Partner = cls.env['res.partner']
        cls.supplier_acme = Partner.create({'name': 'Acme', 'company_id': False})
        Partner.create({
            'name': 'Acme Contact', 'parent_id': cls.supplier_acme.id, 'type': 'contact'})
        cls.supplier_beta = Partner.create({'name': 'Beta', 'company_id': False})
        cls.product = cls.env['product.product'].with_company(cls.company_a).create({
            'name': 'Office chair',
            'is_storable': True,
            'type': 'consu',
            'purchase_method': 'purchase',
            'standard_price': 10.0,
        })

    def make_order(self, qty, price, received=0.0, partner=None):
        """Confirmed purchase order of company A with one line, partly or fully received."""
        order = self.env['purchase.order'].with_company(self.company_a).create({
            'partner_id': (partner or self.supplier_acme).id,
            'company_id': self.company_a.id,
            'order_line': [Command.create({
                'product_id': self.product.id,
                'product_qty': qty,
                'price_unit': price,
                'date_planned': fields.Datetime.now(),
            })],
        })
        order.button_confirm()
        if received:
            picking = order.picking_ids
            picking.move_ids.quantity = received
            picking.move_ids.picked = True
            picking.with_context(skip_backorder=True, skip_sms=True).button_validate()
        return order

    def make_bill(self, partner, lines, ref='BILL-001'):
        """Draft vendor bill of company A; each line is a dict of invoice line values."""
        return self.env['account.move'].with_company(self.company_a).create({
            'move_type': 'in_invoice',
            'partner_id': partner.id,
            'ref': ref,
            'invoice_date': fields.Date.today(),
            'company_id': self.company_a.id,
            'invoice_line_ids': [Command.create(vals) for vals in lines],
        })

    def make_bill_from_order(self, order, qty, price, ref='BILL-001'):
        """Draft vendor bill with one product line linked to the order's line."""
        line = order.order_line
        return self.make_bill(order.partner_id, [{
            'product_id': line.product_id.id,
            'quantity': qty,
            'price_unit': price,
            'purchase_line_id': line.id,
        }], ref=ref)
