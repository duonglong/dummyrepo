from odoo import api, fields, models

from ..services import approval, contract as contract_service


class PurchaseOrderLine(models.Model):  # sdd-allow: model-split the contract link field is built by T-021 (procurement_contract_line.py); the warning is the use task's
    """Order line with its deviation warning against the contracts.

    Requirements: FR-SPM-0069, BR-SPM-0010, FR-SPM-0052
    """
    _inherit = 'purchase.order.line'

    spm_sourcing_event_line_id = fields.Many2one(
        'procurement.sourcing.event.line', string="Sourcing event line", copy=False, ondelete='set null')
    spm_contract_warning = fields.Char(string="Contract warning", compute='_compute_spm_contract_warning')

    @api.model_create_multi
    def create(self, vals_list):
        """Link a line added to an order already confirmed to the contract line it is bought under.

        An added line is a material change of an order to approve or approved (BR-SPM-0010).

        Requirements: FR-SPM-0068, BR-SPM-0010, FR-SPM-0052
        """
        lines = super().create(vals_list)
        lines.order_id.filtered(lambda order: order.state == 'purchase')._spm_link_contract_lines()
        self._spm_review_material(lines.filtered(lambda line: not line.display_type).order_id)
        return lines

    def write(self, vals):
        """Link a line whose product changes on a confirmed order to its contract line.

        A change of product, analytic distribution or price of a line, compared with the order's
        newest approval request, is a material change of an order to approve or approved.

        Requirements: FR-SPM-0068, BR-SPM-0010, FR-SPM-0052
        """
        if 'product_id' in vals:
            vals = dict(vals, spm_contract_line_id=False)
        orders = self.order_id
        result = super().write(vals)
        if 'product_id' in vals:
            self.order_id.filtered(lambda order: order.state == 'purchase')._spm_link_contract_lines()
        self._spm_review_material(orders)
        return result

    def unlink(self):
        """A removed line is a material change of an order to approve or approved.

        Requirements: BR-SPM-0010, FR-SPM-0052
        """
        orders = self.filtered(lambda line: not line.display_type).order_id
        result = super().unlink()
        self._spm_review_material(orders)
        return result

    @api.model
    def _spm_review_material(self, orders):
        """Compare the orders with their approval after a line change and start it again when it is material.

        Inside a save of the order itself nothing is done here: the order's write reviews the whole order.
        """
        if self.env.context.get('spm_review_after_save') is not approval.REVIEW_DEFERRED:
            approval.review_changes(orders)

    @api.depends('product_id', 'product_qty', 'price_unit', 'date_planned', 'uom_id', 'order_id.partner_id',
                 'order_id.date_order', 'order_id.state', 'spm_contract_line_id')
    def _compute_spm_contract_warning(self):
        """Warn when the unit price, quantity, expected date or ceilings deviate from a contract.

        Requirements: FR-SPM-0069
        Shown for orders not yet confirmed, only to users who may read contracts; never blocks.
        """
        contract_lines = self.env['procurement.contract.line']
        open_lines = self.filtered(lambda line: line.order_id.state in ('draft', 'sent', 'to approve'))
        warnings = {}
        if open_lines and contract_lines.has_access('read'):
            warnings = contract_service.deviation_warnings(contract_lines, open_lines)
        for line in self:
            line.spm_contract_warning = warnings.get(line, False) or False


class PurchaseOrder(models.Model):  # sdd-allow: model-split the confirmation hook of the contract link belongs to the use task (T-022), purchase_order.py to supplier control
    """Order that links its lines to contract lines at confirmation and lists their warnings.

    Requirements: FR-SPM-0068, FR-SPM-0069
    """
    _inherit = 'purchase.order'

    spm_contract_warning = fields.Text(string="Contract warnings", compute='_compute_spm_contract_warning')

    @api.depends('order_line.spm_contract_warning')
    def _compute_spm_contract_warning(self):
        """List the warnings of the lines, each under its product.

        Requirements: FR-SPM-0069
        """
        for order in self:
            order.spm_contract_warning = "\n".join(
                f"{line.product_id.display_name}: {line.spm_contract_warning}"
                for line in order.order_line if line.spm_contract_warning) or False

    def button_approve(self, force=False):
        """Link the lines of the orders now confirmed to the contract line they are bought under.

        Requirements: FR-SPM-0068
        """
        result = super().button_approve(force=force)
        self.filtered(lambda order: order.state == 'purchase')._spm_link_contract_lines()
        return result

    def _spm_link_contract_lines(self):
        """Set the contract line of every unlinked goods line (the contract ending first matches)."""
        lines = self.order_line.filtered(lambda line: line.product_id and not line.spm_contract_line_id)
        # sudo: a buyer may lack the right to read contracts; the contracts are matched by company,
        # supplier and product, and only the link of the order lines is written
        matches = contract_service.find_contract_lines(self.env['procurement.contract.line'].sudo(), lines)
        for line, (own, _others) in matches.items():
            if own:
                line.write({'spm_contract_line_id': own.id})
