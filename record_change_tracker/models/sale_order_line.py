from odoo import models


class SaleOrderLine(models.Model):
    _name = 'sale.order.line'
    _inherit = ['record.change.line.mixin', 'sale.order.line']
