from odoo import api, fields, models


class ProcurementSourcingEventLine(models.Model):
    """A product, quantity and unit a sourcing event asks for.

    Requirements: FR-SPM-0031
    """
    _name = 'procurement.sourcing.event.line'
    _description = 'Sourcing Event Line'
    _order = 'sequence, id'

    event_id = fields.Many2one('procurement.sourcing.event', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one('product.product', ondelete='restrict')
    name = fields.Char(
        string="Description", compute='_compute_name', store=True, readonly=False, required=True)
    quantity = fields.Float(default=1.0, digits='Product Unit', required=True)
    uom_id = fields.Many2one(
        'uom.uom', string="Unit", compute='_compute_uom_id', store=True, readonly=False, required=True)
    comparison_uom_id = fields.Many2one('uom.uom', string="Comparison unit")
    request_line_ids = fields.Many2many(
        'procurement.request.line', 'procurement_sourcing_event_line_request_line_rel',
        'event_line_id', 'request_line_id', string="Request lines")

    _quantity_positive = models.Constraint('CHECK(quantity > 0)', 'The quantity must be above zero.')

    @api.depends('product_id')
    def _compute_name(self):
        for line in self:
            if line.product_id:
                line.name = line.product_id.display_name

    @api.depends('product_id')
    def _compute_uom_id(self):
        for line in self:
            if line.product_id:
                line.uom_id = line.product_id.uom_id
