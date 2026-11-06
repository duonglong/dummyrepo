from odoo import models


class PurchaseOrder(models.Model):
    _name = 'purchase.order'
    _inherit = ['record.change.mixin', 'purchase.order']
