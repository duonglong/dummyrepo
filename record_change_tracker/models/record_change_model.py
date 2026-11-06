from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

COVERED_MODELS = ('res.partner', 'sale.order', 'purchase.order', 'product.template')


class RecordChangeModel(models.Model):
    _name = 'record.change.model'
    _description = 'Followed record type'
    _rec_name = 'name'

    model_id = fields.Many2one(
        'ir.model', string='Record type', required=True, ondelete='cascade', index=True,
        domain=[('model', 'in', COVERED_MODELS)])
    name = fields.Char(related='model_id.name', string='Name', readonly=True)
    is_followed = fields.Boolean(string='Followed', default=True)
    field_ids = fields.One2many('record.change.field', 'tracked_model_id', string='Fields')

    _model_id_uniq = models.Constraint(
        'unique (model_id)', 'A record type has only one tracker setting line.')

    @api.constrains('model_id')
    def _check_model_covered(self):
        """Requirements: BR-RCT-002, FR-RCT-007."""
        for rec in self:
            if rec.model_id.model not in COVERED_MODELS:
                raise ValidationError(_(
                    "Only contacts, sales orders, purchase orders and products can be followed."))

    @api.ondelete(at_uninstall=False)
    def _unlink_except_covered(self):
        """Requirements: FR-RCT-007. The four setting lines stay; they are only switched on or off."""
        raise UserError(_("The record types cannot be deleted; turn following off instead."))
