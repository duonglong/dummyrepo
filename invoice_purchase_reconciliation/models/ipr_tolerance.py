from odoo import api, fields, models
from odoo.exceptions import ValidationError


class IprTolerance(models.Model):
    _name = 'ipr.tolerance'
    _description = 'Bill Reconciliation Tolerances'
    _rec_name = 'company_id'

    company_id = fields.Many2one(
        'res.company', string='Company', required=True, ondelete='cascade',
        default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', string='Currency')
    price_tolerance_type = fields.Selection(
        [('percent', 'Percentage'), ('fixed', 'Fixed amount')],
        string='Price Tolerance Type', required=True, default='percent')
    price_tolerance_percent = fields.Float(string='Price Tolerance (%)', default=0.0)  # sdd-allow: money-float a percentage, not money (T-002)
    price_tolerance_amount = fields.Monetary(
        string='Price Tolerance Amount', currency_field='currency_id', default=0.0)
    qty_tolerance_type = fields.Selection(
        [('percent', 'Percentage'), ('fixed', 'Fixed units')],
        string='Quantity Tolerance Type', required=True, default='percent')
    qty_tolerance_value = fields.Float(string='Quantity Tolerance', default=0.0)

    _company_unique = models.Constraint(
        'UNIQUE(company_id)', 'Each company has one set of tolerances.')

    @api.constrains(
        'price_tolerance_percent', 'price_tolerance_amount', 'qty_tolerance_value')
    def _check_not_negative(self):
        """Refuse a negative tolerance.

        Requirements: FR-IPR-0008
        """
        for tolerance in self:
            values = (
                tolerance.price_tolerance_percent,
                tolerance.price_tolerance_amount,
                tolerance.qty_tolerance_value,
            )
            if any(value < 0 for value in values):
                raise ValidationError(self.env._('A tolerance cannot be negative.'))

    @api.model
    def _ipr_for_company(self, company):
        """Return the tolerances in force for a company, zero when it has none.

        Requirements: FR-IPR-0007
        """
        # sudo: the check runs for users who may only read their own company's row
        tolerance = self.sudo().search([('company_id', '=', company.id)], limit=1)
        return {
            'price_type': tolerance.price_tolerance_type or 'percent',
            'price_percent': tolerance.price_tolerance_percent,
            'price_amount': tolerance.price_tolerance_amount,
            'qty_type': tolerance.qty_tolerance_type or 'percent',
            'qty_value': tolerance.qty_tolerance_value,
            'currency': company.currency_id,
        }
