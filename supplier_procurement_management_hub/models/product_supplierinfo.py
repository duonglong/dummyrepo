from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class ProductSupplierinfo(models.Model):
    """Odoo vendor price linked to the contract that set it.

    Requirements: FR-SPM-0067
    """
    _inherit = 'product.supplierinfo'

    spm_contract_id = fields.Many2one(
        'procurement.contract', string="Contract", ondelete='set null', index='btree_not_null', copy=False)
    spm_contract_line_id = fields.Many2one(
        'procurement.contract.line', string="Contract line", ondelete='set null', copy=False)

    def _spm_refuse_link(self, vals):
        if not self.env.su and {'spm_contract_id', 'spm_contract_line_id'} & set(vals):
            raise UserError(self.env._("The contract of a vendor price is set by Update vendor prices on the contract."))

    def write(self, vals):
        """Refuse a change of the contract link outside the Update vendor prices dialog.

        Requirements: FR-SPM-0067
        """
        self._spm_refuse_link(vals)
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        """Refuse a contract link on a new vendor price outside the Update vendor prices dialog.

        Requirements: FR-SPM-0067
        """
        for vals in vals_list:
            self._spm_refuse_link(vals)
        return super().create(vals_list)

    @api.constrains('company_id', 'spm_contract_id', 'spm_contract_line_id')
    def _check_spm_contract_company(self):
        """Refuse a contract link from another company than the vendor price's (a shared price carries none).

        Requirements: FR-SPM-0067
        """
        for info in self.filtered('spm_contract_id'):
            if info.company_id != info.spm_contract_id.company_id or (
                    info.spm_contract_line_id and info.spm_contract_line_id.contract_id != info.spm_contract_id):
                raise ValidationError(self.env._("The vendor price and its contract must belong to the same company."))
