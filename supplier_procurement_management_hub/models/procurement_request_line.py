from odoo import api, fields, models
from odoo.exceptions import UserError


class ProcurementRequestLine(models.Model):
    """A needed product or description with its quantity and unit.

    Requirements: FR-SPM-0030
    """
    _name = 'procurement.request.line'
    _description = 'Purchase Request Line'
    _order = 'id'

    request_id = fields.Many2one('procurement.request', required=True, ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', ondelete='restrict')
    name = fields.Char(
        string="Description", compute='_compute_name', store=True, readonly=False, required=True)
    quantity = fields.Float(default=1.0, digits='Product Unit', required=True)
    uom_id = fields.Many2one(
        'uom.uom', string="Unit", compute='_compute_uom_id', store=True, readonly=False)

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

    @api.model_create_multi
    def create(self, vals_list):
        """Refuse new lines on a request that is no longer a draft.

        Requirements: FR-SPM-0030
        """
        lines = super().create(vals_list)
        lines._spm_check_draft()
        return lines

    def write(self, vals):
        """Refuse a change of a line of a request that is no longer a draft.

        Requirements: FR-SPM-0030
        """
        self._spm_check_draft()
        return super().write(vals)

    def unlink(self):
        self._spm_check_draft()
        return super().unlink()

    def _spm_check_draft(self):
        if self.env.su or self.env.user.has_group('supplier_procurement_management_hub.group_spm_buyer'):
            return
        if any(line.request_id.state != 'draft' for line in self):
            raise UserError(self.env._("The lines of a submitted request cannot be changed."))
