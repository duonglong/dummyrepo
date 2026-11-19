from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..services.contract import EDITABLE_STATES


class ProcurementContractLine(models.Model):
    """An agreed term of a contract: product or category, price, unit, quantities, lead time.

    Requirements: FR-SPM-0063
    """
    _name = 'procurement.contract.line'
    _description = 'Purchase Contract Line'
    _order = 'contract_id, id'
    _check_company_auto = True

    contract_id = fields.Many2one('procurement.contract', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='contract_id.company_id', store=True, index=True)
    currency_id = fields.Many2one(related='contract_id.currency_id')
    product_id = fields.Many2one('product.product', ondelete='restrict', check_company=True)
    product_category_id = fields.Many2one('product.category', string="Product category", ondelete='restrict')
    price_unit = fields.Monetary(string="Unit price", currency_field='currency_id')
    uom_id = fields.Many2one(
        'uom.uom', string="Unit", compute='_compute_uom_id', store=True, readonly=False)
    discount = fields.Float(string="Discount (%)")  # sdd-allow: money-float a percentage between 0 and 100, not an amount
    min_qty = fields.Float(string="Minimum order quantity", digits='Product Unit')
    lead_time_days = fields.Integer(string="Lead time (days)")
    ceiling_qty = fields.Float(string="Quantity ceiling", digits='Product Unit')
    purchase_line_ids = fields.One2many('purchase.order.line', 'spm_contract_line_id', string="Order lines")

    @api.depends('product_id')
    def _compute_uom_id(self):
        for line in self:
            if line.product_id:
                line.uom_id = line.product_id.uom_id

    @api.constrains('product_id', 'product_category_id')
    def _check_scope(self):
        """Requirements: FR-SPM-0063"""
        for line in self:
            if not (line.product_id or line.product_category_id):
                raise ValidationError(self.env._("A contract line needs a product or a product category."))

    @api.constrains('price_unit', 'discount', 'min_qty', 'lead_time_days', 'ceiling_qty')
    def _check_terms(self):
        """Refuse a negative price, a discount above 100 percent and negative quantities.

        Requirements: FR-SPM-0063
        """
        for line in self:
            if line.price_unit < 0:
                raise ValidationError(self.env._("The unit price cannot be negative."))
            if not 0 <= line.discount <= 100:
                raise ValidationError(self.env._("The discount must be between 0 and 100 percent."))
            if line.min_qty < 0 or line.ceiling_qty < 0 or line.lead_time_days < 0:
                raise ValidationError(self.env._("Quantities and lead times cannot be negative."))

    def _spm_check_editable(self):
        """Refuse a change of the lines of a contract that is waiting for approval or is in force."""
        if any(line.contract_id.state not in EDITABLE_STATES for line in self):
            raise UserError(self.env._("The lines of a contract can only be changed in a draft or a revision."))

    @api.model_create_multi
    def create(self, vals_list):
        """Requirements: FR-SPM-0063"""
        lines = super().create(vals_list)
        lines._spm_check_editable()
        return lines

    def _spm_check_not_ordered(self):
        """Refuse to drop or re-target a line that orders were placed from (their link and use would be lost)."""
        # sudo: a contract manager may lack the right to read purchase orders; only a count is read
        if self.env['purchase.order.line'].sudo().search_count([('spm_contract_line_id', 'in', self.ids)]):
            raise UserError(self.env._("Orders were placed from this line, it cannot be removed or given another product."))

    def write(self, vals):
        """Requirements: FR-SPM-0063, FR-SPM-0065"""
        self._spm_check_editable()
        if 'product_id' in vals or 'product_category_id' in vals:
            self.filtered(lambda line: any(
                line[name].id != vals[name] for name in ('product_id', 'product_category_id') if name in vals
            ))._spm_check_not_ordered()
        return super().write(vals)

    def unlink(self):
        """Requirements: FR-SPM-0065"""
        self._spm_check_editable()
        self._spm_check_not_ordered()
        return super().unlink()


class PurchaseOrderLine(models.Model):
    """Order line linked to the contract line it is bought under (FR-SPM-0107).

    Requirements: BR-SPM-0001, FR-SPM-0107
    """
    _inherit = 'purchase.order.line'

    spm_contract_line_id = fields.Many2one(
        'procurement.contract.line', string="Contract line", ondelete='set null', index='btree_not_null',
        copy=False)

    @api.constrains('spm_contract_line_id', 'order_id')
    def _check_spm_contract_company(self):
        """Refuse a link to a contract of another company than the order.

        Requirements: BR-SPM-0001, FR-SPM-0107
        """
        for line in self.filtered('spm_contract_line_id'):
            if line.spm_contract_line_id.company_id != line.order_id.company_id:
                raise ValidationError(self.env._("The contract belongs to another company than the order."))
