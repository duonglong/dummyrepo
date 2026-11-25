from odoo import api, fields, models

from .procurement_matching_policy import MATCHING_METHODS


class ProcurementReconciliationLine(models.Model):
    """One bill line compared with its order line and receipts.

    Requirements: FR-SPM-0072, FR-SPM-0073, FR-SPM-0074
    Differences are signed: billed minus ordered (price) or minus the reference quantity.
    """
    _name = 'procurement.reconciliation.line'
    _description = 'Bill Reconciliation Line'
    _order = 'reconciliation_id, id'

    reconciliation_id = fields.Many2one('procurement.reconciliation', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='reconciliation_id.company_id', store=True, index=True)
    move_line_id = fields.Many2one('account.move.line', string="Bill line", required=True, ondelete='cascade', index=True)
    purchase_line_id = fields.Many2one('purchase.order.line', string="Order line")
    product_id = fields.Many2one('product.product')
    method = fields.Selection(MATCHING_METHODS, readonly=True)
    ordered_qty = fields.Float(digits='Product Unit')
    received_qty = fields.Float(digits='Product Unit')
    billed_qty = fields.Float(digits='Product Unit')
    ordered_price = fields.Monetary(currency_field='order_currency_id')
    billed_price = fields.Monetary(currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', string="Bill currency")
    order_currency_id = fields.Many2one('res.currency', string="Order currency")
    ordered_tax_ids = fields.Many2many('account.tax', 'spm_recon_line_ordered_tax_rel', 'line_id', 'tax_id')
    billed_tax_ids = fields.Many2many('account.tax', 'spm_recon_line_billed_tax_rel', 'line_id', 'tax_id')
    ordered_uom_id = fields.Many2one('uom.uom')
    billed_uom_id = fields.Many2one('uom.uom')
    price_difference = fields.Monetary(currency_field='currency_id')
    qty_difference = fields.Float(digits='Product Unit')
    amount_difference = fields.Monetary(currency_field='currency_id')
    price_within_tolerance = fields.Boolean()
    qty_within_tolerance = fields.Boolean()

    @api.depends('move_line_id')
    def _compute_display_name(self):
        """Requirements: FR-SPM-0071

        The bill line that is matched.
        """
        for line in self:
            line.display_name = line.move_line_id.display_name or line._description
