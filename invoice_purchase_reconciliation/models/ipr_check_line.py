from odoo import fields, models

CHECK_RESULTS = [
    ('matched', 'Matched'),
    ('not_on_order', 'Not on an order'),
    ('exception', 'Exception'),
]


class IprCheckLine(models.Model):
    _name = 'ipr.check.line'
    _description = 'Bill Reconciliation Comparison Line'
    _order = 'move_id, sequence, id'

    move_id = fields.Many2one(
        'account.move', string='Bill', required=True, ondelete='cascade', index=True, readonly=True)
    company_id = fields.Many2one(
        'res.company', related='move_id.company_id', string='Company', store=True)
    currency_id = fields.Many2one(
        'res.currency', related='move_id.currency_id', string='Currency', store=True)
    sequence = fields.Integer(string='Sequence', readonly=True)
    move_line_id = fields.Many2one(
        'account.move.line', string='Bill Line', ondelete='cascade', readonly=True)
    purchase_line_id = fields.Many2one(
        'purchase.order.line', string='Order Line', ondelete='set null', readonly=True)
    product_id = fields.Many2one(
        'product.product', string='Product', ondelete='set null', readonly=True)
    name = fields.Char(string='Description', readonly=True)
    uom_id = fields.Many2one('uom.uom', string='Unit', ondelete='set null', readonly=True)
    ordered_qty = fields.Float(string='Ordered', digits='Product Unit', readonly=True)
    received_qty = fields.Float(string='Received', digits='Product Unit', readonly=True)
    billed_qty = fields.Float(string='Billed', digits='Product Unit', readonly=True)
    qty_difference = fields.Float(string='Quantity Difference', digits='Product Unit', readonly=True)
    ordered_price = fields.Monetary(string='Ordered Price', currency_field='currency_id', readonly=True)
    billed_price = fields.Monetary(string='Billed Price', currency_field='currency_id', readonly=True)
    price_difference = fields.Monetary(
        string='Price Difference', currency_field='currency_id', readonly=True)
    result = fields.Selection(CHECK_RESULTS, string='Result', required=True, readonly=True)
    price_exception = fields.Boolean(string='Price Exception', readonly=True)
    qty_exception = fields.Boolean(string='Quantity Exception', readonly=True)
