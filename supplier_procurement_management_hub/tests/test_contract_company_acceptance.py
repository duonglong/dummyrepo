"""Acceptance test of T-021 (contracts), written from the specification.

Requirements: BR-SPM-0001, FR-SPM-0107

SC-SPM-0107-2: a contract of company A refuses a link from an order of company B.
The link is the order line's contract line (design: purchase.order.line
spm_contract_line_id). The form step of the line is left to the build: the
test writes the field.
"""
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase


class TestContractCompanyAcceptance(TransactionCase):
    """Requirements: FR-SPM-0107"""

    def setUp(self):
        super().setUp()
        self.env = self.env(context=dict(self.env.context, tracking_disable=True))
        self.company_a = self.env.company
        self.company_b = self.env['res.company'].create({'name': 'Company B T021'})
        self.supplier = self.env['res.partner'].create({'name': 'Acme', 'is_company': True})
        self.product = self.env['product.product'].create({'name': 'Bolt T021'})
        self.contract = self.env['procurement.contract'].create({
            'partner_id': self.supplier.id,
            'owner_id': self.env.user.id,
            'company_id': self.company_a.id,
            'currency_id': self.company_a.currency_id.id,
            'date_start': '2026-01-01',
            'date_end': '2026-12-31',
            'line_ids': [(0, 0, {'product_id': self.product.id, 'price_unit': 10.0})],
        })
        self.order_b = self.env['purchase.order'].with_company(self.company_b).create({
            'partner_id': self.supplier.id,
            'company_id': self.company_b.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id, 'product_qty': 1.0, 'price_unit': 10.0,
                'name': 'Bolt',
            })],
        })

    def test_sc_0107_2_order_of_company_b_cannot_link_to_contract_of_company_a(self):
        """SC-SPM-0107-2: a contract of company A; linking an order line of
        company B to it is refused and the link is not saved."""
        line = self.order_b.order_line
        with self.assertRaises((ValidationError, UserError)):
            line.write({'spm_contract_line_id': self.contract.line_ids.id})
            self.env.flush_all()
        self.assertFalse(line.spm_contract_line_id)
