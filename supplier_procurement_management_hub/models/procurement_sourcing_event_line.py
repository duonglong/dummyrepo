from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..services import sourcing


class ProcurementSourcingEventLine(models.Model):
    """A product, quantity and unit a sourcing event asks for.

    Requirements: FR-SPM-0031
    """
    _name = 'procurement.sourcing.event.line'
    _description = 'Sourcing Event Line'
    _order = 'sequence, id'
    _check_company_auto = True

    event_id = fields.Many2one('procurement.sourcing.event', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='event_id.company_id', store=True, index=True)
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one('product.product', ondelete='restrict', check_company=True)
    name = fields.Char(
        string="Description", compute='_compute_name', store=True, readonly=False, required=True)
    quantity = fields.Float(default=1.0, digits='Product Unit', required=True)
    uom_id = fields.Many2one(
        'uom.uom', string="Unit", compute='_compute_uom_id', store=True, readonly=False, required=True)
    comparison_uom_id = fields.Many2one('uom.uom', string="Comparison unit",
        help="The unit prices are converted to when the comparison is normalized.")
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

    @api.constrains('request_line_ids', 'event_id')
    def _check_request_lines_company(self):
        """Refuse request lines that belong to a request of another company.

        Requirements: FR-SPM-0031
        """
        for line in self:
            if any(rl.request_id.company_id != line.event_id.company_id for rl in line.request_line_ids):
                raise ValidationError(_("The request lines must belong to the company of the event."))

    def write(self, vals):
        """Requirements: FR-SPM-0041: the evidence of an awarded event is not changed by a user."""
        sourcing.check_event_not_awarded(self.env, self.event_id)
        return super().write(vals)

    def unlink(self):
        """Requirements: FR-SPM-0041: the evidence of an awarded event is not deleted by a user."""
        sourcing.check_event_not_awarded(self.env, self.event_id)
        return super().unlink()
