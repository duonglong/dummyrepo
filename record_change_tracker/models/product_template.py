from odoo import models


class ProductTemplate(models.Model):
    _name = 'product.template'
    _inherit = ['record.change.mixin', 'product.template']
