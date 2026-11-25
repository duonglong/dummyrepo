from odoo import api, fields, models

MATCHING_METHODS = [('two_way', 'Two-way (order and bill)'), ('three_way', 'Three-way (order, receipt and bill)')]


class ProcurementMatchingPolicy(models.Model):
    """Two-way or three-way matching of vendor bills, by vendor, category or company default.

    Requirements: FR-SPM-0071, FR-SPM-0108
    """
    _name = 'procurement.matching.policy'
    _inherit = ['procurement.audit.mixin']
    _description = 'Matching Policy'
    _order = 'company_id, id'

    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company,
                                 ondelete='cascade', index=True)
    partner_id = fields.Many2one('res.partner', string="Vendor", index=True)
    product_category_id = fields.Many2one('product.category', string="Product category")
    applies_to = fields.Selection([
        ('vendor', 'Vendor'), ('category', 'Product category'), ('company', 'Company default'),
    ], compute='_compute_applies_to', store=True)
    method = fields.Selection(MATCHING_METHODS, required=True, default='three_way')
    active = fields.Boolean(default=True)

    _spm_audited_fields = ('company_id', 'partner_id', 'product_category_id', 'method', 'active')

    @api.depends('partner_id', 'product_category_id')
    def _compute_applies_to(self):
        """Requirements: FR-SPM-0071"""
        for policy in self:
            if policy.partner_id:
                policy.applies_to = 'vendor'
            elif policy.product_category_id:
                policy.applies_to = 'category'
            else:
                policy.applies_to = 'company'
