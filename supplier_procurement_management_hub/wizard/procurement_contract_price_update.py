from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError

MANAGER_GROUP = 'supplier_procurement_management_hub.group_spm_manager'
ADMIN_GROUP = 'supplier_procurement_management_hub.group_spm_admin'


class ProcurementContractPriceUpdate(models.TransientModel):
    """Preview of the vendor price lines a contract creates or changes, applied on confirmation.

    Requirements: FR-SPM-0067
    """
    _name = 'procurement.contract.price.update'
    _description = 'Update Vendor Prices from a Contract'

    contract_id = fields.Many2one('procurement.contract', required=True, ondelete='cascade')
    currency_id = fields.Many2one(related='contract_id.currency_id')
    line_ids = fields.One2many(
        'procurement.contract.price.update.line', 'wizard_id', string="Vendor prices",
        compute='_compute_line_ids', store=True, readonly=False)

    @api.depends('contract_id')
    def _compute_line_ids(self):
        """List one row per product line of the contract: a vendor price to create or to update.

        Requirements: FR-SPM-0067
        """
        for wizard in self:
            wizard.line_ids = [Command.clear()] + [
                Command.create(values) for values in wizard._spm_preview_rows()]

    def _spm_find_supplierinfo(self, line):
        """Return the vendor price line of the contract line: the linked one, else the same vendor, product, contract company and minimum quantity (a shared vendor price is not touched)."""
        contract = line.contract_id
        info = self.env['product.supplierinfo']
        linked = info.search([('spm_contract_line_id', '=', line.id)], limit=1)
        if linked:
            return linked
        return info.search([
            ('partner_id', '=', contract.partner_id.id),
            ('product_tmpl_id', '=', line.product_id.product_tmpl_id.id),
            ('product_id', 'in', [False, line.product_id.id]),
            ('company_id', '=', contract.company_id.id),
            ('min_qty', '=', line.min_qty),
        ], limit=1)

    def _spm_preview_rows(self):
        """Return the values of the preview rows of the contract (lines of a category have no product price)."""
        contract = self.contract_id
        today = fields.Date.context_today(self)
        rows = []
        for line in contract.line_ids.filtered('product_id'):
            info = self._spm_find_supplierinfo(line)
            old_price = info.currency_id._convert(info.price, contract.currency_id, contract.company_id, today) \
                if info else 0.0
            rows.append({
                'contract_line_id': line.id,
                'supplierinfo_id': info.id,
                'operation': 'update' if info else 'create',
                'old_price': old_price,
                'new_price': line.price_unit,
                'min_qty': line.min_qty,
                'delay': line.lead_time_days,
                'date_start': contract.date_start,
                'date_end': contract.date_end,
            })
        return rows

    def action_confirm(self):
        """Create or update the vendor price lines listed, linked to the contract.

        Requirements: FR-SPM-0067
        Only a Procurement manager, only for an active contract of a company they may use.
        """
        self.ensure_one()
        if not (self.env.user.has_group(MANAGER_GROUP) or self.env.user.has_group(ADMIN_GROUP)
                or self.env.user.has_group('base.group_system')):
            raise AccessError(self.env._("Only a Procurement manager updates vendor prices."))
        contract = self.contract_id
        contract.check_access('read')
        if contract.state != 'active':
            raise UserError(self.env._("Vendor prices are updated from an active contract."))
        # sudo: the vendor price link is writable by this dialog only; the manager, state and
        # contract access checks are above. Targets and values are rebuilt here from the contract:
        # the rows of the dialog are only a preview and are never trusted.
        info_model = self.env['product.supplierinfo'].sudo()
        for line in contract.line_ids.filtered('product_id'):
            values = {
                'price': line.price_unit,
                'min_qty': line.min_qty,
                'delay': line.lead_time_days,
                'date_start': contract.date_start,
                'date_end': contract.date_end,
                'discount': line.discount,
                'uom_id': line.uom_id.id,
                'currency_id': contract.currency_id.id,
                'spm_contract_id': contract.id,
                'spm_contract_line_id': line.id,
            }
            info = self.sudo()._spm_find_supplierinfo(line)  # sudo: see above, the vendor price of this contract line
            if info:
                info.write(values)
            else:
                info_model.create(dict(
                    values, partner_id=contract.partner_id.id, product_id=line.product_id.id,
                    product_tmpl_id=line.product_id.product_tmpl_id.id, company_id=contract.company_id.id))
        return {'type': 'ir.actions.act_window_close'}


class ProcurementContractPriceUpdateLine(models.TransientModel):
    """One vendor price line the contract creates or changes.

    Requirements: FR-SPM-0067
    """
    _name = 'procurement.contract.price.update.line'
    _description = 'Vendor Price Update Row'

    wizard_id = fields.Many2one('procurement.contract.price.update', required=True, ondelete='cascade')
    currency_id = fields.Many2one(related='wizard_id.currency_id')
    contract_line_id = fields.Many2one('procurement.contract.line', required=True, ondelete='cascade')
    product_id = fields.Many2one(related='contract_line_id.product_id')
    supplierinfo_id = fields.Many2one('product.supplierinfo', ondelete='set null')
    operation = fields.Selection([('create', 'Create'), ('update', 'Update')], required=True)
    old_price = fields.Monetary(string="Current price", currency_field='currency_id')
    new_price = fields.Monetary(string="Contract price", currency_field='currency_id')
    min_qty = fields.Float(string="Minimum quantity", digits='Product Unit')
    delay = fields.Integer(string="Lead time (days)")
    date_start = fields.Date(string="Valid from")
    date_end = fields.Date(string="Valid to")
