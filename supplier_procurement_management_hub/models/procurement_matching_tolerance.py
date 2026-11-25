from odoo import fields, models


class ProcurementMatchingTolerance(models.Model):
    """Acceptable price or quantity difference by company, vendor, category and amount band.

    Requirements: BR-SPM-0014, FR-SPM-0074, FR-SPM-0108
    A difference is accepted when it is within the absolute amount or the percentage.
    """
    _name = 'procurement.matching.tolerance'
    _inherit = ['procurement.audit.mixin']
    _description = 'Matching Tolerance'
    _order = 'company_id, kind, id'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company,
                                 ondelete='cascade', index=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    partner_id = fields.Many2one('res.partner', string="Vendor", index=True)
    product_category_id = fields.Many2one('product.category', string="Product category")
    kind = fields.Selection([('price', 'Price'), ('quantity', 'Quantity')], required=True, default='price')
    amount_from = fields.Monetary(string="Bill total from", default=0.0)
    amount_to = fields.Monetary(
        string="Bill total to", default=0.0, help="Upper end of the bill total band; 0 means no upper limit.")
    percent = fields.Float(string="Percentage", default=0.0)
    absolute_amount = fields.Monetary(string="Absolute amount", default=0.0)
    active = fields.Boolean(default=True)

    _spm_audited_fields = (
        'company_id', 'partner_id', 'product_category_id', 'kind', 'amount_from', 'amount_to',
        'percent', 'absolute_amount', 'active')

    _percent_positive = models.Constraint('CHECK(percent >= 0)', 'The percentage cannot be negative.')
    _absolute_positive = models.Constraint('CHECK(absolute_amount >= 0)', 'The absolute amount cannot be negative.')
    _band_ordered = models.Constraint('CHECK(amount_to = 0 OR amount_from <= amount_to)', 'The amount band must start below its end.')
