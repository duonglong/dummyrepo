from odoo import models


class ResPartner(models.Model):
    _inherit = ['duplicate.check.mixin', 'res.partner']
