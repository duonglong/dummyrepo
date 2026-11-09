from odoo import models


class ProductTemplate(models.Model):
    _inherit = ['duplicate.check.mixin', 'product.template']
