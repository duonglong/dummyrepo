from odoo import models


class PurchaseOrderLine(models.Model):
    _name = 'purchase.order.line'
    _inherit = ['record.change.line.mixin', 'purchase.order.line']
